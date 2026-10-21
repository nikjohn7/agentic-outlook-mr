"""Post-run firm-reconcile stage (the client's own two-pass design).

`reconcile.py` consumes one or more frozen run `output.csv` files (in practice
the single combined 98-batch output) and writes a RECONCILED master plus a
dual-confidence audit trail. It SUPERSEDES `src/crosscheck.py` as the acting
stage — crosscheck stays untouched as a bare report tool. The join normalization
is imported from `src.eval` (never reimplemented), exactly as crosscheck does, so
"same firm + same leaf" means one thing everywhere.

    .venv/bin/python -m src.reconcile \\
        --outputs <output.csv ...> --out-dir <dir> \\
        [--engine claude --model opus --effort medium]

Pipeline (all merge/selection decisions are DETERMINISTIC code over categorical
inputs — the LLM only classifies scope; it never invents a number or picks a
winner):

1. Group every row on `src.eval`-normalized (firm, sub-asset leaf). Single-row
   keys pass through untouched.
2. Scope gate (LLM, categorical, batched): per multi-row key, are the rows the
   SAME claim or DISTINCT claims sharing a leaf (different horizon / sub-sector /
   scenario)? Verdicts `same_claim` | `distinct_claims`; any engine failure or
   unparseable verdict degrades that key to `needs_human` (crosscheck precedent).
3. Same claim, same view → deterministic merge into one surviving row
   (pipe-joined Source/URL/Date, labeled `||||`-merged commentary, max
   confidence, OR'd review flag). Merged-away members become `merged_by_reconcile`
   failure rows.
4. Same claim, conflicting views → a deterministic precedence ladder
   (recency → basis → confidence band/number → needs_human). A resolved loser
   becomes a `superseded_by_reconcile` failure row; an unresolved group keeps
   ALL its rows, each flagged for review. Never a forced call, never a majority
   vote.
5. Outputs: `output.csv` (reconciled master), `reconcile-audit.csv` (both
   confidences: original per-row + the reconcile decision), `reconcile-summary.md`
   (per-action counts, the needs_human list, scope disclaimer). The failure rows
   are exposed on the result so the combine step can fold them into the run's
   failure files.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from src import llm
from src.assemble import (
    CLIENT_FAILURE_COLUMNS,
    FAILURE_COLUMNS,
    OUTPUT_COLUMNS,
    client_failure_label,
    merge_commentaries,
)
# Reuse the ground-truth join normalization (imported, never reimplemented) so a
# reconcile key is identical to the eval harness's and to crosscheck's. The
# leaf-token helpers back the deterministic near-leaf candidate lanes (Phase 3).
from src.eval import _leaf_key, _leaf_tokens, normalize_firm, token_overlap
from src.taxonomy import Taxonomy, UnknownTaxonomyLabel, load_taxonomy

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RECONCILE_SCOPE_PROMPT = PROJECT_ROOT / "prompts" / "reconcile_scope.md"
RECONCILE_NEARLEAF_PROMPT = PROJECT_ROOT / "prompts" / "reconcile_nearleaf.md"

# Scope-gate categorical verdicts. The model returns only the first two; the
# third is the code-only fail-closed sentinel (engine failure / bad verdict).
VERDICT_SAME_CLAIM = "same_claim"
VERDICT_DISTINCT = "distinct_claims"
VERDICT_NEEDS_HUMAN = "needs_human"
VALID_SCOPE_VERDICTS = frozenset({VERDICT_SAME_CLAIM, VERDICT_DISTINCT})

# Per-row reconcile actions (audit `action` column).
ACTION_MERGED = "merged"
ACTION_KEPT_DISTINCT = "kept_distinct"
ACTION_SUPERSEDED = "superseded"
ACTION_WINNER = "winner"
ACTION_NEEDS_HUMAN = "needs_human"

# New failure reason codes emitted here (registered in src.assemble
# ALL_REASON_CODES / CLIENT_FAILURE_LABELS).
REASON_MERGED = "merged_by_reconcile"
REASON_SUPERSEDED = "superseded_by_reconcile"

# Phase 3 near-leaf reason codes (also registered in src.assemble).
REASON_NEAR_LEAF_MERGED = "near_leaf_merged"
REASON_NEAR_LEAF_SUPERSEDED = "near_leaf_superseded"

# --- Near-leaf candidate generation (deterministic, two bounded lanes) -------- #
LANE_STRUCTURAL = "structural"
LANE_SHORT_LABEL = "short_label_containment"
STRUCTURAL_MIN_OVERLAP = 0.50
SHORT_LABEL_MAX_TOKENS = 2

# Near-leaf grouping relationships the model may return.
NL_SAME_CLAIM = "same_claim"
NL_DISTINCT = "distinct"
VALID_NL_RELATIONSHIPS = frozenset({NL_SAME_CLAIM, NL_DISTINCT})

# Near-leaf per-row audit actions.
NL_ACTION_WINNER = "winner"
NL_ACTION_MERGED = "merged"
NL_ACTION_SUPERSEDED = "superseded"
NL_ACTION_KEPT = "kept"
NL_ACTION_NEEDS_HUMAN = "needs_human"

# Deterministic precedence rankings (higher wins). basis: stated beats implied,
# the existing stated-beats-implied convention.
_BAND_RANK = {"Low": 0, "Medium": 1, "High": 2}
_BASIS_RANK = {"inferred": 0, "forecast_delta": 1, "stated": 2}

Runner = Callable[[list[str], str], object]


class ReconcileError(RuntimeError):
    """A fatal problem loading the run outputs."""


# --------------------------------------------------------------------------- #
# Row loading (full output rows — reconcile rewrites whole rows, not a subset)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Row:
    raw: dict[str, str]
    firm: str
    firm_key: str
    leaf: str
    view: str
    date: str
    source_title: str
    url: str
    commentary: str
    confidence: int | None
    band: str
    review_flag: str
    basis: str
    checker_strength: str
    call_language: str
    quote_match: str
    source_file: str
    index: int


_REQUIRED_COLUMNS = frozenset({"Firm", "Sub-Asset Class", "View"})


def load_rows(paths: list[Path]) -> list[Row]:
    rows: list[Row] = []
    for path in paths:
        rows.extend(_read_rows(path))
    return rows


def _read_rows(path: Path) -> list[Row]:
    if not path.is_file():
        raise ReconcileError(f"output not found: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ReconcileError(f"CSV is empty: {path}")
        missing = _REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            raise ReconcileError(
                f"{path} missing required columns: {', '.join(sorted(missing))}"
            )
        tag = str(path)
        return [row_from_raw(raw, tag, index) for index, raw in enumerate(reader)]


def row_from_raw(raw: dict[str, str], source_file: str, index: int) -> Row:
    """Build a Row from a raw output-CSV dict. Shared by file loading and by the
    near-leaf pass, which re-parses the exact-reconciled output dicts."""
    firm = (raw.get("Firm") or "").strip()
    return Row(
        raw=dict(raw),
        firm=firm,
        firm_key=normalize_firm(firm),
        leaf=_leaf_key(raw.get("Sub-Asset Class") or ""),
        view=(raw.get("View") or "").strip(),
        date=(raw.get("Date") or "").strip(),
        source_title=(raw.get("Source") or "").strip(),
        url=(raw.get("URL") or "").strip(),
        commentary=(raw.get("Full Commentary") or "").strip(),
        confidence=_parse_int(raw.get("confidence")),
        band=(raw.get("band") or "").strip(),
        review_flag=(raw.get("review_flag") or "").strip(),
        basis=(raw.get("basis") or "").strip(),
        checker_strength=(raw.get("checker_strength") or "").strip(),
        call_language=(raw.get("call_language") or "").strip(),
        quote_match=(raw.get("quote_match") or "").strip(),
        source_file=source_file,
        index=index,
    )


def _parse_int(value: str | None) -> int | None:
    try:
        return int((value or "").strip())
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Grouping
# --------------------------------------------------------------------------- #


def group_rows(rows: list[Row]) -> dict[tuple[str, str], list[Row]]:
    """Group on (firm_key, leaf), preserving input order within each key."""
    groups: dict[tuple[str, str], list[Row]] = {}
    for row in rows:
        groups.setdefault((row.firm_key, row.leaf), []).append(row)
    return groups


def multi_row_keys(groups: dict[tuple[str, str], list[Row]]) -> list[tuple[str, str]]:
    """Multi-row keys in deterministic (firm_key, leaf) order — the scope-gate
    batch order, so a group's position (its group_id) is stable across runs."""
    return sorted(k for k, members in groups.items() if len(members) >= 2)


