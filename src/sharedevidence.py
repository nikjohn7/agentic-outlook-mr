"""Shared-evidence gate: one passage, several sub-asset classes - keep the
call(s) the passage most compellingly supports.

Client feedback on the July deliverable (2026-10): the same evidence line was
used to support two (sometimes five) different asset classes - 367 passages,
882 rows in the master file. Some were legitimate ("South Korea and Taiwan
stand out" is two calls), many were stretches (a "smaller companies" sentence
also backing a supply-chain theme). The client's rule: this is not a hard
rule; per evidence line the MOST compelling call is kept, and a second is kept
only when the evidence supports it equally.

That is a judgment, so an LLM makes it - categorically (which row ids to keep,
one sentence why) - and deterministic code does everything else: finds the
shared passages, batches the groups, validates the answer (kept ids must be a
non-empty subset of the group; otherwise the group fails CLOSED: all rows
kept, flagged for review), drops the losers to the failures file with a
plain-language label, and notes on every survivor that it shares its evidence.

Two entry points:

- `gate_entries` - called inside `src.assemble` for every new run, on the
  survivors of cross-leaf dedup.
- the CLI (`python -m src.sharedevidence --output …`) - the same gate over an
  existing output.csv (the July master), writing a new output next to an audit
  file; the input is never edited in place.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from src import llm
from src.commentary import append_note, commentary_from_row, display_locator
from src.confidence import normalize_quote_text
from src.csvio import write_csv
from src.llm import Runner

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SELECT_EVIDENCE_PROMPT = PROJECT_ROOT / "prompts" / "select_evidence_leaf.md"

# Model matrix (checker-tier judgment; same as the checker and reconcile gate).
DEFAULT_ENGINE = "claude"
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"

REASON_WEAKER_FIT = "shared_evidence_weaker_fit"

# Two quotes count as the same passage when, normalized, they are equal or one
# contains the other and the shorter is at least this long (so a short generic
# fragment never glues unrelated calls together).
CONTAINMENT_MIN_CHARS = 60
BATCH_MAX_GROUPS = 20

_FAILED_REASON = "shared-evidence gate failed; all rows kept for review"
_NO_LLM_REASON = "shared-evidence gate skipped (--no-llm); all rows kept for review"


@dataclass(frozen=True, slots=True)
class Member:
    row_id: int
    leaf: str
    view: str
    reasoning: str
    basis: str
    asset_class: str
    category: str


@dataclass(frozen=True, slots=True)
class EvidenceGroup:
    group_id: int
    firm: str
    source_title: str
    evidence: str
    members: tuple[Member, ...]


@dataclass(frozen=True, slots=True)
class GroupVerdict:
    keep: tuple[int, ...]
    reason: str
    failed: bool = False


@dataclass(frozen=True, slots=True)
class GroupOutcome:
    """What deterministic code decided for one group after validation."""

    group: EvidenceGroup
    kept: tuple[int, ...]
    dropped: tuple[int, ...]
    reason: str
    failed: bool

    @property
    def review(self) -> bool:
        # Keeping several equally supported calls is the gate doing its job
        # (a passage that names several assets), not a judgment left open; only
        # a failed verdict leaves the ranking to a human.
        return self.failed


Judge = Callable[[list[EvidenceGroup]], dict[int, GroupVerdict]]


# --------------------------------------------------------------------------- #
# Deterministic grouping
# --------------------------------------------------------------------------- #


def normalize_evidence(text: str) -> str:
    return re.sub(r"\s+", " ", normalize_quote_text(text or "")).strip().lower()


def same_passage(a: str, b: str) -> bool:
    """Equal after normalization, or one contains the other (bounded)."""
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter) >= CONTAINMENT_MIN_CHARS and shorter in longer


def cluster_by_passage(items: list[tuple[int, str]]) -> list[list[int]]:
    """Union-find over (id, normalized evidence) pairs that share a passage.
    Returns only clusters of size >= 2, members in input order."""
    parent = {item_id: item_id for item_id, _ in items}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, (id_a, text_a) in enumerate(items):
        for id_b, text_b in items[i + 1:]:
            if same_passage(text_a, text_b):
                parent[find(id_a)] = find(id_b)
    clusters: dict[int, list[int]] = {}
    for item_id, _ in items:
        clusters.setdefault(find(item_id), []).append(item_id)
    return [members for members in clusters.values() if len(members) >= 2]


# --------------------------------------------------------------------------- #
# LLM judge (categorical, batched, fail-closed)
# --------------------------------------------------------------------------- #


def parse_select_evidence(raw_response: str) -> dict[int, GroupVerdict]:
    payload = json.loads(llm._extract_json(raw_response))
    if not isinstance(payload, dict):
        raise ValueError("shared-evidence response must be a JSON object")
    groups_raw = payload.get("groups")
    if not isinstance(groups_raw, list):
        raise ValueError("shared-evidence response must include a groups list")
    verdicts: dict[int, GroupVerdict] = {}
    for item in groups_raw:
        if not isinstance(item, dict):
            raise ValueError("each group must be a JSON object")
        group_id = item.get("group_id")
        if not isinstance(group_id, int) or isinstance(group_id, bool):
            raise ValueError("each group needs an integer group_id")
        keep = item.get("keep")
        if (
            not isinstance(keep, list)
            or not keep
            or any(not isinstance(k, int) or isinstance(k, bool) for k in keep)
        ):
            raise ValueError("keep must be a non-empty list of integer row_ids")
        reason = item.get("reason", "")
        if not isinstance(reason, str):
            raise ValueError("group reason must be a string")
        verdicts[group_id] = GroupVerdict(tuple(dict.fromkeys(keep)), reason.strip())
    return verdicts


def _group_input(group: EvidenceGroup) -> dict:
    return {
        "group_id": group.group_id,
        "firm": group.firm,
        "source_title": group.source_title,
        "evidence_quote": group.evidence,
        "candidates": [
            {
                "row_id": m.row_id,
                "sub_asset_class": m.leaf,
                "asset_class": m.asset_class,
                "category": m.category,
                "view": m.view,
                "reasoning": m.reasoning,
                "basis": m.basis,
            }
            for m in group.members
        ],
    }


def judge_groups(
    groups: list[EvidenceGroup],
    *,
    engine: str = DEFAULT_ENGINE,
    model: str | None = DEFAULT_MODEL,
    effort: str | None = DEFAULT_EFFORT,
    runner: Runner | None = None,
    use_llm: bool = True,
) -> dict[int, GroupVerdict]:
    """One batched call per BATCH_MAX_GROUPS groups. Any failure, or a missing
    verdict, degrades THAT batch/group to a failed verdict (all rows kept,
    review) - never a crash."""
    if not groups:
        return {}
    if not use_llm:
        return {g.group_id: GroupVerdict((), _NO_LLM_REASON, failed=True) for g in groups}
    verdicts: dict[int, GroupVerdict] = {}
    for start in range(0, len(groups), BATCH_MAX_GROUPS):
        batch = groups[start:start + BATCH_MAX_GROUPS]
        try:
            result = llm.call_parsed(
                SELECT_EVIDENCE_PROMPT,
                {"groups": [_group_input(g) for g in batch]},
                engine=engine,
                model=model,
                effort=effort,
                runner=runner,
                parser=parse_select_evidence,
            )
            payload: dict[int, GroupVerdict] = result.payload
        except Exception:  # noqa: BLE001 - fail closed, never crash the run
            payload = {}
        for group in batch:
            verdicts[group.group_id] = payload.get(
                group.group_id, GroupVerdict((), _FAILED_REASON, failed=True)
            )
    return verdicts


def make_judge(
    *,
    engine: str = DEFAULT_ENGINE,
    model: str | None = DEFAULT_MODEL,
    effort: str | None = DEFAULT_EFFORT,
    runner: Runner | None = None,
    use_llm: bool = True,
) -> Judge:
    def judge(groups: list[EvidenceGroup]) -> dict[int, GroupVerdict]:
        return judge_groups(
            groups, engine=engine, model=model, effort=effort, runner=runner, use_llm=use_llm
        )

    return judge


# --------------------------------------------------------------------------- #
# Deterministic application
# --------------------------------------------------------------------------- #


def apply_verdict(group: EvidenceGroup, verdict: GroupVerdict | None) -> GroupOutcome:
    """Validate and apply: kept ids must be a non-empty subset of the group's
    row ids, else fail closed (keep all, review)."""
    ids = tuple(m.row_id for m in group.members)
    if verdict is None or verdict.failed:
        reason = verdict.reason if verdict is not None else _FAILED_REASON
        return GroupOutcome(group, ids, (), reason, failed=True)
    kept = tuple(k for k in verdict.keep if k in ids)
    if not kept or len(kept) != len(verdict.keep):
        return GroupOutcome(
            group, ids, (), "shared-evidence verdict named row ids outside the group; "
            "all rows kept for review", failed=True,
        )
    dropped = tuple(i for i in ids if i not in kept)
    return GroupOutcome(group, kept, dropped, verdict.reason, failed=False)


def kept_note(outcome: GroupOutcome, row_id: int) -> str:
    """The reviewer note for a surviving row of a multi-member group."""
    others = [m.leaf for m in outcome.group.members if m.row_id != row_id]
    if outcome.failed:
        return (
            "Shared evidence: this passage also supports "
            + ", ".join(f"'{leaf}'" for leaf in others)
            + f"; the calls could not be ranked ({outcome.reason}); all kept for review."
        )
    kept_others = [m.leaf for m in outcome.group.members if m.row_id in outcome.kept and m.row_id != row_id]
    dropped = [m.leaf for m in outcome.group.members if m.row_id in outcome.dropped]
    parts = []
    if kept_others:
        parts.append(
            "Shared evidence: this passage equally supports "
            + ", ".join(f"'{leaf}'" for leaf in kept_others)
            + " (kept as separate calls)."
        )
    if dropped:
        parts.append(
            "Shared evidence: the same passage was also read as "
            + ", ".join(f"'{leaf}'" for leaf in dropped)
            + f"; this call was judged the most compelling fit ({outcome.reason})."
        )
    return " ".join(parts)


def dropped_message(outcome: GroupOutcome, row_id: int) -> str:
    kept = [m.leaf for m in outcome.group.members if m.row_id in outcome.kept]
    return (
        "the same evidence more directly supports "
        + ", ".join(f"'{leaf}'" for leaf in kept)
        + f" ({outcome.reason})"
    )


# --------------------------------------------------------------------------- #
# In-run entry point (assemble)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Entry:
    """What the gate needs to know about one candidate row, independent of
    assemble's internal types."""

    entry_id: int
    source_key: str  # the source DOCUMENT (not the group): one passage lives in one doc
    firm: str
    source_title: str
    evidence: str
    leaf: str
    view: str
    reasoning: str
    basis: str
    asset_class: str
    category: str


