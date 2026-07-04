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
from src.llm import (
    CODEX_MODEL,
    ENGINE_CONFIGS,
    LLMParseError,
    call as llm_call,
    call_parsed,
    parse_arbitration,
    parse_verdicts,
)
from src.schemas import CandidateCall, CheckVerdict, SourceInfo
from src.taxonomy import load_taxonomy


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANALYZE_PROMPT = PROJECT_ROOT / "prompts" / "analyze_chunk.md"
BRAIN_PROMPT = PROJECT_ROOT / "prompts" / "brain.md"
CHECK_PROMPT = PROJECT_ROOT / "prompts" / "check_candidates.md"
ARBITER_PROMPT = PROJECT_ROOT / "prompts" / "arbitrate_conflict.md"

# The chunk call can fail two ways worth catching per-chunk (so one bad chunk
# never sinks the whole run): unparseable/contract-breaking output after the
# repair retries, and a non-zero engine exit.
CHUNK_CALL_ERRORS = (LLMParseError, RuntimeError)


def run_pipeline(
    *,
    sources: list,
    run_id: str,
    engine: str,
    model: str | None = None,
    effort: str | None = None,
    checker_engine: str = "codex",
    checker_model: str | None = None,
    checker_effort: str = "high",
    arbiter_engine: str = "codex",
    arbiter_model: str | None = None,
    arbiter_effort: str = "medium",
    runner=None,
    analyze_prompt: str | Path = ANALYZE_PROMPT,
    brain_text: str | None = None,
):
    """Ingest each source, analyze every chunk, then check, score, and assemble.

    Three LLM steps, each with its own engine/model/effort: analyze (the
    extraction workhorse), checker (second-reader verdicts per source batch),
    and arbiter (conflict resolution, only called when views collide).
    """
    enforce_source_limit(sources)
    if checker_engine == "codex" and checker_model is None:
        checker_model = CODEX_MODEL
    if arbiter_engine == "codex" and arbiter_model is None:
        arbiter_model = CODEX_MODEL
    taxonomy = load_taxonomy()
    taxonomy_block = taxonomy.grouped_block()
    brain = brain_text if brain_text is not None else _brain_text()
    work_dir = Path("work") / run_id

    all_candidates: list[CandidateCall] = []
    verdicts: dict[int, CheckVerdict] = {}
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
            model=model,
            effort=effort,
            runner=runner,
            analyze_prompt=analyze_prompt,
        )
        offset = len(all_candidates)
        all_candidates.extend(candidates)
        chunk_failures.extend(failures)
        if candidates:
            source_verdicts, checker_failure = _check_candidates(
                source,
                candidates,
                engine=checker_engine,
                model=checker_model,
                effort=checker_effort,
                runner=runner,
            )
            verdicts.update(
                {offset + local_index: verdict for local_index, verdict in source_verdicts.items()}
            )
            if checker_failure is not None:
                chunk_failures.append(checker_failure)
        source_summaries.append(
            {
                "source_id": source.source_id,
                "source_type": source.source_type,
                "page_count": ingested.page_count,
                "chunk_count": len(ingested.chunks),
                "candidates": len(candidates),
                "visual_heavy": ingested.visual_heavy,
                "printed_pdf": ingested.printed_pdf,
            }
        )

    arbiter = _make_arbiter(
        brain,
        engine=arbiter_engine,
        model=arbiter_model,
        effort=arbiter_effort,
        runner=runner,
    )
    result = assemble_candidates(
        all_candidates,
        sources=source_infos,
        taxonomy=taxonomy,
        snapshots=snapshots,
        page_counts=page_counts,
        verdicts=verdicts,
        arbiter=arbiter,
    )
    run_dir = Path("runs") / run_id
    write_run_outputs(
        result,
        run_dir,
        source_summaries=source_summaries,
        chunk_failures=chunk_failures,
        run_config={
            "engine": engine,
            "model": model,
            "effort": effort,
            "checker": f"{checker_engine}/{checker_model}/{checker_effort}",
            "arbiter": f"{arbiter_engine}/{arbiter_model}/{arbiter_effort}",
        },
    )
    return result, chunk_failures, run_dir