# --------------------------------------------------------------------------- #
# Scope gate (LLM, categorical, batched)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Verdict:
    verdict: str
    reason: str


_SCOPE_FAILED_REASON = "scope gate failed; degraded to needs_human"
_NO_LLM_REASON = "LLM scope gate skipped (--no-llm); degraded to needs_human"


def parse_reconcile_scope(raw_response: str) -> dict[int, Verdict]:
    """Parse `{"groups": [{group_id, verdict, reason}]}`. Raises on any contract
    violation so `llm.call_parsed`'s repair-retry loop can re-prompt."""
    payload = json.loads(llm._extract_json(raw_response))
    if not isinstance(payload, dict):
        raise ValueError("reconcile scope response must be a JSON object")
    groups_raw = payload.get("groups")
    if not isinstance(groups_raw, list):
        raise ValueError("reconcile scope response must include a groups list")
    verdicts: dict[int, Verdict] = {}
    for item in groups_raw:
        if not isinstance(item, dict):
            raise ValueError("each group must be a JSON object")
        group_id = item.get("group_id")
        if not isinstance(group_id, int) or isinstance(group_id, bool):
            raise ValueError("each group needs an integer group_id")
        verdict = item.get("verdict")
        if verdict not in VALID_SCOPE_VERDICTS:
            raise ValueError(
                f"verdict must be one of {', '.join(sorted(VALID_SCOPE_VERDICTS))}; got {verdict!r}"
            )
        reason = item.get("reason", "")
        if not isinstance(reason, str):
            raise ValueError("group reason must be a string")
        verdicts[group_id] = Verdict(verdict=verdict, reason=reason.strip())
    return verdicts


def scope_verdicts(
    groups: list[list[Row]],
    *,
    engine: str,
    model: str | None,
    effort: str | None,
    runner: Runner | None = None,
    use_llm: bool = True,
) -> dict[int, Verdict]:
    """One batched scope-gate pass over the multi-row groups.

    Returns {index-into-groups: Verdict}. With `use_llm=False`, or when the batched
    call fails for any reason, every group degrades to `needs_human` — never a
    crash. A verdict missing for any group also degrades to `needs_human`, so
    every group is always accounted for."""
    if not groups:
        return {}
    if not use_llm:
        return {i: Verdict(VERDICT_NEEDS_HUMAN, _NO_LLM_REASON) for i in range(len(groups))}

    inputs = {
        "groups": [
            {
                "group_id": i,
                "firm": members[0].firm,
                "sub_asset_leaf": members[0].leaf,
                "rows": [
                    {
                        "view": row.view,
                        "source_title": row.source_title,
                        "date": row.date,
                        "full_commentary": row.commentary,
                    }
                    for row in members
                ],
            }
            for i, members in enumerate(groups)
        ]
    }
    try:
        result = llm.call_parsed(
            RECONCILE_SCOPE_PROMPT,
            inputs,
            engine=engine,
            model=model,
            effort=effort,
            runner=runner,
            parser=parse_reconcile_scope,
        )
    except Exception:  # noqa: BLE001 — any failure degrades to needs_human, never a crash
        return {i: Verdict(VERDICT_NEEDS_HUMAN, _SCOPE_FAILED_REASON) for i in range(len(groups))}

    payload: dict[int, Verdict] = result.payload
    return {
        i: payload.get(i, Verdict(VERDICT_NEEDS_HUMAN, _SCOPE_FAILED_REASON))
        for i in range(len(groups))
    }


# --------------------------------------------------------------------------- #
# Deterministic merge + precedence
# --------------------------------------------------------------------------- #


def _pipe_join_dedupe(cells: list[str]) -> str:
    """Split each cell on `|`, drop blanks, dedupe exact members preserving input
    order, and re-join. Used for Source / URL / Date across merged members (a
    member cell may itself already be pipe-joined for a grouped row)."""
    seen: set[str] = set()
    out: list[str] = []
    for cell in cells:
        for part in (cell or "").split("|"):
            part = part.strip()
            if not part or part in seen:
                continue
            seen.add(part)
            out.append(part)
    return " | ".join(out)


def _or_review_flags(flags: list[str]) -> str:
    """OR review flags across members: any strong_review wins, then any review."""
    values = set(flags)
    if "strong_review" in values:
        return "strong_review"
    if "review" in values:
        return "review"
    return "none"


def _locator_from_commentary(commentary: str) -> str:
    """Best-effort locator for a merged reconcile segment: the first `Locator: X.`
    the member commentary carries (assemble writes it), minus any trailing source
    parenthetical. Empty when the commentary has no such marker."""
    match = re.search(r"Locator:\s*(.+?)\.(?:\s|$)", commentary or "")
    if not match:
        return ""
    locator = match.group(1).strip()
    locator = re.split(r"\s*\(", locator, maxsplit=1)[0].strip() or locator
    return locator


def merge_same_view(members: list[Row]) -> tuple[dict[str, str], Row, list[Row]]:
    """Merge same-claim, same-view members into ONE surviving row.

    Returns (merged_row, winner, losers). The winner (max confidence, input order
    breaks ties) supplies the deterministic per-member columns (basis,
    checker_strength, call_language, quote_match, band); Source/URL/Date are
    pipe-joined across all members; commentary is labeled and `||||`-merged;
    confidence is the max; review_flag is the OR."""
    winner = max(members, key=lambda r: (r.confidence or 0))
    merged = dict(winner.raw)
    merged["Source"] = _pipe_join_dedupe([r.source_title for r in members])
    merged["URL"] = _pipe_join_dedupe([r.url for r in members])
    merged["Date"] = _pipe_join_dedupe([r.date for r in members])
    merged["Full Commentary"] = merge_commentaries(
        [(r.source_title, _locator_from_commentary(r.commentary), r.commentary) for r in members]
    )
    merged["confidence"] = str(max((r.confidence or 0) for r in members))
    merged["band"] = winner.band
    merged["review_flag"] = _or_review_flags([r.review_flag for r in members])
    losers = [r for r in members if r is not winner]
    return merged, winner, losers


def _fmt_date(parts: tuple[int, int, int]) -> str:
    year, month, day = parts
    return f"{day:02d}/{month:02d}/{year}"


def _row_date(cell: str) -> tuple[int, int, int] | None:
    """The newest DD/MM/YYYY date in a (possibly pipe-joined) Date cell, as a
    (year, month, day) tuple for comparison; None when the cell has none."""
    found = [
        (int(m.group(3)), int(m.group(2)), int(m.group(1)))
        for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", cell or "")
    ]
    return max(found) if found else None


def resolve_conflict(members: list[Row]) -> tuple[Row | None, str, str]:
    """Deterministic precedence ladder for same-claim, conflicting-view members.

    Applies recency → basis → confidence band/number in order; the FIRST rule
    that yields a UNIQUE winner ends it. Returns (winner|None, rule, detail);
    None winner means rule 4 (needs_human): keep every row.
    """
    # Rule 1 — recency (only when EVERY row is dated and one date is strictly newest).
    dated = [(row, _row_date(row.date)) for row in members]
    if all(parts is not None for _, parts in dated):
        newest = max(parts for _, parts in dated)  # type: ignore[type-var]
        winners = [row for row, parts in dated if parts == newest]
        if len(winners) == 1:
            return winners[0], "recency", f"newest date {_fmt_date(newest)}"

    # Rule 2 — basis (stated > forecast_delta > inferred).
    ranked = [(row, _BASIS_RANK.get(row.basis, -1)) for row in members]
    top_basis = max(rank for _, rank in ranked)
    basis_winners = [row for row, rank in ranked if rank == top_basis]
    if len(basis_winners) == 1:
        winner = basis_winners[0]
        return winner, "basis", f"basis '{winner.basis}' beats the other rows"

    # Rule 3 — confidence band, then numeric confidence.
    def band_conf(row: Row) -> tuple[int, int]:
        return (_BAND_RANK.get(row.band, -1), row.confidence or 0)

    best = max(band_conf(row) for row in members)
    conf_winners = [row for row in members if band_conf(row) == best]
    if len(conf_winners) == 1:
        winner = conf_winners[0]
        return winner, "confidence", f"band {winner.band or '—'} / confidence {winner.confidence}"

    # Rule 4 — no unique winner: escalate the whole group to a human.
    return None, "needs_human", "no deterministic rule produced a unique winner"