@dataclass
class GateResult:
    outcomes: list[GroupOutcome] = field(default_factory=list)
    kept_notes: dict[int, str] = field(default_factory=dict)  # entry_id -> note
    dropped: dict[int, str] = field(default_factory=dict)  # entry_id -> failure message
    # Survivors that must be reviewed: only those of a failed verdict. A valid
    # keep (one call, or several equally supported) is noted, not a review item.
    review_ids: set[int] = field(default_factory=set)

    @property
    def group_count(self) -> int:
        return len(self.outcomes)


def build_groups(entries: list[Entry]) -> list[EvidenceGroup]:
    by_source: dict[str, list[Entry]] = {}
    for entry in entries:
        by_source.setdefault(entry.source_key, []).append(entry)
    groups: list[EvidenceGroup] = []
    for source_entries in by_source.values():
        lookup = {e.entry_id: e for e in source_entries}
        items = [(e.entry_id, normalize_evidence(e.evidence)) for e in source_entries]
        for cluster in cluster_by_passage(items):
            members = [lookup[i] for i in cluster]
            if len({m.leaf for m in members}) < 2:
                continue  # same leaf twice is not a shared-evidence question
            first = members[0]
            groups.append(
                EvidenceGroup(
                    group_id=len(groups),
                    firm=first.firm,
                    source_title=first.source_title,
                    evidence=max((m.evidence for m in members), key=len),
                    members=tuple(
                        Member(m.entry_id, m.leaf, m.view, m.reasoning, m.basis, m.asset_class, m.category)
                        for m in members
                    ),
                )
            )
    return groups


