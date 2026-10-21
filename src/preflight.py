"""Link preflight: fetch every source in a list and flag broken / wrong links.

A standalone sweep (NOT a pipeline run) over a whole source CSV. For each row it
calls the existing ingest ``create_snapshot`` — the same deterministic fetch the
run uses (HTML fetch, visual-heavy print-to-PDF, remote-PDF download with the
`%PDF` magic guard, snapshot text, document-date extraction) — wrapped so one
failure never stops the sweep. Because it is a preflight and not a run, the
20-source-per-run cap does NOT apply and is deliberately not enforced here.

It records, per source: ok / FAILED (with the exception message), the source
type, page count or snapshot char count, the visual-heavy / printed-PDF flags,
and the document-extracted date and its provenance (which doubles as the first
at-scale validation of the document-only date policy). After the deterministic
sweep, ONE batched LLM content-sanity pass (categorical `looks_right` /
`suspect`, no scores — house rule) smell-tests that each fetch landed on the
titled document rather than a consent wall / listing page / wrong doc; a failed
call degrades every source to `unchecked`, never a crash.

Outputs to ``--out-dir``: ``preflight.csv`` (one row per source) and
``preflight-report.md`` (summary counts, the FAILED list first, then the
suspects, then a date-extraction table). Downloaded PDFs and print-captured
pages stay under ``<out-dir>/work/`` — the "PDF downloaded locally" deliverable.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path

from src.ingest import (
    IngestedSource,
    SourceRecord,
    create_snapshot,
    date_provenance,
    load_pilot_sources,
    load_target_sources,
)
from src.llm import LLMParseError, _extract_json, call_parsed
from src.run import resolve_engine_settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTENT_CHECK_PROMPT = PROJECT_ROOT / "prompts" / "preflight_content_check.md"

# The content check is a short batched triage over ~400-char snippets. Model
# revamp 2026-07-10: codex/gpt-5.6-luna at high effort (was codex/gpt-5.5/medium).
# Override with --model from the allowlist.
DEFAULT_ENGINE = "codex"
DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_EFFORT = "high"

# How much of each snapshot's opening text the content check sees per source.
SNAPSHOT_HEAD_CHARS = 400

# Same failure modes worth catching as the run pipeline: unparseable/contract-
# breaking output after repair retries, or a non-zero engine exit. The content
# check degrades to every source `unchecked`, never a crash.
CONTENT_CHECK_ERRORS = (LLMParseError, RuntimeError)


@dataclass(slots=True)
class PreflightRecord:
    source_id: str
    firm: str
    title: str
    url: str
    status: str  # "ok" or "FAILED"
    source_type: str = ""
    page_count: int | None = None
    char_count: int | None = None
    visual_heavy: bool = False
    printed_pdf: bool = False
    date: str = ""
    date_from: str = ""  # "" | "html" | "pdf_text"
    content_check: str = ""  # "" (not run) | looks_right | suspect | unchecked
    content_reason: str = ""
    error: str = ""


def load_preflight_sources(path: str | Path) -> list[SourceRecord]:
    """Load the source list, preferring the canonical target-batch loader.

    The 37-source workbook copy (`Id`/`Firm`/`Title`/`Published At`/`Source Link`)
    loads via ``load_target_sources`` (preserving its `Id`s as source ids); any
    other pilot-family CSV (header aliases accepted) falls back to
    ``load_pilot_sources``. Either way loading never applies a source cap — a
    preflight sweeps the whole list.
    """
    try:
        return load_target_sources(path)
    except KeyError:
        return load_pilot_sources(path)


def sweep(
    sources: list[SourceRecord],
    work_dir: str | Path,
    *,
    snapshotter=create_snapshot,
) -> tuple[list[PreflightRecord], dict[str, str]]:
    """Fetch every source into ``work_dir``; one failure never stops the sweep.

    Returns (records, snapshot_heads) where snapshot_heads maps an OK source's id
    to the first ``SNAPSHOT_HEAD_CHARS`` of its captured text (fed to the content
    check). ``snapshotter`` is injectable for tests; production uses
    ``create_snapshot`` unchanged (so the real fetch, and the downloaded PDFs /
    printed pages under work_dir, are the deliverable).
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    records: list[PreflightRecord] = []
    snapshot_heads: dict[str, str] = {}
    for source in sources:
        # Broad by design: a single unreachable link, timeout, non-PDF body, or
        # parser blowup must be recorded and skipped, never abort the 37-sweep.
        try:
            ingested = snapshotter(source, work_dir)
        except Exception as exc:  # noqa: BLE001 - see comment above
            records.append(
                PreflightRecord(
                    source_id=source.source_id,
                    firm=source.firm,
                    title=source.source,
                    url=source.url,
                    status="FAILED",
                    source_type=source.source_type,
                    error=f"{type(exc).__name__}: {exc}"[:300],
                )
            )
            continue
        records.append(_ok_record(source, ingested))
        head = _snapshot_head(ingested)
        if head is not None:
            snapshot_heads[source.source_id] = head
    return records, snapshot_heads