# --------------------------------------------------------------------------- #
# Reconcile failures (folded into the run's failure files at the combine step)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ReconcileFailure:
    reason_code: str
    firm: str
    source: str
    leaf: str
    view: str
    notes: str

    def internal_row(self) -> dict[str, str]:
        """A failures.csv (FAILURE_COLUMNS) row. The internal file carries no Firm
        column, so the firm/source context lives in `message`."""
        return {
            "reason_code": self.reason_code,
            "message": self.notes,
            "source_id": self.source,
            "chunk_id": "",
            "sub_asset_raw": "",
            "sub_asset_class": self.leaf,
            "view": self.view,
            "taxonomy_match": "",
            "evidence_kind": "",
            "evidence_quote": "",
            "locator": "",
            "reasoning": "",
            "basis": "",
            "checker_strength": "",
            "call_language": "",
        }

    def client_row(self) -> dict[str, str]:
        """A failures-client.csv (CLIENT_FAILURE_COLUMNS) row with a plain label."""
        what_happened, explanation = client_failure_label(self.reason_code)
        return {
            "Firm": self.firm,
            "Source": self.source,
            "Sub-Asset Class": self.leaf,
            "View (proposed)": self.view,
            "What happened": what_happened,
            "Explanation": explanation,
            "Evidence / notes": self.notes,
        }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class KeyDecision:
    """Everything decided for one multi-row key."""

    firm: str
    leaf: str
    members: tuple[Row, ...]
    scope: Verdict
    action_bucket: str  # merged | distinct | superseded | needs_human
    precedence_rule: str
    emit: dict[int, dict[str, str] | None]  # id(row) -> row to emit, or None to drop
    per_row_action: dict[int, str]  # id(row) -> audit action
    surviving_pointer: str
    failures: tuple[ReconcileFailure, ...]


@dataclass
class ReconcileResult:
    output_rows: list[dict[str, str]]
    audit_rows: list[dict[str, str]]
    failures: list[ReconcileFailure]
    decisions: list[KeyDecision]
    single_row_key_count: int
    multi_row_key_count: int
    same_view_key_count: int  # multi-row keys whose rows all share a view
    conflicting_key_count: int  # multi-row keys with >1 view
    input_row_count: int
    output_row_count: int
    # Populated only when the Phase 3 near-leaf pass runs (--near-leaf); None
    # otherwise, so the exact-only path is byte-identical to before.
    near_leaf: "NearLeafResult | None" = None


def _decide_key(firm: str, leaf: str, members: list[Row], scope: Verdict) -> KeyDecision:
    emit: dict[int, dict[str, str] | None] = {}
    per_row_action: dict[int, str] = {}
    failures: list[ReconcileFailure] = []

    def keep_all(action: str) -> KeyDecision:
        """needs_human / distinct: every row survives. needs_human forces review."""
        force_review = action == ACTION_NEEDS_HUMAN
        for row in members:
            out = dict(row.raw)
            if force_review and (out.get("review_flag") or "none") == "none":
                out["review_flag"] = "review"
            emit[id(row)] = out
            per_row_action[id(row)] = action
        bucket = "needs_human" if action == ACTION_NEEDS_HUMAN else "distinct"
        rule = "needs_human" if action == ACTION_NEEDS_HUMAN else ""
        return KeyDecision(
            firm, leaf, tuple(members), scope, bucket, rule, emit, per_row_action,
            "(all rows kept)", tuple(failures),
        )

    # Scope-gate degraded, or genuinely distinct claims.
    if scope.verdict == VERDICT_NEEDS_HUMAN:
        return keep_all(ACTION_NEEDS_HUMAN)
    if scope.verdict == VERDICT_DISTINCT:
        return keep_all(ACTION_KEPT_DISTINCT)

    # scope.verdict == same_claim.
    views = {row.view for row in members}
    if len(views) == 1:
        merged, winner, losers = merge_same_view(members)
        emit[id(winner)] = merged
        per_row_action[id(winner)] = ACTION_WINNER
        for loser in losers:
            emit[id(loser)] = None
            per_row_action[id(loser)] = ACTION_MERGED
            failures.append(
                ReconcileFailure(
                    REASON_MERGED, firm, loser.source_title, leaf, loser.view,
                    f"same {loser.view} view on '{leaf}' merged into the kept row "
                    f"from {winner.source_title}; wording preserved in the merged commentary",
                )
            )
        return KeyDecision(
            firm, leaf, tuple(members), scope, "merged", "same_view_merge",
            emit, per_row_action, merged.get("Source", ""), tuple(failures),
        )

    # same_claim, conflicting views → precedence ladder.
    winner, rule, detail = resolve_conflict(members)
    if winner is None:
        return keep_all(ACTION_NEEDS_HUMAN)

    emit[id(winner)] = dict(winner.raw)
    per_row_action[id(winner)] = ACTION_WINNER
    for loser in members:
        if loser is winner:
            continue
        emit[id(loser)] = None
        per_row_action[id(loser)] = ACTION_SUPERSEDED
        failures.append(
            ReconcileFailure(
                REASON_SUPERSEDED, firm, loser.source_title, leaf, loser.view,
                f"{loser.view} superseded by {winner.source_title}'s {winner.view} "
                f"({rule}: {detail})",
            )
        )
    return KeyDecision(
        firm, leaf, tuple(members), scope, "superseded", rule,
        emit, per_row_action, winner.source_title, tuple(failures),
    )


AUDIT_COLUMNS = (
    "Firm",
    "Sub-Asset Class",
    "action",
    "scope_verdict",
    "scope_reason",
    "precedence_rule",
    "row_view",
    "row_source",
    "row_date",
    "row_confidence",
    "row_band",
    "row_basis",
    "surviving_row",
)


def _audit_rows(decisions: list[KeyDecision]) -> list[dict[str, str]]:
    """One row per input row that hit a multi-row key, in (firm_key, leaf) then
    member order. Carries BOTH confidences of the client's dual-confidence design:
    the original per-row confidence/band and the reconcile decision."""
    rows: list[dict[str, str]] = []
    for decision in decisions:
        for member in decision.members:
            rows.append(
                {
                    "Firm": member.firm,
                    "Sub-Asset Class": decision.leaf,
                    "action": decision.per_row_action[id(member)],
                    "scope_verdict": decision.scope.verdict,
                    "scope_reason": decision.scope.reason,
                    "precedence_rule": decision.precedence_rule,
                    "row_view": member.view,
                    "row_source": member.source_title,
                    "row_date": member.date,
                    "row_confidence": "" if member.confidence is None else str(member.confidence),
                    "row_band": member.band,
                    "row_basis": member.basis,
                    "surviving_row": decision.surviving_pointer,
                }
            )
    return rows