def gate_entries(entries: list[Entry], judge: Judge) -> GateResult:
    groups = build_groups(entries)
    result = GateResult()
    if not groups:
        return result
    verdicts = judge(groups)
    for group in groups:
        outcome = apply_verdict(group, verdicts.get(group.group_id))
        result.outcomes.append(outcome)
        for row_id in outcome.kept:
            result.kept_notes[row_id] = kept_note(outcome, row_id)
            if outcome.review:
                result.review_ids.add(row_id)
        for row_id in outcome.dropped:
            result.dropped[row_id] = dropped_message(outcome, row_id)
    return result


# --------------------------------------------------------------------------- #
# Post-hoc entry point over an existing output.csv
# --------------------------------------------------------------------------- #

AUDIT_COLUMNS = (
    "group_id", "Firm", "Source", "Sub-Asset Class", "View", "action", "reason",
    "evidence",
)


def entries_from_rows(rows: list[dict[str, str]]) -> list[Entry]:
    """One Entry per output row, keyed on the row's PRIMARY segment (first
    source title + its evidence). Merged rows contribute their primary passage."""
    from src.eval import normalize_firm  # late import: eval pulls in optional deps

    entries: list[Entry] = []
    for index, row in enumerate(rows):
        commentary = commentary_from_row(row)
        if not commentary.segments:
            continue
        primary = commentary.segments[0]
        titles = [part.strip() for part in (row.get("Source") or "").split("|")]
        title = (primary.title or titles[0]).strip()
        # A firm can reuse a title across documents, so the document identity
        # also carries the primary source's URL (grouped rows join URLs by "|").
        urls = [part.strip() for part in (row.get("URL") or "").split("|")]
        position = titles.index(title) if title in titles else 0
        url = urls[position] if position < len(urls) else ""
        entries.append(
            Entry(
                entry_id=index,
                source_key=f"{normalize_firm(row.get('Firm', ''))}::{title.lower()}::{url.lower()}",
                firm=row.get("Firm", ""),
                source_title=title,
                evidence=primary.evidence,
                leaf=row.get("Sub-Asset Class", ""),
                view=row.get("View", ""),
                reasoning=primary.reasoning,
                basis=row.get("basis", ""),
                asset_class=row.get("Asset Class", ""),
                category=row.get("Asset Class Category", ""),
            )
        )
    return entries