def _ok_record(source: SourceRecord, ingested: IngestedSource) -> PreflightRecord:
    date = ingested.source.date
    # Provenance mirrors ingest's own rule (document-only dates).
    date_from = date_provenance(source.source_type) if date else ""
    char_count: int | None = None
    try:
        char_count = len(ingested.snapshot_text_path.read_text(encoding="utf-8"))
    except OSError:
        char_count = None
    return PreflightRecord(
        source_id=source.source_id,
        firm=source.firm,
        title=source.source,
        url=source.url,
        status="ok",
        source_type=source.source_type,
        page_count=ingested.page_count,
        char_count=char_count,
        visual_heavy=ingested.visual_heavy,
        printed_pdf=ingested.printed_pdf,
        date=date,
        date_from=date_from,
    )


def _snapshot_head(ingested: IngestedSource) -> str | None:
    try:
        text = ingested.snapshot_text_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    return text[:SNAPSHOT_HEAD_CHARS]


def parse_content_verdicts(raw_response: str) -> list[dict]:
    """Parse the content check: {"verdicts": [{index, verdict, reason}]}.

    Each verdict needs an int ``index``, a ``verdict`` of exactly `looks_right`
    or `suspect`, and a string ``reason``. Anything malformed raises so the
    repair-retry loop in ``call_parsed`` can trigger.
    """
    payload = json.loads(_extract_json(raw_response))
    if not isinstance(payload, dict):
        raise ValueError("content-check response must be a JSON object")
    verdicts_raw = payload.get("verdicts")
    if not isinstance(verdicts_raw, list):
        raise ValueError("content-check response must include a verdicts list")
    verdicts: list[dict] = []
    for item in verdicts_raw:
        if not isinstance(item, dict):
            raise ValueError("each verdict must be a JSON object")
        index = item.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            raise ValueError("each verdict needs an integer index")
        verdict = item.get("verdict")
        if verdict not in ("looks_right", "suspect"):
            raise ValueError("verdict must be 'looks_right' or 'suspect'")
        reason = item.get("reason", "")
        if not isinstance(reason, str):
            raise ValueError("verdict reason must be a string")
        verdicts.append({"index": index, "verdict": verdict, "reason": reason})
    return verdicts


def run_content_check(
    records: list[PreflightRecord],
    snapshot_heads: dict[str, str],
    *,
    engine: str = DEFAULT_ENGINE,
    model: str | None = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    runner=None,
) -> None:
    """Set each OK record's ``content_check`` in place via one batched LLM call.

    Only OK sources are checked. A failed call marks every checked source
    `unchecked` with a note, never a crash (the only live call in the tool).
    """
    checkable = [record for record in records if record.status == "ok"]
    if not checkable:
        return
    inputs = {
        "sources": [
            {
                "index": index,
                "firm": record.firm,
                "expected_title": record.title,
                "snapshot_head": snapshot_heads.get(record.source_id, ""),
            }
            for index, record in enumerate(checkable)
        ]
    }
    try:
        result = call_parsed(
            CONTENT_CHECK_PROMPT,
            inputs,
            engine=engine,
            model=model,
            effort=effort,
            runner=runner,
            parser=parse_content_verdicts,
        )
    except CONTENT_CHECK_ERRORS as exc:
        for record in checkable:
            record.content_check = "unchecked"
            record.content_reason = f"content check unavailable: {str(exc)[:120]}"
        return
    by_index = {verdict["index"]: verdict for verdict in result.payload}
    for index, record in enumerate(checkable):
        verdict = by_index.get(index)
        if verdict is None:
            record.content_check = "unchecked"
            record.content_reason = "no verdict returned for this source"
            continue
        record.content_check = verdict["verdict"]
        record.content_reason = verdict["reason"] if verdict["verdict"] == "suspect" else ""


CSV_COLUMNS = (
    "source_id",
    "firm",
    "title",
    "url",
    "status",
    "source_type",
    "page_count",
    "char_count",
    "visual_heavy",
    "printed_pdf",
    "date",
    "date_from",
    "content_check",
    "content_reason",
    "error",
)


def write_outputs(out_dir: str | Path, records: list[PreflightRecord]) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "preflight.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for record in records:
            writer.writerow(
                [
                    record.source_id,
                    record.firm,
                    record.title,
                    record.url,
                    record.status,
                    record.source_type,
                    "" if record.page_count is None else record.page_count,
                    "" if record.char_count is None else record.char_count,
                    record.visual_heavy,
                    record.printed_pdf,
                    record.date,
                    record.date_from,
                    record.content_check,
                    record.content_reason,
                    record.error,
                ]
            )
    report_path = out_dir / "preflight-report.md"
    report_path.write_text(render_report(records), encoding="utf-8")
    return csv_path, report_path