def run_reconcile(
    paths: list[Path],
    *,
    engine: str = "claude",
    model: str | None = "opus",
    effort: str | None = "medium",
    runner: Runner | None = None,
    use_llm: bool = True,
    near_leaf: bool = False,
    taxonomy: Taxonomy | None = None,
) -> ReconcileResult:
    """Load → group → scope gate → merge/precedence (the exact-leaf pass). When
    `near_leaf` is set, a second Phase 3 pass runs over the exact-reconciled rows
    (same LLM engine/model/effort — no independent default). Writes nothing."""
    rows = load_rows(paths)
    groups = group_rows(rows)
    multi_keys = multi_row_keys(groups)
    multi_groups = [groups[key] for key in multi_keys]

    same_view = sum(1 for members in multi_groups if len({r.view for r in members}) == 1)
    conflicting = len(multi_groups) - same_view

    verdicts = scope_verdicts(
        multi_groups, engine=engine, model=model, effort=effort, runner=runner, use_llm=use_llm
    )

    decisions: list[KeyDecision] = []
    decision_by_key: dict[tuple[str, str], KeyDecision] = {}
    for i, key in enumerate(multi_keys):
        members = groups[key]
        decision = _decide_key(members[0].firm, members[0].leaf, members, verdicts[i])
        decisions.append(decision)
        decision_by_key[key] = decision

    # Emit reconciled rows in original input order: single-row keys pass through;
    # multi-row keys emit only their surviving row(s) at each member's position.
    output_rows: list[dict[str, str]] = []
    for row in rows:
        key = (row.firm_key, row.leaf)
        decision = decision_by_key.get(key)
        if decision is None:
            output_rows.append(dict(row.raw))
            continue
        emitted = decision.emit.get(id(row))
        if emitted is not None:
            output_rows.append(emitted)

    failures = [failure for decision in decisions for failure in decision.failures]

    near_leaf_result: NearLeafResult | None = None
    if near_leaf:
        tax = taxonomy if taxonomy is not None else load_taxonomy()
        near_leaf_result = run_near_leaf(
            output_rows, taxonomy=tax, engine=engine, model=model,
            effort=effort, runner=runner, use_llm=use_llm,
        )
        # The near-leaf pass consumes the exact-reconciled rows and returns the new
        # master; its failures fold in alongside the exact-pass ones.
        output_rows = near_leaf_result.output_rows
        failures = failures + near_leaf_result.failures

    return ReconcileResult(
        output_rows=output_rows,
        audit_rows=_audit_rows(decisions),
        failures=failures,
        decisions=decisions,
        single_row_key_count=sum(1 for members in groups.values() if len(members) == 1),
        multi_row_key_count=len(multi_keys),
        same_view_key_count=same_view,
        conflicting_key_count=conflicting,
        input_row_count=len(rows),
        output_row_count=len(output_rows),
        near_leaf=near_leaf_result,
    )


# =========================================================================== #
# Phase 3 — near-leaf reconciliation (opt-in)
#
# Runs AFTER the exact-leaf pass above, over its reconciled rows, so the 61-key
# exact baseline is preserved and never touched when --near-leaf is off. Two
# deterministic candidate lanes pull same-firm related leaves into clusters; an
# LLM partitions each cluster's rows into collective calls (merge) vs distinct
# calls (keep separate); deterministic code applies the partition, fails closed
# to needs_human on any violation, and rebuilds every taxonomy field through
# src.taxonomy for a remapped row. Cross-firm volume is a SEPARATE advisory only.
# =========================================================================== #


@dataclass(frozen=True, slots=True)
class LeafMeta:
    """The locked-taxonomy facts a near-leaf decision needs about one leaf."""

    label: str
    tokens: frozenset[str]
    asset_class: str
    category: str
    canva: str
    number: int


def _leaf_meta(label: str, taxonomy: Taxonomy) -> LeafMeta | None:
    """LeafMeta for an exact locked label, or None when the label is not in the
    taxonomy (candidate generation skips leaves it cannot classify)."""
    try:
        entry = taxonomy.require_label(label)
    except UnknownTaxonomyLabel:
        return None
    return LeafMeta(
        label=label,
        tokens=frozenset(_leaf_tokens(label)),
        asset_class=entry.asset_class,
        category=entry.asset_class_category,
        canva=entry.canva_groupings,
        number=entry.number,
    )


@dataclass(frozen=True, slots=True)
class NearLeafCandidate:
    """One deterministic same-firm near-leaf pair (an edge in the cluster graph)."""

    firm_key: str
    leaf_a: str  # ordered by locked number then label, so the pair is canonical
    leaf_b: str
    lane: str
    overlap: float


def _classify_pair(a: LeafMeta, b: LeafMeta) -> str | None:
    """Which bounded lane (if any) pairs two leaves of the SAME top-level asset
    class. Structural lane preferred when both apply; None means not a candidate."""
    if a.asset_class != b.asset_class:
        return None
    overlap = token_overlap(a.label, b.label)
    subset = a.tokens <= b.tokens or b.tokens <= a.tokens
    # Structural lane: strong token overlap plus either a subset relationship or a
    # shared taxonomy category.
    if overlap >= STRUCTURAL_MIN_OVERLAP and (subset or a.category == b.category):
        return LANE_STRUCTURAL
    # Short-label containment lane: one leaf is <=2 meaningful tokens and all of
    # them appear in the other — the low-Jaccard normalization case (e.g. AI ↔
    # IT/Tech/Telecomms (inc. AI)) the structural lane misses.
    short = (
        (0 < len(a.tokens) <= SHORT_LABEL_MAX_TOKENS and a.tokens <= b.tokens)
        or (0 < len(b.tokens) <= SHORT_LABEL_MAX_TOKENS and b.tokens <= a.tokens)
    )
    if short:
        return LANE_SHORT_LABEL
    return None


def generate_candidates(rows: list[Row], taxonomy: Taxonomy) -> list[NearLeafCandidate]:
    """Deterministic near-leaf candidates: distinct locked leaves under the SAME
    firm and SAME top-level asset class that pass either bounded lane. Output is
    sorted and deduplicated (one edge per unordered leaf pair)."""
    meta_cache: dict[str, LeafMeta | None] = {}

    def meta(label: str) -> LeafMeta | None:
        if label not in meta_cache:
            meta_cache[label] = _leaf_meta(label, taxonomy)
        return meta_cache[label]

    leaves_by_firm: dict[str, set[str]] = {}
    firm_display: dict[str, str] = {}
    for row in rows:
        leaves_by_firm.setdefault(row.firm_key, set()).add(row.leaf)
        firm_display.setdefault(row.firm_key, row.firm)

    candidates: list[NearLeafCandidate] = []
    for firm_key, leaves in leaves_by_firm.items():
        ordered = sorted(leaves)
        for i in range(len(ordered)):
            for j in range(i + 1, len(ordered)):
                ma, mb = meta(ordered[i]), meta(ordered[j])
                if ma is None or mb is None:
                    continue
                lane = _classify_pair(ma, mb)
                if lane is None:
                    continue
                lo, hi = sorted((ma, mb), key=lambda m: (m.number, m.label))
                candidates.append(
                    NearLeafCandidate(
                        firm_key=firm_key,
                        leaf_a=lo.label,
                        leaf_b=hi.label,
                        lane=lane,
                        overlap=round(token_overlap(lo.label, hi.label), 4),
                    )
                )
    candidates.sort(key=lambda c: (c.firm_key, c.leaf_a, c.leaf_b))
    return candidates


# --------------------------------------------------------------------------- #
# Cluster construction (connected components of the candidate graph, per firm)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class NearLeafCluster:
    firm: str
    firm_key: str
    leaves: tuple[str, ...]  # distinct locked labels in the cluster, sorted
    rows: tuple[Row, ...]  # every exact-reconciled row on those leaves, ordered
    meta: dict[str, LeafMeta]  # label -> LeafMeta, for prompt context
    candidates: tuple[NearLeafCandidate, ...]  # the edges that formed the cluster