def gate_rows(
    rows: list[dict[str, str]], judge: Judge
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], GateResult]:
    """Apply the gate to output rows. Returns (surviving rows, client failure
    rows for the dropped calls, audit rows, result). Input rows are not
    mutated; survivors are copies with notes/flags applied."""
    from src.assemble import client_failure_label

    result = gate_entries(entries_from_rows(rows), judge)
    what_happened, explanation = client_failure_label(REASON_WEAKER_FIT)
    survivors: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    audit: list[dict[str, str]] = []
    for index, row in enumerate(rows):
        if index in result.dropped:
            failures.append(
                {
                    "Firm": row.get("Firm", ""),
                    "Source": row.get("Source", ""),
                    "Sub-Asset Class": row.get("Sub-Asset Class", ""),
                    "View (proposed)": row.get("View", ""),
                    "What happened": what_happened,
                    "Explanation": explanation,
                    "Evidence / notes": result.dropped[index],
                }
            )
            continue
        out = dict(row)
        if index in result.kept_notes:
            append_note(out, result.kept_notes[index])
        if index in result.review_ids and (out.get("review_flag") or "none") == "none":
            out["review_flag"] = "review"
        survivors.append(out)
    for outcome in result.outcomes:
        for member in outcome.group.members:
            action = "kept" if member.row_id in outcome.kept else "dropped"
            if outcome.failed:
                action = "kept_needs_human"
            audit.append(
                {
                    "group_id": str(outcome.group.group_id),
                    "Firm": outcome.group.firm,
                    "Source": outcome.group.source_title,
                    "Sub-Asset Class": member.leaf,
                    "View": member.view,
                    "action": action,
                    "reason": outcome.reason,
                    "evidence": outcome.group.evidence,
                }
            )
    return survivors, failures, audit, result