def render_report(records: list[PreflightRecord]) -> str:
    total = len(records)
    ok = [r for r in records if r.status == "ok"]
    failed = [r for r in records if r.status == "FAILED"]
    suspects = [r for r in ok if r.content_check == "suspect"]
    unchecked = [r for r in ok if r.content_check == "unchecked"]
    pdf_ok = [r for r in ok if r.source_type == "pdf"]
    html_ok = [r for r in ok if r.source_type == "html"]
    txt_ok = [r for r in ok if r.source_type == "txt"]
    printed = [r for r in ok if r.printed_pdf]
    dated = [r for r in ok if r.date]
    from_html = [r for r in dated if r.date_from == "html"]
    from_pdf = [r for r in dated if r.date_from == "pdf_text"]
    from_txt = [r for r in dated if r.date_from == "txt_text"]
    blank_dates = [r for r in ok if not r.date]

    lines: list[str] = ["# Link preflight report", ""]
    lines.append(f"- Sources swept: {total}")
    lines.append(f"- Fetched OK: {len(ok)}")
    lines.append(f"- FAILED: {len(failed)}")
    lines.append(f"- OK by type: {len(pdf_ok)} PDF, {len(html_ok)} HTML "
                 f"({len(printed)} print-captured visual-heavy), "
                 f"{len(txt_ok)} transcript (txt)")
    lines.append(f"- Content check: {len(suspects)} suspect, "
                 f"{len(unchecked)} unchecked, {len(ok) - len(suspects) - len(unchecked)} looks_right")
    lines.append("")

    lines.append("## FAILED links")
    lines.append("")
    if failed:
        for record in failed:
            lines.append(f"- **{record.firm} — {record.title}**")
            lines.append(f"  - {record.url}")
            lines.append(f"  - {record.error}")
    else:
        lines.append("(none — every link fetched)")
    lines.append("")

    lines.append("## Suspect content (fetched, but may be the wrong page)")
    lines.append("")
    if suspects:
        for record in suspects:
            lines.append(f"- **{record.firm} — {record.title}**: {record.content_reason}")
            lines.append(f"  - {record.url}")
    else:
        lines.append("(none flagged suspect)")
    if unchecked:
        lines.append("")
        lines.append(f"_Note: {len(unchecked)} OK source(s) went `unchecked` "
                     "(content-check call unavailable)._")
    lines.append("")

    lines.append("## Date extraction")
    lines.append("")
    lines.append(f"- OK sources with a document date: {len(dated)} of {len(ok)} "
                 f"({len(from_html)} from HTML, {len(from_pdf)} from PDF text, "
                 f"{len(from_txt)} from transcript text)")
    lines.append(f"- Blank (no document date found): {len(blank_dates)}")
    lines.append("")
    lines.append("| Firm | Title | Type | Date | From |")
    lines.append("| --- | --- | --- | --- | --- |")
    for record in ok:
        lines.append(
            f"| {record.firm} | {record.title} | {record.source_type} | "
            f"{record.date or '(blank)'} | {record.date_from or '-'} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Link preflight: fetch every source in a list, flag broken or "
        "wrong-looking links (no run cap; not a pipeline run)."
    )
    parser.add_argument("--sources", required=True, help="path to the source CSV")
    parser.add_argument(
        "--out-dir",
        required=True,
        help="directory for preflight.csv, preflight-report.md, and the work/ dir "
        "holding downloaded PDFs / print-captured pages",
    )
    parser.add_argument("--engine", choices=("claude", "codex"), default=DEFAULT_ENGINE)
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"model (codex allowlist member, default {DEFAULT_MODEL})")
    parser.add_argument("--effort", default=DEFAULT_EFFORT)
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="skip the content-sanity LLM pass (deterministic sweep only)",
    )
    args = parser.parse_args()

    try:
        model, effort = resolve_engine_settings(args.engine, args.model, args.effort)
    except ValueError as exc:
        parser.error(str(exc))

    out_dir = Path(args.out_dir)
    sources = load_preflight_sources(args.sources)
    print(f"preflight: sweeping {len(sources)} source(s) from {args.sources}")
    records, snapshot_heads = sweep(sources, out_dir / "work")

    if args.no_llm:
        for record in records:
            if record.status == "ok":
                record.content_check = "unchecked"
                record.content_reason = "content check skipped (--no-llm)"
    else:
        run_content_check(
            records, snapshot_heads, engine=args.engine, model=model, effort=effort
        )

    csv_path, report_path = write_outputs(out_dir, records)
    ok = sum(1 for r in records if r.status == "ok")
    failed = sum(1 for r in records if r.status == "FAILED")
    suspects = sum(1 for r in records if r.content_check == "suspect")
    print(
        f"preflight: {ok} ok, {failed} failed, {suspects} suspect "
        f"-> {csv_path}, {report_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