def _components(nodes: set[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    """Connected components over `nodes` given undirected `edges`. Deterministic:
    components and their members come back in sorted order."""
    parent = {n: n for n in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        parent[find(a)] = find(b)

    groups: dict[str, list[str]] = {}
    for n in nodes:
        groups.setdefault(find(n), []).append(n)
    return sorted((sorted(members) for members in groups.values()), key=lambda m: m[0])


def build_clusters(
    rows: list[Row], candidates: list[NearLeafCandidate], taxonomy: Taxonomy
) -> list[NearLeafCluster]:
    """Group candidate edges into per-firm connected components (>=2 leaves) and
    attach every exact-reconciled row on the component's leaves, in input order."""
    by_firm: dict[str, list[NearLeafCandidate]] = {}
    for cand in candidates:
        by_firm.setdefault(cand.firm_key, []).append(cand)

    rows_by_firm_leaf: dict[tuple[str, str], list[Row]] = {}
    firm_display: dict[str, str] = {}
    for row in rows:
        rows_by_firm_leaf.setdefault((row.firm_key, row.leaf), []).append(row)
        firm_display.setdefault(row.firm_key, row.firm)

    clusters: list[NearLeafCluster] = []
    for firm_key in sorted(by_firm):
        firm_cands = by_firm[firm_key]
        nodes = {c.leaf_a for c in firm_cands} | {c.leaf_b for c in firm_cands}
        edges = [(c.leaf_a, c.leaf_b) for c in firm_cands]
        for component in _components(nodes, edges):
            leaf_set = set(component)
            member_rows = [
                row
                for row in rows
                if row.firm_key == firm_key and row.leaf in leaf_set
            ]
            if len(member_rows) < 2:
                continue  # nothing to reconcile: a single row across the cluster
            meta = {label: _leaf_meta(label, taxonomy) for label in component}
            meta = {label: m for label, m in meta.items() if m is not None}
            clusters.append(
                NearLeafCluster(
                    firm=firm_display[firm_key],
                    firm_key=firm_key,
                    leaves=tuple(component),
                    rows=tuple(member_rows),
                    meta=meta,
                    candidates=tuple(
                        c for c in firm_cands if c.leaf_a in leaf_set and c.leaf_b in leaf_set
                    ),
                )
            )
    return clusters


# --------------------------------------------------------------------------- #
# Near-leaf LLM contract (partition each cluster into merge / keep-separate groups)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class NearLeafGroup:
    member_row_ids: tuple[int, ...]
    relationship: str
    canonical_leaf: str | None
    primary_row_id: int | None
    reason: str


@dataclass(frozen=True, slots=True)
class NearLeafVerdict:
    """A parsed cluster verdict, or a fail-closed sentinel (groups=(), failed)."""

    groups: tuple[NearLeafGroup, ...]
    failed: bool = False
    reason: str = ""


_NL_FAILED_REASON = "near-leaf judge failed; cluster degraded to needs_human"
_NL_NO_LLM_REASON = "near-leaf judge skipped (--no-llm); cluster degraded to needs_human"

# Bounded near-leaf batching: each LLM call sees at most this many clusters or
# rows (whichever binds first) so a large 98-batch stays out of one giant prompt.
NEARLEAF_BATCH_MAX_CLUSTERS = 8
NEARLEAF_BATCH_MAX_ROWS = 40


def parse_nearleaf(raw_response: str) -> dict[int, NearLeafVerdict]:
    """Parse `{"clusters": [{cluster_id, groups: [...]}]}`. Validates JSON shape,
    types, and the relationship enum so `llm.call_parsed`'s repair loop can
    re-prompt; membership/canonical/partition checks against the actual cluster
    happen deterministically at apply time (fail-closed to needs_human)."""
    payload = json.loads(llm._extract_json(raw_response))
    if not isinstance(payload, dict):
        raise ValueError("near-leaf response must be a JSON object")
    clusters_raw = payload.get("clusters")
    if not isinstance(clusters_raw, list):
        raise ValueError("near-leaf response must include a clusters list")

    out: dict[int, NearLeafVerdict] = {}
    for cluster in clusters_raw:
        if not isinstance(cluster, dict):
            raise ValueError("each cluster must be a JSON object")
        cluster_id = cluster.get("cluster_id")
        if not isinstance(cluster_id, int) or isinstance(cluster_id, bool):
            raise ValueError("each cluster needs an integer cluster_id")
        groups_raw = cluster.get("groups")
        if not isinstance(groups_raw, list) or not groups_raw:
            raise ValueError("each cluster needs a non-empty groups list")
        groups: list[NearLeafGroup] = []
        for group in groups_raw:
            groups.append(_parse_nearleaf_group(group))
        out[cluster_id] = NearLeafVerdict(groups=tuple(groups))
    return out


def _parse_nearleaf_group(group: object) -> NearLeafGroup:
    if not isinstance(group, dict):
        raise ValueError("each group must be a JSON object")
    ids = group.get("member_row_ids")
    if not isinstance(ids, list) or not ids or not all(
        isinstance(x, int) and not isinstance(x, bool) for x in ids
    ):
        raise ValueError("member_row_ids must be a non-empty list of integers")
    relationship = group.get("relationship")
    if relationship not in VALID_NL_RELATIONSHIPS:
        raise ValueError(
            f"relationship must be one of {', '.join(sorted(VALID_NL_RELATIONSHIPS))}; got {relationship!r}"
        )
    canonical = group.get("canonical_leaf")
    if canonical is not None and not isinstance(canonical, str):
        raise ValueError("canonical_leaf must be a string or null")
    canonical = canonical.strip() if isinstance(canonical, str) else None
    # A 2+ row group is a merge — it must name a canonical leaf up front so the
    # model gets a repair prompt rather than silently failing closed later.
    if len(ids) >= 2 and (relationship != NL_SAME_CLAIM or not canonical):
        raise ValueError("a multi-row group must be same_claim and name a canonical_leaf")
    primary = group.get("primary_row_id")
    if primary is not None and (not isinstance(primary, int) or isinstance(primary, bool)):
        raise ValueError("primary_row_id must be an integer or null")
    reason = group.get("reason", "")
    if not isinstance(reason, str):
        raise ValueError("group reason must be a string")
    return NearLeafGroup(
        member_row_ids=tuple(ids),
        relationship=relationship,
        canonical_leaf=canonical or None,
        primary_row_id=primary,
        reason=reason.strip(),
    )


def _batch_clusters(clusters: list[NearLeafCluster]) -> list[list[int]]:
    """Split cluster indices into bounded batches (cluster and row caps). Clusters
    arrive firm-grouped (build_clusters iterates firms in order), so a batch stays
    close to one firm without a hard firm boundary."""
    batches: list[list[int]] = []
    current: list[int] = []
    rows = 0
    for i, cluster in enumerate(clusters):
        if current and (
            len(current) >= NEARLEAF_BATCH_MAX_CLUSTERS
            or rows + len(cluster.rows) > NEARLEAF_BATCH_MAX_ROWS
        ):
            batches.append(current)
            current, rows = [], 0
        current.append(i)
        rows += len(cluster.rows)
    if current:
        batches.append(current)
    return batches


def _nearleaf_cluster_input(local_id: int, cluster: NearLeafCluster) -> dict:
    return {
        "cluster_id": local_id,
        "firm": cluster.firm,
        "leaves": [
            {
                "label": label,
                "category": m.category,
                "asset_class": m.asset_class,
                "canva_grouping": m.canva,
                "locked_order": m.number,
            }
            for label, m in sorted(cluster.meta.items(), key=lambda kv: kv[1].number)
        ],
        "rows": [
            {
                "row_id": rid,
                "leaf": row.leaf,
                "view": row.view,
                "source_title": row.source_title,
                "date": row.date,
                "full_commentary": row.commentary,
                "candidate_reason": _candidate_reason(cluster, row.leaf),
            }
            for rid, row in enumerate(cluster.rows)
        ],
    }


def nearleaf_verdicts(
    clusters: list[NearLeafCluster],
    *,
    engine: str,
    model: str | None,
    effort: str | None,
    runner: Runner | None = None,
    use_llm: bool = True,
) -> dict[int, NearLeafVerdict]:
    """Near-leaf judge pass over the clusters, in bounded batches. Returns
    {global cluster index: Verdict}. Any engine failure on a batch, or a cluster
    missing from the batch's response, degrades THOSE clusters to a fail-closed
    needs_human verdict — never a crash."""
    if not clusters:
        return {}
    if not use_llm:
        return {
            i: NearLeafVerdict((), failed=True, reason=_NL_NO_LLM_REASON)
            for i in range(len(clusters))
        }

    out: dict[int, NearLeafVerdict] = {}
    for batch in _batch_clusters(clusters):
        inputs = {
            "clusters": [
                _nearleaf_cluster_input(local_id, clusters[global_i])
                for local_id, global_i in enumerate(batch)
            ]
        }
        try:
            result = llm.call_parsed(
                RECONCILE_NEARLEAF_PROMPT,
                inputs,
                engine=engine,
                model=model,
                effort=effort,
                runner=runner,
                parser=parse_nearleaf,
            )
            payload: dict[int, NearLeafVerdict] = result.payload
        except Exception:  # noqa: BLE001 — a failed batch degrades its clusters, never crashes
            payload = {}
        for local_id, global_i in enumerate(batch):
            out[global_i] = payload.get(
                local_id, NearLeafVerdict((), failed=True, reason=_NL_FAILED_REASON)
            )
    return out


def _candidate_reason(cluster: NearLeafCluster, leaf: str) -> str:
    """A short 'lexical rule that pulled this leaf in' note for the prompt."""
    for cand in cluster.candidates:
        if leaf in (cand.leaf_a, cand.leaf_b):
            other = cand.leaf_b if leaf == cand.leaf_a else cand.leaf_a
            return f"{cand.lane} lane with '{other}' (token overlap {cand.overlap})"
    return ""


# --------------------------------------------------------------------------- #
# Deterministic apply: partition -> merge / supersede / keep, fail closed
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class NearLeafRowAudit:
    firm: str
    original_leaf: str
    canonical_leaf: str
    view: str
    source: str
    date: str
    join_type: str
    candidate_generation_rule: str
    token_overlap: str
    relationship_verdict: str
    mapping_action: str
    mapping_reason: str
    review_required: str


@dataclass(frozen=True, slots=True)
class NearLeafClusterDecision:
    cluster: NearLeafCluster
    emit: dict[int, dict[str, str] | None]  # id(row) -> emitted row, or None to drop
    failures: tuple[ReconcileFailure, ...]
    audits: tuple[NearLeafRowAudit, ...]
    action_bucket: str  # merged | mixed | kept | needs_human
    # Distinct original leaves in each merged group (for the coverage advisory's
    # near_leaf_action flag — a pair is "acted" when both its leaves sit in one set).
    merged_leaf_sets: tuple[frozenset[str], ...] = ()


def _cluster_rule(cluster: NearLeafCluster) -> tuple[str, str]:
    """(candidate_generation_rule, token_overlap) summary for a cluster's audit —
    the lanes and the max overlap across its edges."""
    lanes = sorted({c.lane for c in cluster.candidates})
    overlap = max((c.overlap for c in cluster.candidates), default=0.0)
    return " + ".join(lanes), f"{overlap:.4f}"


def _keep_cluster(
    cluster: NearLeafCluster, action: str, verdict_label: str, reason: str
) -> NearLeafClusterDecision:
    """needs_human / all-distinct: every row survives. needs_human forces review."""
    rule, overlap = _cluster_rule(cluster)
    force_review = action == NL_ACTION_NEEDS_HUMAN
    emit: dict[int, dict[str, str] | None] = {}
    audits: list[NearLeafRowAudit] = []
    for row in cluster.rows:
        out = dict(row.raw)
        if force_review and (out.get("review_flag") or "none") == "none":
            out["review_flag"] = "review"
        emit[id(row)] = out
        audits.append(
            NearLeafRowAudit(
                firm=cluster.firm, original_leaf=row.leaf, canonical_leaf="",
                view=row.view, source=row.source_title, date=row.date,
                join_type="near_leaf", candidate_generation_rule=rule, token_overlap=overlap,
                relationship_verdict=verdict_label, mapping_action=action,
                mapping_reason=reason, review_required="true" if force_review else "false",
            )
        )
    bucket = "needs_human" if force_review else "kept"
    return NearLeafClusterDecision(cluster, emit, (), tuple(audits), bucket)


def _nearleaf_merge(
    members: list[Row], canonical_leaf: str, primary: Row, taxonomy: Taxonomy
) -> dict[str, str]:
    """Merge members onto the validated canonical leaf, rebuilding ALL four
    taxonomy output fields from src.taxonomy (never just Sub-Asset Class). The
    primary row's view survives; Source/URL/Date pipe-joined; commentary labeled
    and ||||-merged with the primary first; confidence = max; review forced."""
    ordered = [primary, *[m for m in members if m is not primary]]
    merged = dict(primary.raw)
    merged.update(taxonomy.output_fields_for(canonical_leaf))  # all 4 taxonomy fields
    merged["View"] = primary.view
    merged["Source"] = _pipe_join_dedupe([m.source_title for m in members])
    merged["URL"] = _pipe_join_dedupe([m.url for m in members])
    merged["Date"] = _pipe_join_dedupe([m.date for m in members])
    merged["Full Commentary"] = merge_commentaries(
        [(m.source_title, _locator_from_commentary(m.commentary), m.commentary) for m in ordered]
    )
    merged["confidence"] = str(max((m.confidence or 0) for m in members))
    merged["band"] = primary.band
    # Every near-leaf survivor is flagged for review in this opt-in first run.
    merged["review_flag"] = "review"
    return merged


def apply_cluster(
    cluster: NearLeafCluster, verdict: NearLeafVerdict, taxonomy: Taxonomy
) -> NearLeafClusterDecision:
    """Turn one cluster verdict into emitted rows + failures + audits.

    Fails CLOSED to needs_human (keep all rows, force review) on any contract
    violation: the response failed, the groups don't partition the cluster's rows,
    a merge group names a canonical leaf not in the cluster (or not locked), or a
    conflicting-view merge omits a valid primary."""
    if verdict.failed:
        return _keep_cluster(cluster, NL_ACTION_NEEDS_HUMAN, "needs_human", verdict.reason)

    n = len(cluster.rows)
    leaf_labels = set(cluster.leaves)

    # Partition check: every row id exactly once, no strangers.
    seen: list[int] = []
    for group in verdict.groups:
        seen.extend(group.member_row_ids)
    if sorted(seen) != list(range(n)):
        return _keep_cluster(
            cluster, NL_ACTION_NEEDS_HUMAN, "needs_human",
            "near-leaf groups did not partition the cluster's rows",
        )

    rule, overlap = _cluster_rule(cluster)
    emit: dict[int, dict[str, str] | None] = {}
    failures: list[ReconcileFailure] = []
    audits: list[NearLeafRowAudit] = []
    merged_leaf_sets: list[frozenset[str]] = []
    saw_merge = False
    saw_cross_view = False

    for group in verdict.groups:
        members = [cluster.rows[rid] for rid in group.member_row_ids]

        if len(members) == 1:
            row = members[0]
            emit[id(row)] = dict(row.raw)
            audits.append(_row_audit(cluster, row, "", NL_ACTION_KEPT, group.relationship,
                                     group.reason, rule, overlap, review=False))
            continue

        # A merge group. Validate the canonical leaf and the primary.
        canonical = group.canonical_leaf
        if canonical not in leaf_labels or not taxonomy.is_valid_label(canonical):
            return _keep_cluster(
                cluster, NL_ACTION_NEEDS_HUMAN, "needs_human",
                f"canonical leaf {canonical!r} is not one of the cluster's locked leaves",
            )
        views = {m.view for m in members}
        if len(views) == 1:
            primary = max(members, key=lambda m: (m.confidence or 0))
        else:
            saw_cross_view = True
            primary = next((m for i, m in zip(group.member_row_ids, members)
                            if i == group.primary_row_id), None)
            if primary is None:
                return _keep_cluster(
                    cluster, NL_ACTION_NEEDS_HUMAN, "needs_human",
                    "conflicting-view near-leaf merge did not name a valid primary_row_id",
                )

        saw_merge = True
        merged_leaf_sets.append(frozenset(m.leaf for m in members))
        merged = _nearleaf_merge(members, canonical, primary, taxonomy)
        emit[id(primary)] = merged
        audits.append(_row_audit(cluster, primary, canonical, NL_ACTION_WINNER,
                                 group.relationship, group.reason, rule, overlap, review=True))
        for loser in members:
            if loser is primary:
                continue
            emit[id(loser)] = None
            if len(views) == 1:
                code, action = REASON_NEAR_LEAF_MERGED, NL_ACTION_MERGED
                note = (
                    f"same {loser.view} view on near-leaf '{loser.leaf}' merged into the kept "
                    f"'{canonical}' row from {primary.source_title}; wording preserved in the "
                    f"merged commentary"
                )
            else:
                code, action = REASON_NEAR_LEAF_SUPERSEDED, NL_ACTION_SUPERSEDED
                note = (
                    f"{loser.view} on '{loser.leaf}' folded into {primary.source_title}'s "
                    f"{primary.view} on '{canonical}' as the more relevant collective call; "
                    f"flagged for review"
                )
            failures.append(
                ReconcileFailure(code, cluster.firm, loser.source_title, loser.leaf, loser.view, note)
            )
            audits.append(_row_audit(cluster, loser, canonical, action, group.relationship,
                                     group.reason, rule, overlap, review=True))

    bucket = "mixed" if (saw_merge and saw_cross_view) else ("merged" if saw_merge else "kept")
    return NearLeafClusterDecision(
        cluster, emit, tuple(failures), tuple(audits), bucket, tuple(merged_leaf_sets)
    )


def _row_audit(
    cluster: NearLeafCluster, row: Row, canonical: str, action: str, relationship: str,
    reason: str, rule: str, overlap: str, *, review: bool,
) -> NearLeafRowAudit:
    return NearLeafRowAudit(
        firm=cluster.firm, original_leaf=row.leaf, canonical_leaf=canonical,
        view=row.view, source=row.source_title, date=row.date, join_type="near_leaf",
        candidate_generation_rule=rule, token_overlap=overlap,
        relationship_verdict=relationship, mapping_action=action, mapping_reason=reason,
        review_required="true" if review else "false",
    )


# --------------------------------------------------------------------------- #
# Cross-firm broad/specific coverage advisory (volume is CONTEXT ONLY)
# --------------------------------------------------------------------------- #

COVERAGE_COLUMNS = (
    "broad_leaf",
    "specific_leaf",
    "asset_class",
    "broad_firm_count",
    "specific_firm_count",
    "broad_row_count",
    "specific_row_count",
    "firms_with_both",
    "near_leaf_action",
    "human_review_advisory",
)


def coverage_advisory(
    rows: list[Row], taxonomy: Taxonomy, merged_leaf_sets: list[frozenset[str]] | None = None
) -> list[dict[str, str]]:
    """Deterministic cross-firm broad↔specific coverage table. For every pair of
    locked leaves (any firm) where one leaf's meaningful tokens are a strict subset
    of the other's under the same asset class, report volume on each side. Volume
    is CONTEXT ONLY — it never chooses a canonical leaf. `merged_leaf_sets` marks
    which broad/specific pairs the near-leaf pass actually merged for some firm (a
    pair is flagged when both its leaves fall inside one merged set)."""
    merged_leaf_sets = merged_leaf_sets or []
    firms_by_leaf: dict[str, set[str]] = {}
    rowcount_by_leaf: dict[str, int] = {}
    for row in rows:
        firms_by_leaf.setdefault(row.leaf, set()).add(row.firm_key)
        rowcount_by_leaf[row.leaf] = rowcount_by_leaf.get(row.leaf, 0) + 1

    metas = {leaf: _leaf_meta(leaf, taxonomy) for leaf in firms_by_leaf}
    metas = {leaf: m for leaf, m in metas.items() if m is not None}
    labels = sorted(metas)

    out: list[dict[str, str]] = []
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            a, b = metas[labels[i]], metas[labels[j]]
            if a.asset_class != b.asset_class:
                continue
            if a.tokens < b.tokens:
                broad, specific = a, b
            elif b.tokens < a.tokens:
                broad, specific = b, a
            else:
                continue  # not a strict broad/specific containment
            both = firms_by_leaf[broad.label] & firms_by_leaf[specific.label]
            pair = {broad.label, specific.label}
            acted = any(pair <= s for s in merged_leaf_sets)
            advisory = (
                "same firm(s) carry both — candidate for near-leaf review"
                if both
                else "cross-firm coverage split across the broad and specific leaf"
            )
            out.append(
                {
                    "broad_leaf": broad.label,
                    "specific_leaf": specific.label,
                    "asset_class": broad.asset_class,
                    "broad_firm_count": str(len(firms_by_leaf[broad.label])),
                    "specific_firm_count": str(len(firms_by_leaf[specific.label])),
                    "broad_row_count": str(rowcount_by_leaf[broad.label]),
                    "specific_row_count": str(rowcount_by_leaf[specific.label]),
                    "firms_with_both": str(len(both)),
                    "near_leaf_action": "merged" if acted else "",
                    "human_review_advisory": advisory,
                }
            )
    out.sort(key=lambda r: (r["asset_class"], r["broad_leaf"], r["specific_leaf"]))
    return out


# --------------------------------------------------------------------------- #
# Near-leaf orchestration (over the exact-reconciled rows)
# --------------------------------------------------------------------------- #


@dataclass
class NearLeafResult:
    output_rows: list[dict[str, str]]
    failures: list[ReconcileFailure]
    audit_rows: list[dict[str, str]]
    coverage_rows: list[dict[str, str]]
    cluster_count: int
    candidate_count: int
    merged_count: int
    superseded_count: int
    kept_count: int
    needs_human_count: int


NEARLEAF_AUDIT_COLUMNS = (
    "Firm",
    "original_leaf",
    "canonical_leaf",
    "row_view",
    "row_source",
    "row_date",
    "join_type",
    "candidate_generation_rule",
    "token_overlap",
    "relationship_verdict",
    "mapping_action",
    "review_required",
    "mapping_reason",
)


def _audit_to_row(a: NearLeafRowAudit) -> dict[str, str]:
    return {
        "Firm": a.firm,
        "original_leaf": a.original_leaf,
        "canonical_leaf": a.canonical_leaf,
        "row_view": a.view,
        "row_source": a.source,
        "row_date": a.date,
        "join_type": a.join_type,
        "candidate_generation_rule": a.candidate_generation_rule,
        "token_overlap": a.token_overlap,
        "relationship_verdict": a.relationship_verdict,
        "mapping_action": a.mapping_action,
        "review_required": a.review_required,
        "mapping_reason": a.mapping_reason,
    }


def run_near_leaf(
    exact_output_rows: list[dict[str, str]],
    *,
    taxonomy: Taxonomy,
    engine: str,
    model: str | None,
    effort: str | None,
    runner: Runner | None = None,
    use_llm: bool = True,
) -> NearLeafResult:
    """Near-leaf pass over the EXACT-reconciled rows. Generates candidates, builds
    per-firm clusters, judges each with the LLM, applies the partition
    deterministically (fail-closed), and emits reconciled rows + failures + audit +
    the standalone coverage advisory. Rows outside any cluster pass through."""
    rows = [row_from_raw(raw, "reconciled", i) for i, raw in enumerate(exact_output_rows)]
    candidates = generate_candidates(rows, taxonomy)
    clusters = build_clusters(rows, candidates, taxonomy)
    verdicts = nearleaf_verdicts(
        clusters, engine=engine, model=model, effort=effort, runner=runner, use_llm=use_llm
    )

    decisions = [apply_cluster(clusters[i], verdicts[i], taxonomy) for i in range(len(clusters))]
    emit: dict[int, dict[str, str] | None] = {}
    for decision in decisions:
        emit.update(decision.emit)

    output_rows: list[dict[str, str]] = []
    for row in rows:
        if id(row) in emit:
            emitted = emit[id(row)]
            if emitted is not None:
                output_rows.append(emitted)
        else:
            output_rows.append(dict(row.raw))

    failures = [f for d in decisions for f in d.failures]
    audit_rows = [_audit_to_row(a) for d in decisions for a in d.audits]

    merged_leaf_sets = [s for d in decisions for s in d.merged_leaf_sets]
    coverage = coverage_advisory(rows, taxonomy, merged_leaf_sets)

    merged = sum(1 for f in failures if f.reason_code == REASON_NEAR_LEAF_MERGED)
    superseded = sum(1 for f in failures if f.reason_code == REASON_NEAR_LEAF_SUPERSEDED)
    kept = sum(1 for d in decisions if d.action_bucket == "kept")
    needs_human = sum(1 for d in decisions if d.action_bucket == "needs_human")

    return NearLeafResult(
        output_rows=output_rows,
        failures=failures,
        audit_rows=audit_rows,
        coverage_rows=coverage,
        cluster_count=len(clusters),
        candidate_count=len(candidates),
        merged_count=merged,
        superseded_count=superseded,
        kept_count=kept,
        needs_human_count=needs_human,
    )


# --------------------------------------------------------------------------- #
# Output rendering
# --------------------------------------------------------------------------- #


def render_summary(result: ReconcileResult, paths: list[Path]) -> str:
    action_counts: Counter[str] = Counter()
    for decision in result.decisions:
        for action in decision.per_row_action.values():
            action_counts[action] += 1
    needs_human = [d for d in result.decisions if d.action_bucket == "needs_human"]

    lines: list[str] = []
    lines.append("# Firm-reconcile summary")
    lines.append("")
    lines.append(
        "Post-run firm-reconcile stage (v1.2 item 1 — the client's dual-confidence "
        "two-pass design). Deterministic join on `src.eval`-normalized firm + "
        "sub-asset leaf; an LLM scope gate classifies each multi-row key as the "
        "same claim or distinct claims, then all merge/precedence decisions are "
        "deterministic code. Never a forced call, never a majority vote."
    )
    lines.append("")
    lines.append("## Inputs")
    lines.append("")
    for path in paths:
        lines.append(f"- {path}")
    lines.append("")
    lines.append("## Row totals")
    lines.append("")
    lines.append(f"- input rows: {result.input_row_count}")
    lines.append(f"- reconciled output rows: {result.output_row_count}")
    lines.append(f"- rows removed (merged/superseded away): {result.input_row_count - result.output_row_count}")
    lines.append("")
    lines.append("## Keys")
    lines.append("")
    lines.append(f"- single-row keys (passed through untouched): {result.single_row_key_count}")
    lines.append(f"- multi-row keys: {result.multi_row_key_count}")
    lines.append(f"  - all-same-view: {result.same_view_key_count}")
    lines.append(f"  - conflicting views: {result.conflicting_key_count}")
    lines.append("")
    lines.append(
        "Sanity anchor: the frozen crosscheck report over the same inputs found "
        "61 keys (39 same-view, 22 conflicting). Any drift from those numbers is "
        "expected only if the underlying outputs changed since that report."
    )
    lines.append("")
    lines.append("## Per-action row counts")
    lines.append("")
    for action in (ACTION_WINNER, ACTION_MERGED, ACTION_SUPERSEDED, ACTION_KEPT_DISTINCT, ACTION_NEEDS_HUMAN):
        lines.append(f"- `{action}`: {action_counts.get(action, 0)}")
    lines.append("")
    lines.append("## Needs-human keys")
    lines.append("")
    if needs_human:
        for decision in needs_human:
            views = " | ".join(row.view for row in decision.members)
            reason = decision.scope.reason or decision.precedence_rule
            lines.append(
                f"- **{decision.firm} / {decision.leaf}** (views: {views}) — {reason}"
            )
    else:
        lines.append("_None._")
    lines.append("")
    nl = result.near_leaf
    if nl is not None:
        lines.append("## Near-leaf pass (Phase 3)")
        lines.append("")
        lines.append(
            "A second deterministic-candidate + LLM-partition pass over the "
            "exact-reconciled rows: same-firm related leaves are clustered by two "
            "bounded lexical lanes, an LLM groups each cluster's rows into "
            "collective calls (merged onto a validated canonical leaf) vs distinct "
            "calls (kept), and any contract violation fails closed to needs_human. "
            "Every near-leaf survivor is flagged for review in this first run."
        )
        lines.append("")
        lines.append(f"- near-leaf candidate pairs: {nl.candidate_count}")
        lines.append(f"- clusters judged: {nl.cluster_count}")
        lines.append(f"- rows merged away (same-view): {nl.merged_count}")
        lines.append(f"- rows superseded (cross-view collective pick): {nl.superseded_count}")
        lines.append(f"- clusters kept fully separate: {nl.kept_count}")
        lines.append(f"- clusters failed closed to needs_human: {nl.needs_human_count}")
        lines.append(f"- broad/specific coverage advisory rows: {len(nl.coverage_rows)}")
        lines.append("")

    lines.append("## Scope — what this stage does NOT do")
    lines.append("")
    if nl is None:
        lines.append(
            "- **Exact firm+leaf join only.** Adjacent leaves (\"US Duration\" vs "
            "\"US Treasuries\") are NOT merged; near-leaf matching is the opt-in "
            "Phase 3 pass (`--near-leaf`), v1.2 backlog item 6 in `ROADMAP.md`."
        )
    else:
        lines.append(
            "- **Cross-firm volume never decides a mapping.** Broad/specific volume "
            "is a standalone advisory (`taxonomy-coverage-review.csv`); the canonical "
            "leaf is chosen from commentary evidence, never by row counts."
        )
    lines.append(
        "- **No LLM-invented numbers.** Every merge, precedence, and near-leaf "
        "decision is deterministic code over categorical LLM judgments; the model "
        "never invents a label, a number, or a surviving row. Any LLM failure "
        "degrades that key/cluster to needs_human."
    )
    lines.append("")
    return "\n".join(lines)


def write_outputs(result: ReconcileResult, out_dir: Path, paths: list[Path]) -> dict[str, Path]:
    """Write output.csv, reconcile-audit.csv, reconcile-summary.md, and the
    reconcile failure rows (client-shaped, for inspection / combine)."""
    out_dir.mkdir(parents=True, exist_ok=True)

    output_path = out_dir / "output.csv"
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result.output_rows)

    audit_path = out_dir / "reconcile-audit.csv"
    with audit_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=AUDIT_COLUMNS)
        writer.writeheader()
        writer.writerows(result.audit_rows)

    summary_path = out_dir / "reconcile-summary.md"
    summary_path.write_text(render_summary(result, paths), encoding="utf-8")

    failures_path = out_dir / "reconcile-failures-client.csv"
    with failures_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CLIENT_FAILURE_COLUMNS)
        writer.writeheader()
        writer.writerows(failure.client_row() for failure in result.failures)

    written = {
        "output": output_path,
        "audit": audit_path,
        "summary": summary_path,
        "failures": failures_path,
    }

    # Phase 3 artifacts, written only when the near-leaf pass ran.
    if result.near_leaf is not None:
        nl_audit_path = out_dir / "reconcile-nearleaf-audit.csv"
        with nl_audit_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=NEARLEAF_AUDIT_COLUMNS)
            writer.writeheader()
            writer.writerows(result.near_leaf.audit_rows)
        written["nearleaf_audit"] = nl_audit_path

        coverage_path = out_dir / "taxonomy-coverage-review.csv"
        with coverage_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=COVERAGE_COLUMNS)
            writer.writeheader()
            writer.writerows(result.near_leaf.coverage_rows)
        written["coverage"] = coverage_path

    return written


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _resolve_model(engine: str, model: str | None) -> str | None:
    """claude requires an explicit model; supply the opus default when omitted.
    codex passes the model through unchanged — None → the adapter's default codex
    model, a named codex model validated downstream (never silently dropped)."""
    if engine == "claude" and model is None:
        return "opus"
    return model


