"""Pre-run companion scout: propose read-together groups among same-firm sources.

Metadata only. The scout reads a source CSV's firm / title / date via the
existing ingest loader (`ingest.load_pilot_sources`, header aliases accepted)
and asks one light LLM whether any same-firm sources are clear companions that
should be read as one combined source. It never fetches a URL or reads a
document — document-level linking is a later (v1.2) concern.

Its `--out` file is a `--group-notes`-compatible notes file: one analyst-style
line per accepted group, phrased the way `prompts/resolve_groups.md` expects,
so the run's group resolver can map each line back to source ids without
hand-editing. Its `--report` sidecar records the per-firm reasoning (grouped
and left-independent) plus any guard warnings, for human review before the run.

Conservatism is enforced twice: the prompt requires a clear companion signal
(same series + same period, an explicit multi-part title, a monthly+quarterly
of the same franchise over the same window) and treats "same firm" alone as
never a reason to group; the deterministic guards here then drop unknown ids,
overlapping memberships, cross-firm merges, and groups smaller than two, each
as a warning rather than a crash. A failed LLM call degrades to an empty notes
file plus a report note.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from src.ingest import SourceRecord, load_pilot_sources
from src.llm import LLMParseError, _extract_json, call_parsed
from src.run import resolve_engine_settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCOUT_PROMPT = PROJECT_ROOT / "prompts" / "scout_groups.md"

# Metadata triage over same-firm titles: a fast codex 5.6 model at medium effort
# (model revamp 2026-07-10; the earlier claude/haiku/low default was never
# user-approved and is removed). Overridable via the CLI flags.
DEFAULT_ENGINE = "codex"
DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_EFFORT = "medium"

# Same failure modes worth catching as the run pipeline (unparseable/contract-
# breaking output after repair retries, or a non-zero engine exit): the scout
# degrades to an empty notes file, never a crash.
SCOUT_CALL_ERRORS = (LLMParseError, RuntimeError)


@dataclass(frozen=True, slots=True)
class ScoutOutcome:
    notes_text: str
    report_text: str
    accepted_groups: list[dict]
    warnings: list[str]
    multi_source_firm_count: int
    llm_invoked: bool


def parse_scout_groups(raw_response: str) -> tuple[list[dict], list[dict]]:
    """Parse the scout's response into (groups, ungrouped_firms).

    Mirrors the strictness of ``llm.parse_groups``: a group needs a
    ``source_ids`` list of at least two non-empty strings plus string
    ``firm``/``reason`` fields; ``ungrouped_firms`` is a list of
    ``{firm, reason}`` objects. Anything malformed raises so the repair-retry
    loop in ``call_parsed`` can trigger.
    """
    payload = json.loads(_extract_json(raw_response))
    if not isinstance(payload, dict):
        raise ValueError("scout response must be a JSON object")

    groups_raw = payload.get("groups")
    if not isinstance(groups_raw, list):
        raise ValueError("scout response must include a groups list")
    groups: list[dict] = []
    for item in groups_raw:
        if not isinstance(item, dict):
            raise ValueError("each group must be a JSON object")
        source_ids = item.get("source_ids")
        if (
            not isinstance(source_ids, list)
            or len(source_ids) < 2
            or not all(isinstance(sid, str) and sid.strip() for sid in source_ids)
        ):
            raise ValueError("each group needs source_ids: at least two source-id strings")
        firm = item.get("firm", "")
        reason = item.get("reason", "")
        if not isinstance(firm, str) or not isinstance(reason, str):
            raise ValueError("group firm and reason must be strings")
        groups.append({"firm": firm, "source_ids": list(source_ids), "reason": reason})

    ungrouped_raw = payload.get("ungrouped_firms", [])
    if not isinstance(ungrouped_raw, list):
        raise ValueError("ungrouped_firms must be a list")
    ungrouped: list[dict] = []
    for item in ungrouped_raw:
        if not isinstance(item, dict):
            raise ValueError("each ungrouped_firms entry must be a JSON object")
        firm = item.get("firm", "")
        reason = item.get("reason", "")
        if not isinstance(firm, str) or not isinstance(reason, str):
            raise ValueError("ungrouped firm and reason must be strings")
        ungrouped.append({"firm": firm, "reason": reason})

    return groups, ungrouped


def multi_source_firms(sources: list[SourceRecord]) -> list[tuple[str, list[SourceRecord]]]:
    """Firms with >=2 sources, in first-appearance order (single-source dropped)."""
    by_firm: dict[str, list[SourceRecord]] = {}
    for source in sources:
        by_firm.setdefault(source.firm, []).append(source)
    return [(firm, records) for firm, records in by_firm.items() if len(records) >= 2]


def run_scout(
    sources: list[SourceRecord],
    *,
    sources_label: str = "",
    engine: str = DEFAULT_ENGINE,
    model: str | None = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    runner=None,
) -> ScoutOutcome:
    """Propose companion groups among same-firm sources (one LLM call at most).

    Returns the notes-file text, the report text, the accepted groups, and any
    guard warnings. Firms with a single source are filtered out; if none remain,
    no LLM call is made and an empty notes file (comment only) is returned.
    """
    firms = multi_source_firms(sources)
    if not firms:
        notes = _empty_notes("no firm in this source list has two or more sources")
        report = _render_report(
            sources_label, firms, accepted=[], ungrouped=[], warnings=[], llm_invoked=False
        )
        return ScoutOutcome(notes, report, [], [], 0, False)

    inputs = {
        "firms": [
            {
                "firm": firm,
                "sources": [
                    {"source_id": r.source_id, "title": r.source, "date": r.date}
                    for r in records
                ],
            }
            for firm, records in firms
        ]
    }
    try:
        result = call_parsed(
            SCOUT_PROMPT,
            inputs,
            engine=engine,
            model=model,
            effort=effort,
            runner=runner,
            parser=parse_scout_groups,
        )
    except SCOUT_CALL_ERRORS as exc:
        warning = f"scout LLM call failed; no groups proposed: {str(exc)[:200]}"
        notes = _empty_notes("the scout LLM call failed (see report)")
        report = _render_report(
            sources_label, firms, accepted=[], ungrouped=[], warnings=[warning], llm_invoked=True
        )
        return ScoutOutcome(notes, report, [], [warning], len(firms), True)

    raw_groups, ungrouped = result.payload
    record_by_id = {source.source_id: source for source in sources}
    firm_by_id = {source.source_id: source.firm for source in sources}
    known_ids = {
        record.source_id for _, records in firms for record in records
    }
    accepted, warnings = _apply_guards(raw_groups, known_ids, firm_by_id)

    accepted_with_records = [
        {
            "firm": group["firm"],
            "source_ids": group["source_ids"],
            "records": [record_by_id[sid] for sid in group["source_ids"]],
            "reason": group["reason"],
        }
        for group in accepted
    ]

    notes = _render_notes(accepted_with_records)
    report = _render_report(
        sources_label,
        firms,
        accepted=accepted_with_records,
        ungrouped=ungrouped,
        warnings=warnings,
        llm_invoked=True,
    )
    return ScoutOutcome(notes, report, accepted_with_records, warnings, len(firms), True)


def _apply_guards(
    raw_groups: list[dict],
    known_ids: set[str],
    firm_by_id: dict[str, str],
) -> tuple[list[dict], list[str]]:
    """Drop unknown ids, overlaps, cross-firm merges, and sub-pairs — with a
    warning for each — mirroring ``run._resolve_groups``."""
    warnings: list[str] = []
    accepted: list[dict] = []
    grouped: set[str] = set()
    for group in raw_groups:
        member_ids = group["source_ids"]
        reason = group.get("reason", "")
        unknown = [m for m in member_ids if m not in known_ids]
        overlap = [m for m in member_ids if m in grouped]
        members = [m for m in member_ids if m in known_ids and m not in grouped]
        if unknown:
            warnings.append(f"dropped unknown source ids {unknown} from group ({reason})")
        if overlap:
            warnings.append(f"source(s) {overlap} already grouped; dropped from group ({reason})")
        member_firms = {firm_by_id[m] for m in members}
        if len(member_firms) > 1:
            warnings.append(
                f"group spans multiple firms {sorted(member_firms)}; dropped ({reason})"
            )
            continue
        if len(members) < 2:
            warnings.append(f"group did not resolve to two known sources; ignored ({reason})")
            continue
        grouped.update(members)
        accepted.append(
            {"firm": firm_by_id[members[0]], "source_ids": members, "reason": reason}
        )
    return accepted, warnings


def _render_notes(accepted: list[dict]) -> str:
    """One analyst-style paragraph per accepted group, `--group-notes`-ready."""
    if not accepted:
        return _empty_notes("the scout proposed no companion groups")
    return "\n\n".join(_note_line(group["firm"], group["records"]) for group in accepted) + "\n"


def _note_line(firm: str, records: list[SourceRecord]) -> str:
    """Phrase one group the way `resolve_groups.md` expects analyst notes.

    Names the firm and each exact source title (in quotes) plus its date, so
    the resolver can map the line back to source ids by title/firm/date without
    guessing. The two-document phrasing matches the proven pilot/test2 notes.
    """
    quoted = [_quote_title(record) for record in records]
    if len(records) == 2:
        return (
            f"Read the {firm} {quoted[0]} along with the {firm} {quoted[1]} "
            "report when making calls — treat the two as one combined source."
        )
    joined = ", ".join(quoted[:-1]) + f", and {quoted[-1]}"
    return (
        f"Read the {firm} reports {joined} together when making calls — "
        "treat them as one combined source."
    )


def _quote_title(record: SourceRecord) -> str:
    title = f'"{record.source}"'
    return f"{title} (published {record.date})" if record.date.strip() else title


def _empty_notes(reason: str) -> str:
    return f"<!-- scout: no groups — {reason} -->\n"


def _render_report(
    sources_label: str,
    firms: list[tuple[str, list[SourceRecord]]],
    *,
    accepted: list[dict],
    ungrouped: list[dict],
    warnings: list[str],
    llm_invoked: bool,
) -> str:
    """Per-firm decisions for human review before the run (never read by the run)."""
    grouped_ids = {sid for group in accepted for sid in group["source_ids"]}
    ungrouped_reason_by_firm = {entry["firm"]: entry["reason"] for entry in ungrouped}
    lines: list[str] = ["# Companion-scout report", ""]
    lines.append(f"Source list: {sources_label or '(unnamed)'}")
    lines.append(f"Multi-source firms considered: {len(firms)}")
    lines.append(f"Groups proposed: {len(accepted)}")
    lines.append(f"LLM call made: {'yes' if llm_invoked else 'no'}")
    lines.append("")

    if accepted:
        lines.append("## Grouped")
        lines.append("")
        for index, group in enumerate(accepted, start=1):
            lines.append(f"### {group['firm']} — group-{index}")
            for record in group["records"]:
                date = record.date.strip() or "no date"
                lines.append(f"- \"{record.source}\" ({date}) [{record.source_id}]")
            lines.append(f"Reason: {group['reason'] or '(none given)'}")
            lines.append("")

    lines.append("## Left independent")
    lines.append("")
    any_independent = False
    for firm, records in firms:
        independent = [r for r in records if r.source_id not in grouped_ids]
        if not independent:
            continue
        any_independent = True
        partial = len(independent) != len(records)
        suffix = " (some of its sources were grouped)" if partial else ""
        lines.append(f"### {firm}{suffix}")
        reason = ungrouped_reason_by_firm.get(firm)
        lines.append(f"Reason: {reason or 'no clear companion signal (default: keep independent)'}")
        for record in independent:
            date = record.date.strip() or "no date"
            lines.append(f"- \"{record.source}\" ({date}) [{record.source_id}]")
        lines.append("")
    if not any_independent:
        lines.append("(none — every multi-source firm was fully grouped)")
        lines.append("")

    lines.append("## Guard warnings")
    lines.append("")
    if warnings:
        for warning in warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("(none)")
    lines.append("")
    return "\n".join(lines)


def _default_report_path(out_path: str) -> str:
    path = Path(out_path)
    return str(path.with_name(f"{path.stem}-report{path.suffix or '.md'}"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pre-run companion scout: propose read-together groups among "
        "same-firm sources from source metadata only (no URL fetch, no document read)."
    )
    parser.add_argument(
        "--sources",
        required=True,
        help="path to a source CSV (canonical firm/date/source/url columns, header "
        "aliases like Entity Name/Title/Published At/Source Link accepted)",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="path to write the `--group-notes`-compatible notes file",
    )
    parser.add_argument(
        "--report",
        help="path to write the per-firm reasoning sidecar (default: alongside --out)",
    )
    parser.add_argument("--engine", choices=("claude", "codex"), default=DEFAULT_ENGINE)
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"model (default {DEFAULT_MODEL}; codex allowlist member or claude model)",
    )
    parser.add_argument("--effort", default=DEFAULT_EFFORT)
    args = parser.parse_args()

    try:
        model, effort = resolve_engine_settings(args.engine, args.model, args.effort)
    except ValueError as exc:
        parser.error(str(exc))

    sources = load_pilot_sources(args.sources)
    outcome = run_scout(
        sources,
        sources_label=args.sources,
        engine=args.engine,
        model=model,
        effort=effort,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(outcome.notes_text, encoding="utf-8")

    report_path = Path(args.report or _default_report_path(args.out))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(outcome.report_text, encoding="utf-8")

    print(
        f"scout: {outcome.multi_source_firm_count} multi-source firm(s), "
        f"{len(outcome.accepted_groups)} group(s) proposed, "
        f"{len(outcome.warnings)} warning(s) -> {out_path}, {report_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