def analyze_source(
    ingested: IngestedSource,
    work_dir: str | Path,
    *,
    taxonomy_block: str,
    brain_text: str,
    engine: str,
    model: str | None = None,
    effort: str | None = None,
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
                model=model,
                effort=effort,
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


def _check_candidates(
    source,
    candidates: list[CandidateCall],
    *,
    engine: str,
    model: str | None,
    effort: str | None,
    runner=None,
) -> tuple[dict[int, CheckVerdict], FailureRecord | None]:
    """One second-reader call for all of a source's candidates.

    Returns {local candidate index: verdict}. A failed checker call returns no
    verdicts plus a failure record — candidates then proceed capped and
    flagged for review, never silently promoted.
    """
    inputs = {
        "source_id": source.source_id,
        "firm": source.firm,
        "source_title": source.source,
        "candidates": [
            {
                "index": index,
                "sub_asset_raw": candidate.sub_asset_raw,
                "sub_asset_class": candidate.sub_asset_class,
                "view": candidate.view,
                "call_language": candidate.call_language,
                "evidence_kind": candidate.evidence_kind,
                "evidence_quote": candidate.evidence_quote,
                "locator": candidate.locator,
                "reasoning": candidate.reasoning,
            }
            for index, candidate in enumerate(candidates)
        ],
    }
    try:
        result = call_parsed(
            CHECK_PROMPT,
            inputs,
            engine=engine,
            model=model,
            effort=effort,
            runner=runner,
            parser=parse_verdicts,
        )
    except CHUNK_CALL_ERRORS as exc:
        failure = FailureRecord.from_chunk(
            "checker_error", str(exc)[:300], source.source_id, "checker"
        )
        return {}, failure
    verdict_map = {
        verdict.index: verdict
        for verdict in result.payload
        if 0 <= verdict.index < len(candidates)
    }
    return verdict_map, None


def _make_arbiter(
    brain_text: str,
    *,
    engine: str,
    model: str | None,
    effort: str | None,
    runner=None,
):
    """Build the conflict arbiter callable handed to assemble_candidates.

    The arbiter sees the conflicting candidates' evidence (not their
    deterministic confidence, which must not anchor it) plus the brain
    conventions, and must name a winner or return null (-> unresolved).
    """

    def arbiter(group) -> tuple[int | None, str]:
        first = group[0].candidate
        inputs = {
            "source_id": first.source_id,
            "sub_asset_class": first.sub_asset_class,
            "candidates": [
                {
                    "index": index,
                    "view": item.candidate.view,
                    "call_language": item.candidate.call_language,
                    "evidence_kind": item.candidate.evidence_kind,
                    "evidence_quote": item.candidate.evidence_quote,
                    "locator": item.candidate.locator,
                    "reasoning": item.candidate.reasoning,
                }
                for index, item in enumerate(group)
            ],
        }
        try:
            result = call_parsed(
                ARBITER_PROMPT,
                inputs,
                engine=engine,
                model=model,
                effort=effort,
                runner=runner,
                template_vars={"brain_examples": brain_text},
                parser=parse_arbitration,
            )
        except CHUNK_CALL_ERRORS as exc:
            return None, f"arbiter error: {str(exc)[:200]}"
        return result.payload

    return arbiter


def _chunk_content(ingested: IngestedSource, chunk: Chunk) -> str:
    """Render the chunk the model must read: native PDF pages, or HTML text."""
    if ingested.source.source_type == "pdf" or ingested.printed_pdf:
        pages = chunk.locator.replace("p.", "")
        capture_note = (
            f"(This PDF is a print-to-PDF capture of the web page at "
            f"{ingested.source.resolved_url}; use its page numbers as locators.)\n\n"
            if ingested.printed_pdf
            else ""
        )
        return (
            f"This chunk is pages {pages} of the PDF file at:\n"
            f"`{chunk.source_path}`\n\n"
            f"{capture_note}"
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


def resolve_engine_settings(engine: str, model: str | None, effort: str | None) -> tuple[str, str]:
    """Validate and resolve the per-run model/effort so every run states them.

    claude: model is required (alias like ``fable``/``opus``/``sonnet`` or a
    full name) so a run never silently inherits the CLI's settings default.
    codex: model is pinned to CODEX_MODEL; passing anything else is an error.
    effort is required for both engines and must be a level the engine accepts.
    """
    if engine == "codex":
        if model not in (None, CODEX_MODEL):
            raise ValueError(f"--engine codex is pinned to {CODEX_MODEL}; drop --model")
        model = CODEX_MODEL
    elif not model:
        raise ValueError("--model is required with --engine claude (e.g. fable, opus, sonnet)")

    efforts = ENGINE_CONFIGS[engine].efforts
    if not effort:
        raise ValueError(f"--effort is required; {engine} accepts: {', '.join(efforts)}")
    if effort not in efforts:
        raise ValueError(f"unknown {engine} effort {effort!r}; accepts: {', '.join(efforts)}")
    return model, effort


def main() -> int:
    parser = argparse.ArgumentParser(description="Markets Recon POC runner")
    parser.add_argument("--sources", choices=("pilot", "target"), default="pilot")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--engine", choices=("claude", "codex"), default="claude")
    parser.add_argument(
        "--model",
        help="model for the run: required with --engine claude; pinned to "
        f"{CODEX_MODEL} with --engine codex (omit it)",
    )
    parser.add_argument(
        "--effort",
        help="reasoning effort for the run (required): claude low|medium|high|xhigh|max; "
        "codex minimal|low|medium|high|xhigh",
    )
    parser.add_argument(
        "--checker-engine",
        choices=("claude", "codex"),
        default="codex",
        help="engine for the second-reader checker step (default codex)",
    )
    parser.add_argument(
        "--checker-model",
        help="model for the checker step (required with --checker-engine claude)",
    )
    parser.add_argument(
        "--checker-effort",
        default="high",
        help="reasoning effort for the checker step (default high)",
    )
    parser.add_argument(
        "--arbiter-engine",
        choices=("claude", "codex"),
        default="codex",
        help="engine for the conflict-arbiter step (default codex)",
    )
    parser.add_argument(
        "--arbiter-model",
        help="model for the arbiter step (required with --arbiter-engine claude)",
    )
    parser.add_argument(
        "--arbiter-effort",
        default="medium",
        help="reasoning effort for the arbiter step (default medium)",
    )
    parser.add_argument("--ingest-only", action="store_true")
    args = parser.parse_args()

    sources = load_pilot_sources() if args.sources == "pilot" else load_target_sources()
    enforce_source_limit(sources)

    if args.ingest_only:
        work_dir = Path("work") / args.run_id
        for source in sources:
            create_snapshot(source, work_dir)
        return 0

    try:
        model, effort = resolve_engine_settings(args.engine, args.model, args.effort)
        checker_model, checker_effort = resolve_engine_settings(
            args.checker_engine, args.checker_model, args.checker_effort
        )
        arbiter_model, arbiter_effort = resolve_engine_settings(
            args.arbiter_engine, args.arbiter_model, args.arbiter_effort
        )
    except ValueError as exc:
        parser.error(str(exc))

    print(
        f"run {args.run_id}: engine={args.engine} model={model} effort={effort} | "
        f"checker={args.checker_engine}/{checker_model}/{checker_effort} | "
        f"arbiter={args.arbiter_engine}/{arbiter_model}/{arbiter_effort}"
    )
    result, chunk_failures, run_dir = run_pipeline(
        sources=sources,
        run_id=args.run_id,
        engine=args.engine,
        model=model,
        effort=effort,
        checker_engine=args.checker_engine,
        checker_model=checker_model,
        checker_effort=checker_effort,
        arbiter_engine=args.arbiter_engine,
        arbiter_model=arbiter_model,
        arbiter_effort=arbiter_effort,
    )
    kept = len(result.output_rows)
    failed = len(result.failures) + len(chunk_failures)
    print(f"run {args.run_id}: {kept} calls kept, {failed} failed -> {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