def build_parser() -> argparse.ArgumentParser:
    """The reconcile CLI parser. Extracted so the no-flags scope-gate defaults
    (the model matrix) are testable."""
    parser = argparse.ArgumentParser(
        prog="python -m src.reconcile",
        description="Post-run firm-reconcile stage with a dual-confidence audit trail.",
    )
    parser.add_argument("--outputs", required=True, nargs="+", type=Path, help="one or more frozen run output.csv files")
    parser.add_argument("--out-dir", required=True, type=Path, help="directory for the reconciled output + audit + summary")
    parser.add_argument("--engine", default="claude", help="LLM engine for the scope gate (default: claude)")
    parser.add_argument("--model", default="opus", help="model for the scope gate (claude default: opus; codex: allowlist member, default gpt-5.5)")
    parser.add_argument("--effort", default="medium", help="reasoning effort (default: medium)")
    parser.add_argument("--no-llm", action="store_true", help="skip the scope gate; every multi-row key degrades to needs_human")
    parser.add_argument(
        "--near-leaf",
        action="store_true",
        help="run the Phase 3 near-leaf pass over the exact-reconciled rows (opt-in; same model as the scope gate)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    model = _resolve_model(args.engine, args.model)
    result = run_reconcile(
        args.outputs,
        engine=args.engine,
        model=model,
        effort=args.effort,
        use_llm=not args.no_llm,
        near_leaf=args.near_leaf,
    )
    written = write_outputs(result, args.out_dir, args.outputs)

    action_counts: Counter[str] = Counter()
    for decision in result.decisions:
        for action in decision.per_row_action.values():
            action_counts[action] += 1
    needs_human = sum(1 for d in result.decisions if d.action_bucket == "needs_human")
    print(
        f"reconcile: {result.input_row_count} -> {result.output_row_count} rows; "
        f"{result.multi_row_key_count} multi-row keys "
        f"({result.same_view_key_count} same-view, {result.conflicting_key_count} conflicting); "
        f"{action_counts.get(ACTION_MERGED, 0)} merged, "
        f"{action_counts.get(ACTION_SUPERSEDED, 0)} superseded, "
        f"{action_counts.get(ACTION_KEPT_DISTINCT, 0)} kept_distinct, "
        f"{needs_human} needs_human keys"
    )
    if result.near_leaf is not None:
        nl = result.near_leaf
        print(
            f"  near-leaf: {nl.candidate_count} candidate pairs, {nl.cluster_count} clusters; "
            f"{nl.merged_count} merged, {nl.superseded_count} superseded, "
            f"{nl.kept_count} kept, {nl.needs_human_count} needs_human clusters"
        )
    for label, path in written.items():
        print(f"  {label}: {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
