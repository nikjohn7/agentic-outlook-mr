"""Run-level orchestration: ingest -> LLM analyze -> validate/score -> assemble."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from src.assemble import FailureRecord, assemble_candidates, write_run_outputs
from src.ingest import (
    IngestedSource,
    Chunk,
    SourceRecord,
    create_snapshot,
    enforce_source_limit,
    load_pilot_sources,
    load_target_sources,
)
from src.llm import LLMParseError, call as llm_call
from src.schemas import CandidateCall, SourceInfo
from src.taxonomy import load_taxonomy


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANALYZE_PROMPT = PROJECT_ROOT / "prompts" / "analyze_chunk.md"
BRAIN_PROMPT = PROJECT_ROOT / "prompts" / "brain.md"

# The chunk call can fail two ways worth catching per-chunk (so one bad chunk
# never sinks the whole run): unparseable/contract-breaking output after the
# repair retries, and a non-zero engine exit.
CHUNK_CALL_ERRORS = (LLMParseError, RuntimeError)


def run_pipeline(
    *,
    sources: list,
    run_id: str,
    engine: str,
    runner=None,
    analyze_prompt: str | Path = ANALYZE_PROMPT,
    brain_text: str | None = None,
):
    """Ingest each source, analyze every chunk, then score and assemble one run."""
    enforce_source_limit(sources)
    taxonomy = load_taxonomy()
    taxonomy_block = taxonomy.grouped_block()
    brain = brain_text if brain_text is not None else _brain_text()
    work_dir = Path("work") / run_id

    all_candidates: list[CandidateCall] = []
    chunk_failures: list[FailureRecord] = []
    snapshots: dict[tuple[str, str], str] = {}
    page_counts: dict[str, int] = {}
    source_infos: dict[str, SourceInfo] = {}
    source_summaries: list[dict[str, object]] = []

    for source in sources:
        ingested = create_snapshot(source, work_dir)
        snapshot_text = ingested.snapshot_text_path.read_text(encoding="utf-8")
        for chunk in ingested.chunks:
            snapshots[(source.source_id, chunk.chunk_id)] = snapshot_text
        if ingested.page_count is not None:
            page_counts[source.source_id] = ingested.page_count
        source_infos[source.source_id] = SourceInfo(
            source_id=source.source_id,
            firm=source.firm,
            date=source.date,
            source=source.source,
            url=source.url,
        )

        candidates, failures = analyze_source(
            ingested,
            work_dir,
            taxonomy_block=taxonomy_block,
            brain_text=brain,
            engine=engine,
            runner=runner,
            analyze_prompt=analyze_prompt,
        )
        all_candidates.extend(candidates)
        chunk_failures.extend(failures)
        source_summaries.append(
            {
                "source_id": source.source_id,
                "source_type": source.source_type,
                "page_count": ingested.page_count,
                "chunk_count": len(ingested.chunks),
                "candidates": len(candidates),
                "visual_heavy": ingested.visual_heavy,
            }
        )

    result = assemble_candidates(
        all_candidates,
        sources=source_infos,
        taxonomy=taxonomy,
        snapshots=snapshots,
        page_counts=page_counts,
    )
    run_dir = Path("runs") / run_id
    write_run_outputs(
        result,
        run_dir,
        source_summaries=source_summaries,
        chunk_failures=chunk_failures,
    )
    return result, chunk_failures, run_dir


def analyze_source(
    ingested: IngestedSource,
    work_dir: str | Path,
    *,
    taxonomy_block: str,
    brain_text: str,
    engine: str,
    runner=None,
    analyze_prompt: str | Path = ANALYZE_PROMPT,
) -> tuple[list[CandidateCall], list[FailureRecord]]:
    """Analyze every chunk of one source with rolling memory between chunks."""
    source = ingested.source
    memory_path = Path(work_dir) / source.source_id / "memory.md"
    memory_path.write_text(_memory_header(source), encoding="utf-8")

    candidates: list[CandidateCall] = []
    failures: list[FailureRecord] = []
    for chunk in ingested.chunks:
        template_vars = {
            "taxonomy": taxonomy_block,
            "brain_examples": brain_text,
            "memory": memory_path.read_text(encoding="utf-8"),
            "chunk_content": _chunk_content(ingested, chunk),
        }
        inputs = {"source_id": source.source_id, "chunk_id": chunk.chunk_id}
        try:
            call_result = llm_call(
                analyze_prompt,
                inputs,
                engine=engine,
                runner=runner,
                template_vars=template_vars,
            )
        except CHUNK_CALL_ERRORS as exc:
            reason = "json_parse_error" if isinstance(exc, LLMParseError) else "engine_error"
            failures.append(
                FailureRecord.from_chunk(reason, str(exc)[:300], source.source_id, chunk.chunk_id)
            )
            _append_memory(memory_path, chunk.chunk_id, f"({reason}; chunk skipped)", [])
            continue

        candidates.extend(call_result.candidates)
        _append_memory(memory_path, chunk.chunk_id, call_result.summary, call_result.candidates)

    return candidates, failures


def _chunk_content(ingested: IngestedSource, chunk: Chunk) -> str:
    """Render the chunk the model must read: native PDF pages, or HTML text."""
    if ingested.source.source_type == "pdf":
        pages = chunk.locator.replace("p.", "")
        return (
            f"This chunk is pages {pages} of the PDF file at:\n"
            f"`{chunk.source_path}`\n\n"
            "Open and read those pages as rendered pages — view them so you see "
            "tables, positioning/view grids, arrows, and dial gauges as printed, not "
            "only the extracted text. Cite what you actually see on the page."
        )
    text = _html_chunk_text(chunk)
    return (
        f"This chunk is the extracted text below "
        f"(locator {chunk.locator}, from {ingested.source.resolved_url}).\n"
        "If the text references a chart, figure, or infographic you cannot see, "
        "say so in your summary rather than guessing its contents.\n\n"
        "-----\n"
        f"{text}\n"
        "-----"
    )


def _html_chunk_text(chunk: Chunk) -> str:
    text = Path(chunk.source_path).read_text(encoding="utf-8")
    match = re.fullmatch(r"char:(\d+)-(\d+)", chunk.chunk_id)
    if match:
        return text[int(match.group(1)) : int(match.group(2))]
    return text


def _memory_header(source: SourceRecord) -> str:
    return f"# {source.firm} — {source.source}  ({source.source_id})\n"


def _append_memory(
    memory_path: Path,
    chunk_id: str,
    summary: str,
    candidates: list[CandidateCall],
) -> None:
    ledger = (
        "; ".join(f"{c.sub_asset_class}={c.view}[{c.locator}]" for c in candidates)
        if candidates
        else "none"
    )
    block = f"\n## Chunk {chunk_id}\nSummary: {summary}\nCandidates: {ledger}\n"
    with memory_path.open("a", encoding="utf-8") as handle:
        handle.write(block)


def _brain_text() -> str:
    if BRAIN_PROMPT.exists():
        return BRAIN_PROMPT.read_text(encoding="utf-8").strip()
    return "No calibration examples are available for this run."


def main() -> int:
    parser = argparse.ArgumentParser(description="Markets Recon POC runner")
    parser.add_argument("--sources", choices=("pilot", "target"), default="pilot")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--engine", choices=("claude", "codex"), default="claude")
    parser.add_argument("--ingest-only", action="store_true")
    args = parser.parse_args()

    sources = load_pilot_sources() if args.sources == "pilot" else load_target_sources()
    enforce_source_limit(sources)

    if args.ingest_only:
        work_dir = Path("work") / args.run_id
        for source in sources:
            create_snapshot(source, work_dir)
        return 0

    result, chunk_failures, run_dir = run_pipeline(
        sources=sources,
        run_id=args.run_id,
        engine=args.engine,
    )
    kept = len(result.output_rows)
    failed = len(result.failures) + len(chunk_failures)
    print(f"run {args.run_id}: {kept} calls kept, {failed} failed -> {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