def render_summary(result: GateResult, input_rows: int, output_rows: int) -> str:
    counts = Counter()
    for outcome in result.outcomes:
        if outcome.failed:
            counts["groups failed closed (all kept, review)"] += 1
        elif len(outcome.kept) > 1:
            counts["groups where several calls were kept (equally supported)"] += 1
        else:
            counts["groups reduced to one call"] += 1
    lines = [
        "# Shared-evidence gate summary",
        "",
        "One passage cited by several sub-asset classes: an LLM picked the most",
        "compelling call(s) per passage (categorical); deterministic code dropped the",
        "rest to the failures file and noted on every survivor that it shares its",
        "evidence. Only a failed verdict flags rows for review: it keeps all of them.",
        "",
        f"- input rows: {input_rows}",
        f"- output rows: {output_rows}",
        f"- shared-evidence groups: {result.group_count}",
        f"- rows dropped: {len(result.dropped)}",
    ]
    for label, count in sorted(counts.items()):
        lines.append(f"- {label}: {count}")
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.sharedevidence",
        description="Shared-evidence gate over an existing output.csv (writes new files; never edits in place).",
    )
    parser.add_argument("--output", required=True, type=Path, help="an output.csv to gate")
    parser.add_argument("--out-dir", required=True, type=Path, help="directory for the gated output + audit")
    parser.add_argument("--engine", choices=("claude", "codex"), default=DEFAULT_ENGINE)
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"model (default {DEFAULT_MODEL})")
    parser.add_argument("--effort", default=DEFAULT_EFFORT, help=f"reasoning effort (default {DEFAULT_EFFORT})")
    parser.add_argument("--no-llm", action="store_true", help="group only; every group fails closed (all kept, review)")
    parser.add_argument("--dry-run", action="store_true", help="report the groups that would be judged and stop")
    return parser


def main(argv: list[str] | None = None) -> int:
    from src.assemble import CLIENT_FAILURE_COLUMNS, OUTPUT_COLUMNS
    from src.csvio import read_csv

    args = build_parser().parse_args(argv)
    columns, rows = read_csv(args.output)
    if args.dry_run:
        groups = build_groups(entries_from_rows(rows))
        rows_involved = sum(len(g.members) for g in groups)
        print(f"{len(groups)} shared-evidence groups over {rows_involved} rows (of {len(rows)})")
        print(f"{-(-len(groups) // BATCH_MAX_GROUPS)} LLM calls at {BATCH_MAX_GROUPS} groups per call")
        return 0
    judge = make_judge(engine=args.engine, model=args.model, effort=args.effort, use_llm=not args.no_llm)
    survivors, failures, audit, result = gate_rows(rows, judge)
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_columns = tuple(columns) if columns else OUTPUT_COLUMNS
    write_csv(out_dir / "output.csv", out_columns, survivors)
    write_csv(out_dir / "shared-evidence-failures-client.csv", CLIENT_FAILURE_COLUMNS, failures)
    write_csv(out_dir / "shared-evidence-audit.csv", AUDIT_COLUMNS, audit)
    summary = render_summary(result, len(rows), len(survivors))
    (out_dir / "shared-evidence-summary.md").write_text(summary, encoding="utf-8")
    from src.export import write_client_files

    # Client twin of the gated output; the gate's failure rows stay in their own
    # file until folded into the batch's failures-client.csv via src.export.
    write_client_files(out_dir, survivors)
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
