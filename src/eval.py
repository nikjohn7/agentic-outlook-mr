"""Deterministic ground-truth comparison harness for a frozen run.

`eval.py` is a standalone CLI, run ONLY after a run's `output.csv` is frozen. It
is NOT part of the run pipeline and makes NO LLM calls anywhere — it does the
bookkeeping (joins, counts, bucketing) so the judgment layer (is a miss
defensible? is a model-only row an overreach?) stays with humans/agents
downstream, fed by the `judgment-worksheet.csv` this tool emits.

    .venv/bin/python -m src.eval --run runs/pilot-06 \\
        --ground-truth ground-truth/pilot-ground-truth.csv

Core logic:

1. Load the run's `output.csv` and the ground-truth CSV; normalize firm names
   for the join (see `normalize_firm`).
2. Join on (normalized firm, sub-asset-class leaf). Buckets use the pilot-05
   phase-1 vocabulary exactly: `exact_match` (split view-agree / view-disagree),
   `model_only`, `gt_only`.
3. Near-leaf candidates: for each gt_only row, list same-firm model rows with an
   agreeing view whose leaf differs, with a token-overlap similarity hint —
   suggestions for the human judgment pass, never auto-matched.
4. Metrics: raw leaf-match recall, view-agreement among matched, per-firm table,
   UNCERTAIN reported separately as abstain/coverage, the missed-call list, a
   review-flag hit analysis, and basis / checker_strength / band distributions.
5. Quote-verbatim spot check (best-effort): when `work/<run-id>/` snapshots
   exist, re-verify each output row's parsed evidence via the existing checks in
   `src/confidence.py`. Skips cleanly with a note when snapshots are absent.

Outputs land in `runs/<run-id>/eval/`: `eval-report.md`, `eval-buckets.json`,
and `judgment-worksheet.csv`.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from src import confidence
from src.schemas import CandidateCall
from src.taxonomy import Taxonomy, load_taxonomy

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# The join is deterministic and case/punctuation-insensitive on the firm name.
# In pilot-05 the GT and output spellings happen to match exactly, but the
# product's real inputs differ ("J.P. Morgan" vs "JP Morgan", "PIMCO" vs
# "Pimco"), so we fold both sides through the same normalization before joining.
VIEW_CODES = ("O", "N", "U", "UNCERTAIN")
ABSTAIN_VIEW = "UNCERTAIN"

# Tokens that carry no leaf-identity signal for the near-leaf overlap hint.
_LEAF_STOPWORDS = frozenset(
    {"the", "and", "of", "a", "an", "for", "to", "in", "-", "/", "general"}
)


class EvalError(RuntimeError):
    """A fatal problem loading the run or ground-truth inputs."""


def normalize_firm(name: str) -> str:
    """Fold a firm name to a join key.

    NFKC, lowercased, with dots/commas removed and whitespace collapsed. This
    makes "J.P. Morgan Asset Management" and "JP Morgan Asset Management" (or
    "PIMCO"/"Pimco") join, while never merging genuinely distinct firms. Applied
    identically to both the output and the ground-truth side.
    """
    text = unicodedata.normalize("NFKC", name).lower()
    text = text.replace(".", "").replace(",", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _leaf_key(leaf: str) -> str:
    """The leaf half of the join key: stripped, exact otherwise.

    Leaves must be exact locked taxonomy labels (validation is done separately);
    the join itself is exact-string so a real leaf-name drift shows up as a
    near-leaf suggestion rather than being silently absorbed."""
    return leaf.strip()


def _leaf_tokens(leaf: str) -> set[str]:
    """Content tokens of a leaf label for the token-overlap similarity hint."""
    lowered = unicodedata.normalize("NFKC", leaf).lower()
    raw = re.split(r"[^a-z0-9]+", lowered)
    return {token for token in raw if token and token not in _LEAF_STOPWORDS}


def token_overlap(left: str, right: str) -> float:
    """Jaccard token overlap between two leaf labels (0.0–1.0).

    A cheap similarity HINT for the human judgment pass — never a match
    decision."""
    a = _leaf_tokens(left)
    b = _leaf_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# --------------------------------------------------------------------------- #
# Row loading
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Row:
    """One normalized CSV row (model output or ground truth)."""

    firm: str
    firm_key: str
    leaf: str
    view: str
    commentary: str
    raw: dict[str, str]
    index: int  # 0-based data-row index within its source file


def _read_rows(path: Path, *, commentary_column: str) -> list[Row]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise EvalError(f"CSV is empty: {path}")
        required = {"Firm", "Sub-Asset Class", "View"}
        missing = required - set(reader.fieldnames)
        if missing:
            raise EvalError(
                f"{path} missing required columns: {', '.join(sorted(missing))}"
            )
        rows: list[Row] = []
        for index, raw in enumerate(reader):
            firm = (raw.get("Firm") or "").strip()
            rows.append(
                Row(
                    firm=firm,
                    firm_key=normalize_firm(firm),
                    leaf=_leaf_key(raw.get("Sub-Asset Class") or ""),
                    view=(raw.get("View") or "").strip(),
                    commentary=(raw.get(commentary_column) or "").strip(),
                    raw=raw,
                    index=index,
                )
            )
        return rows


def _index_by_key(rows: Iterable[Row], *, kind: str) -> dict[tuple[str, str], Row]:
    """Index rows by (firm_key, leaf); raise on a duplicate join key."""
    indexed: dict[tuple[str, str], Row] = {}
    for row in rows:
        key = (row.firm_key, row.leaf)
        if key in indexed:
            raise EvalError(
                f"duplicate ({kind}) join key {key!r} at rows "
                f"{indexed[key].index} and {row.index}"
            )
        indexed[key] = row
    return indexed


# --------------------------------------------------------------------------- #
# Join + buckets
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class MatchedPair:
    firm: str
    leaf: str
    model_view: str
    gt_view: str
    view_agree: bool
    abstain: bool  # model view is UNCERTAIN — neither right nor wrong
    model: Row
    gt: Row


@dataclass(frozen=True, slots=True)
class NearLeaf:
    """A same-firm model row proposed as a near-leaf candidate for a gt_only
    row. A SUGGESTION for the human pass — never auto-matched."""

    model_leaf: str
    model_view: str
    similarity: float
    model_row_index: int


@dataclass
class EvalResult:
    run_dir: Path
    model_rows: list[Row]
    gt_rows: list[Row]
    matched: list[MatchedPair] = field(default_factory=list)
    model_only: list[Row] = field(default_factory=list)
    gt_only: list[Row] = field(default_factory=list)
    near_leaf: dict[tuple[str, str], list[NearLeaf]] = field(default_factory=dict)
    spot_check: dict = field(default_factory=dict)


def build_eval(model_rows: list[Row], gt_rows: list[Row], run_dir: Path) -> EvalResult:
    """Join model output against ground truth into the three phase-1 buckets."""
    model_index = _index_by_key(model_rows, kind="model")
    gt_index = _index_by_key(gt_rows, kind="ground-truth")

    result = EvalResult(run_dir=run_dir, model_rows=model_rows, gt_rows=gt_rows)

    for key, model in model_index.items():
        if key not in gt_index:
            result.model_only.append(model)
            continue
        gt = gt_index[key]
        abstain = model.view == ABSTAIN_VIEW
        result.matched.append(
            MatchedPair(
                firm=model.firm,
                leaf=model.leaf,
                model_view=model.view,
                gt_view=gt.view,
                view_agree=(not abstain) and model.view == gt.view,
                abstain=abstain,
                model=model,
                gt=gt,
            )
        )

    for key, gt in gt_index.items():
        if key not in model_index:
            result.gt_only.append(gt)

    result.near_leaf = _near_leaf_candidates(result.gt_only, result.model_only)
    return result


def _near_leaf_candidates(
    gt_only: list[Row], model_only: list[Row]
) -> dict[tuple[str, str], list[NearLeaf]]:
    """For each gt_only row, same-firm model_only rows with an AGREEING view and
    a DIFFERENT leaf, ranked by token overlap. Suggestions only."""
    by_firm: dict[str, list[Row]] = {}
    for row in model_only:
        by_firm.setdefault(row.firm_key, []).append(row)

    suggestions: dict[tuple[str, str], list[NearLeaf]] = {}
    for gt in gt_only:
        candidates: list[NearLeaf] = []
        for model in by_firm.get(gt.firm_key, ()):
            if model.leaf == gt.leaf:
                continue  # would be an exact match, not near-leaf
            if model.view != gt.view:
                continue  # only agreeing-view candidates help a missed call
            similarity = token_overlap(gt.leaf, model.leaf)
            if similarity <= 0:
                # No shared content token — an agreeing view alone is too weak to
                # suggest as a near-leaf. (Deterministic-token limitation: a
                # purely semantic pair like Oil/Energy is not surfaced; that is
                # left to the human judgment pass.)
                continue
            candidates.append(
                NearLeaf(
                    model_leaf=model.leaf,
                    model_view=model.view,
                    similarity=round(similarity, 3),
                    model_row_index=model.index,
                )
            )
        candidates.sort(key=lambda c: (-c.similarity, c.model_leaf))
        if candidates:
            suggestions[(gt.firm_key, gt.leaf)] = candidates
    return suggestions


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #


def _firm_display_names(result: EvalResult) -> dict[str, str]:
    names: dict[str, str] = {}
    for row in (*result.gt_rows, *result.model_rows):
        names.setdefault(row.firm_key, row.firm)
    return names


def per_firm_table(result: EvalResult) -> list[dict]:
    """Per-firm join math, ordered by firm display name."""
    names = _firm_display_names(result)
    firms = sorted(names, key=lambda key: names[key].lower())
    gt_totals = _count_by_firm(result.gt_rows)
    model_totals = _count_by_firm(result.model_rows)

    table: list[dict] = []
    for firm_key in firms:
        matched = [m for m in result.matched if m.model.firm_key == firm_key]
        agree = sum(1 for m in matched if m.view_agree)
        abstain = sum(1 for m in matched if m.abstain)
        disagree = len(matched) - agree - abstain
        gt_total = gt_totals.get(firm_key, 0)
        table.append(
            {
                "firm": names[firm_key],
                "gt_total": gt_total,
                "model_total": model_totals.get(firm_key, 0),
                "matched": len(matched),
                "view_agree": agree,
                "view_disagree": disagree,
                "abstain": abstain,
                "model_only": sum(
                    1 for r in result.model_only if r.firm_key == firm_key
                ),
                "gt_only": sum(1 for r in result.gt_only if r.firm_key == firm_key),
                "recall_pct": round(100 * len(matched) / gt_total, 1)
                if gt_total
                else 0.0,
            }
        )
    return table


def _count_by_firm(rows: Iterable[Row]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.firm_key] = counts.get(row.firm_key, 0) + 1
    return counts


def headline_metrics(result: EvalResult) -> dict:
    """Run-level headline numbers."""
    gt_total = len(result.gt_rows)
    model_total = len(result.model_rows)
    matched = len(result.matched)
    agree = sum(1 for m in result.matched if m.view_agree)
    abstain = sum(1 for m in result.matched if m.abstain)
    disagree = matched - agree - abstain
    decided = agree + disagree  # UNCERTAIN excluded from the agreement rate
    return {
        "gt_total": gt_total,
        "model_total": model_total,
        "exact_match": matched,
        "view_agree": agree,
        "view_disagree": disagree,
        "abstain_uncertain": abstain,
        "model_only": len(result.model_only),
        "gt_only": len(result.gt_only),
        "raw_recall": _pct(matched, gt_total),
        "view_agreement_among_decided": _pct(agree, decided),
    }


def _pct(numerator: int, denominator: int) -> dict:
    pct = round(100 * numerator / denominator, 1) if denominator else 0.0
    return {"n": numerator, "d": denominator, "pct": pct}


def column_distribution(rows: list[Row], column: str) -> dict[str, int] | None:
    """Value counts for an optional output column, or None if absent from the
    run (pilot-05 predates the `basis`/`checker_strength` columns, so those are
    reported as 'not present in this run')."""
    if not rows or column not in rows[0].raw:
        return None
    counts: dict[str, int] = {}
    for row in rows:
        value = (row.raw.get(column) or "").strip() or "(empty)"
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def review_flag_analysis(result: EvalResult) -> dict:
    """How many disagreements / model_only rows sit on review-flagged rows.

    Misses (gt_only) have no model row, so no review flag — but a near-leaf
    candidate might, so we report how many missed calls have a flagged near-leaf
    suggestion."""
    has_flag = bool(result.model_rows) and "review_flag" in result.model_rows[0].raw

    def flagged(row: Row) -> bool:
        return has_flag and (row.raw.get("review_flag") or "").strip() not in (
            "",
            "none",
        )

    disagreements = [m for m in result.matched if not m.view_agree and not m.abstain]
    model_index = {(r.firm_key, r.leaf): r for r in result.model_rows}
    near_flagged = 0
    for key, candidates in result.near_leaf.items():
        if any(
            flagged(model_index[(key[0], c.model_leaf)])
            for c in candidates
            if (key[0], c.model_leaf) in model_index
        ):
            near_flagged += 1

    return {
        "review_flag_column_present": has_flag,
        "disagreements_total": len(disagreements),
        "disagreements_flagged": sum(1 for m in disagreements if flagged(m.model)),
        "model_only_total": len(result.model_only),
        "model_only_flagged": sum(1 for r in result.model_only if flagged(r)),
        "misses_with_flagged_near_leaf": near_flagged,
    }


# --------------------------------------------------------------------------- #
# Quote-verbatim spot check (best-effort)
# --------------------------------------------------------------------------- #

# Full Commentary is assembled as "{reasoning} Evidence: {quote}. Locator:
# {where}." optionally followed by " Checker: ..." / " Corroborated by ...". We
# parse the evidence + locator back out to re-run the deterministic check. The
# quote itself may contain periods, so we anchor on ". Locator:".
_EVIDENCE_RE = re.compile(
    r"Evidence:\s*(?P<quote>.*?)\.\s*Locator:\s*(?P<locator>.*?)\.(?:\s|$)",
    re.DOTALL,
)
# Dial-grid cues in the EVIDENCE text — the model reads these off a colored
# dial, so the strict verbatim prose path does not apply. Only grid-read markers
# count; a prose sentence that merely CITES "(Chart 3)" is still prose, so
# generic "chart" is deliberately excluded here.
_EVIDENCE_VISUAL_CUES = re.compile(
    r"\b(dot|arrow|grid|dashboard|overweight bar|view list|"
    r"green (?:long|positive)|red (?:short|negative)|yellow neutral)\b",
    re.IGNORECASE,
)
# A table/figure LOCATOR (per the plan, table/visual locators must name the
# specific artifact — "p.5 — 'China' forecast table"). A bare "p.N" locator is
# prose. The source-title parenthetical is stripped before this test, so these
# words only appear when the locator genuinely names a grid/table/figure.
_LOCATOR_TABLE_CUES = re.compile(
    r"(—|-\s'|table|grid|figure|forecast|dashboard|view list|chart)",
    re.IGNORECASE,
)


def _slugify_firm(firm: str) -> str:
    lowered = unicodedata.normalize("NFKC", firm).lower()
    return re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")


def _work_dir(run_dir: Path) -> Path:
    return PROJECT_ROOT / "work" / run_dir.name


def _firm_snapshots(run_dir: Path) -> dict[str, tuple[str, frozenset[int]]]:
    """Map a firm slug to its combined snapshot text and scrambled-page union.

    A firm may span several sources (grouped pairs), so snapshots are
    concatenated. Returns {} when the work dir is absent."""
    work = _work_dir(run_dir)
    if not work.is_dir():
        return {}
    by_firm: dict[str, list[str]] = {}
    scrambled: dict[str, set[int]] = {}
    for source_dir in sorted(work.iterdir()):
        snapshot = source_dir / "snapshot.txt"
        if not snapshot.is_file():
            continue
        # Firm slug is the source-dir prefix; match the longest known firm slug.
        firm_slug = source_dir.name
        by_firm.setdefault(firm_slug, []).append(snapshot.read_text(encoding="utf-8"))
        meta = source_dir / "ingest_meta.json"
        pages: set[int] = set()
        if meta.is_file():
            try:
                pages = set(json.loads(meta.read_text()).get("scrambled_pages") or [])
            except (json.JSONDecodeError, OSError):
                pages = set()
        scrambled.setdefault(firm_slug, set()).update(pages)
    return {
        slug: ("\n".join(texts), frozenset(scrambled.get(slug, set())))
        for slug, texts in by_firm.items()
    }


def _match_firm_snapshot(
    firm: str, snapshots: dict[str, tuple[str, frozenset[int]]]
) -> tuple[str, frozenset[int]] | None:
    """Find the source-dir(s) whose slug the firm slug prefixes."""
    firm_slug = _slugify_firm(firm)
    combined: list[str] = []
    pages: set[int] = set()
    for slug, (text, scrambled) in snapshots.items():
        if slug.startswith(firm_slug):
            combined.append(text)
            pages.update(scrambled)
    if not combined:
        return None
    return "\n".join(combined), frozenset(pages)


def _reconstruct_candidate(row: Row) -> CandidateCall | None:
    """Rebuild the minimal CandidateCall fields the evidence check reads, parsed
    from Full Commentary. Returns None when the commentary is unparseable."""
    match = _EVIDENCE_RE.search(row.commentary)
    if not match:
        return None
    quote = match.group("quote").strip()
    locator = match.group("locator").strip()
    if not quote:
        return None
    # A parenthetical locator source ("p.2 (Our multi-asset ...)") is appended by
    # assemble; keep only the leading locator token for the page parse.
    locator = re.split(r"\s*\(", locator, maxsplit=1)[0].strip() or locator
    spans = tuple(part.strip() for part in quote.split(" ... ") if part.strip())
    if not spans:
        return None
    is_visual = bool(
        _EVIDENCE_VISUAL_CUES.search(quote) or _LOCATOR_TABLE_CUES.search(locator)
    )
    evidence_kind = "table" if is_visual else "prose"
    return CandidateCall(
        source_id=row.firm_key,
        chunk_id="",
        sub_asset_raw="",
        sub_asset_class=row.leaf,
        taxonomy_match="exact",
        view=row.view if row.view in VIEW_CODES else "UNCERTAIN",
        call_language="none",
        evidence_kind=evidence_kind,
        evidence_spans=spans,
        locator=locator,
        reasoning="",
    )


def quote_spot_check(result: EvalResult) -> dict:
    """Best-effort re-verification of each output row's evidence against the
    frozen snapshots, using `confidence.evidence_passes` (imported, not
    reimplemented). Skips cleanly when snapshots are absent.

    evidence_kind is inferred heuristically from the commentary text (the raw
    candidate schema is not persisted to the run), so this is an approximate
    signal for analyst triage, not an authoritative re-score."""
    snapshots = _firm_snapshots(result.run_dir)
    if not snapshots:
        return {
            "ran": False,
            "note": f"no work snapshots found under {_work_dir(result.run_dir)}",
        }
    counts = {"passed": 0, "failed": 0, "unparseable": 0, "no_snapshot": 0}
    failures: list[dict] = []
    for row in result.model_rows:
        candidate = _reconstruct_candidate(row)
        if candidate is None:
            counts["unparseable"] += 1
            continue
        snapshot = _match_firm_snapshot(row.firm, snapshots)
        if snapshot is None:
            counts["no_snapshot"] += 1
            continue
        text, scrambled = snapshot
        check = confidence.evidence_passes(candidate, text, scrambled_pages=scrambled)
        if check.passed:
            counts["passed"] += 1
        else:
            counts["failed"] += 1
            failures.append(
                {
                    "firm": row.firm,
                    "leaf": row.leaf,
                    "evidence_kind": candidate.evidence_kind,
                    "reason": check.reason_code,
                    "locator": candidate.locator,
                }
            )
    return {
        "ran": True,
        "note": (
            "best-effort: evidence_kind inferred from commentary text; the raw "
            "candidate schema is not persisted to the run"
        ),
        "counts": counts,
        "failures": failures,
    }


# --------------------------------------------------------------------------- #
# Output rendering
# --------------------------------------------------------------------------- #

WORKSHEET_COLUMNS = (
    "kind",
    "firm",
    "leaf",
    "model_view",
    "gt_view",
    "review_flag",
    "near_leaf_hint",
    "commentary",
    "judgment",
    "notes",
)


def _worksheet_rows(result: EvalResult) -> list[dict]:
    """One row per gt_only + model_only + view-disagreement, with empty
    judgment/notes columns for the downstream human/agent pass."""
    rows: list[dict] = []

    for miss in result.gt_only:
        candidates = result.near_leaf.get((miss.firm_key, miss.leaf), [])
        hint = "; ".join(
            f"{c.model_leaf} ({c.model_view}, sim={c.similarity})" for c in candidates
        )
        rows.append(
            {
                "kind": "gt_only",
                "firm": miss.firm,
                "leaf": miss.leaf,
                "model_view": "",
                "gt_view": miss.view,
                "review_flag": "",
                "near_leaf_hint": hint,
                "commentary": miss.commentary,
                "judgment": "",
                "notes": "",
            }
        )

    for pair in result.matched:
        if pair.view_agree or pair.abstain:
            continue
        rows.append(
            {
                "kind": "view_disagreement",
                "firm": pair.firm,
                "leaf": pair.leaf,
                "model_view": pair.model_view,
                "gt_view": pair.gt_view,
                "review_flag": (pair.model.raw.get("review_flag") or "").strip(),
                "near_leaf_hint": "",
                "commentary": pair.model.commentary,
                "judgment": "",
                "notes": "",
            }
        )

    for extra in result.model_only:
        rows.append(
            {
                "kind": "model_only",
                "firm": extra.firm,
                "leaf": extra.leaf,
                "model_view": extra.view,
                "gt_view": "",
                "review_flag": (extra.raw.get("review_flag") or "").strip(),
                "near_leaf_hint": "",
                "commentary": extra.commentary,
                "judgment": "",
                "notes": "",
            }
        )
    return rows


def _buckets_json(result: EvalResult) -> dict:
    def match_dict(m: MatchedPair) -> dict:
        return {
            "firm": m.firm,
            "leaf": m.leaf,
            "model_view": m.model_view,
            "gt_view": m.gt_view,
            "view_agree": m.view_agree,
            "abstain": m.abstain,
            "review_flag": (m.model.raw.get("review_flag") or "").strip(),
        }

    def model_dict(r: Row) -> dict:
        return {
            "firm": r.firm,
            "leaf": r.leaf,
            "view": r.view,
            "review_flag": (r.raw.get("review_flag") or "").strip(),
            "commentary": r.commentary,
        }

    def gt_dict(r: Row) -> dict:
        candidates = result.near_leaf.get((r.firm_key, r.leaf), [])
        return {
            "firm": r.firm,
            "leaf": r.leaf,
            "view": r.view,
            "commentary": r.commentary,
            "near_leaf": [
                {
                    "model_leaf": c.model_leaf,
                    "model_view": c.model_view,
                    "similarity": c.similarity,
                }
                for c in candidates
            ],
        }

    return {
        "exact_match": [match_dict(m) for m in result.matched],
        "model_only": [model_dict(r) for r in result.model_only],
        "gt_only": [gt_dict(r) for r in result.gt_only],
    }


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def render_report(result: EvalResult) -> str:
    head = headline_metrics(result)
    firm_table = per_firm_table(result)
    flags = review_flag_analysis(result)

    lines: list[str] = []
    lines.append(f"# Evaluation — {result.run_dir.name} vs ground truth")
    lines.append("")
    lines.append(
        "Deterministic ground-truth comparison (no LLM calls). Buckets use the "
        "pilot-05 phase-1 vocabulary: `exact_match` (firm+leaf join), "
        "`model_only`, `gt_only`. Firm names are normalized before joining."
    )
    lines.append("")
    lines.append("## Headline numbers")
    lines.append("")
    lines.append(f"- Ground-truth rows: **{head['gt_total']}**")
    lines.append(f"- Model rows: **{head['model_total']}**")
    lines.append(
        f"- `exact_match`: **{head['exact_match']}** "
        f"({head['view_agree']} view-agree, {head['view_disagree']} disagree, "
        f"{head['abstain_uncertain']} UNCERTAIN/abstain)"
    )
    lines.append(f"- `model_only`: **{head['model_only']}**")
    lines.append(f"- `gt_only` (missed calls): **{head['gt_only']}**")
    lines.append(
        f"- Raw leaf-match recall: **{head['raw_recall']['n']}/"
        f"{head['raw_recall']['d']} ({head['raw_recall']['pct']}%)**"
    )
    lines.append(
        f"- View-agreement among decided matches (UNCERTAIN excluded): "
        f"**{head['view_agreement_among_decided']['n']}/"
        f"{head['view_agreement_among_decided']['d']} "
        f"({head['view_agreement_among_decided']['pct']}%)**"
    )
    lines.append("")

    lines.append("## Per-firm breakdown")
    lines.append("")
    lines.append(
        _md_table(
            ["Firm", "GT", "Model", "Matched", "Agree", "Disagree", "Abstain", "Model-only", "GT-only", "Recall%"],
            [
                [
                    row["firm"],
                    str(row["gt_total"]),
                    str(row["model_total"]),
                    str(row["matched"]),
                    str(row["view_agree"]),
                    str(row["view_disagree"]),
                    str(row["abstain"]),
                    str(row["model_only"]),
                    str(row["gt_only"]),
                    f"{row['recall_pct']}",
                ]
                for row in firm_table
            ],
        )
    )
    lines.append("")

    lines.append("## UNCERTAIN as abstain / coverage")
    lines.append("")
    abstain = head["abstain_uncertain"]
    lines.append(
        f"{abstain} matched model rows are `UNCERTAIN` — scored as abstain, "
        "neither right nor wrong (ground truth carries no UNCERTAIN). They are "
        "excluded from the view-agreement denominator above."
    )
    lines.append("")

    lines.append("## View disagreements (matched leaf, opposite call)")
    lines.append("")
    disagreements = [m for m in result.matched if not m.view_agree and not m.abstain]
    if disagreements:
        lines.append(
            _md_table(
                ["Firm", "Leaf", "Model", "GT", "Review flag"],
                [
                    [
                        m.firm,
                        m.leaf,
                        m.model_view,
                        m.gt_view,
                        (m.model.raw.get("review_flag") or "").strip() or "none",
                    ]
                    for m in disagreements
                ],
            )
        )
    else:
        lines.append("_None._")
    lines.append("")

    lines.append("## Missed calls (`gt_only`)")
    lines.append("")
    lines.append(
        "Every ground-truth call the pipeline did not emit under the same leaf. "
        "A miss is likely costlier than a wrong call, so this is the primary "
        "review list. Near-leaf column lists same-firm, agreeing-view model rows "
        "on a *different* leaf (a suggestion, never an auto-match)."
    )
    lines.append("")
    if result.gt_only:
        miss_rows = []
        for miss in sorted(result.gt_only, key=lambda r: (r.firm.lower(), r.leaf)):
            candidates = result.near_leaf.get((miss.firm_key, miss.leaf), [])
            hint = "; ".join(
                f"{c.model_leaf} ({c.model_view}, {c.similarity})" for c in candidates[:3]
            )
            miss_rows.append([miss.firm, miss.leaf, miss.view, hint or "—"])
        lines.append(_md_table(["Firm", "Leaf", "GT view", "Near-leaf hint"], miss_rows))
    else:
        lines.append("_None._")
    lines.append("")

    lines.append("## Review-flag hit analysis")
    lines.append("")
    if flags["review_flag_column_present"]:
        lines.append(
            f"- View disagreements on review-flagged rows: "
            f"**{flags['disagreements_flagged']}/{flags['disagreements_total']}**"
        )
        lines.append(
            f"- `model_only` rows on review-flagged rows: "
            f"**{flags['model_only_flagged']}/{flags['model_only_total']}**"
        )
        lines.append(
            f"- Missed calls with a review-flagged near-leaf suggestion: "
            f"**{flags['misses_with_flagged_near_leaf']}/{head['gt_only']}**"
        )
    else:
        lines.append("_No `review_flag` column in this run's output._")
    lines.append("")

    lines.append("## Column distributions")
    lines.append("")
    for column in ("band", "basis", "checker_strength"):
        dist = column_distribution(result.model_rows, column)
        if dist is None:
            lines.append(f"- `{column}`: not present in this run.")
        else:
            rendered = ", ".join(f"{k}={v}" for k, v in dist.items())
            lines.append(f"- `{column}`: {rendered}")
    lines.append("")

    lines.append("## Quote-verbatim spot check")
    lines.append("")
    spot = result.spot_check
    if not spot.get("ran"):
        lines.append(f"_Skipped — {spot.get('note', 'no snapshots')}._")
    else:
        counts = spot["counts"]
        lines.append(f"_{spot['note']}._")
        lines.append("")
        lines.append(
            f"- Passed: **{counts['passed']}**, Failed: **{counts['failed']}**, "
            f"Unparseable commentary: {counts['unparseable']}, "
            f"No snapshot: {counts['no_snapshot']}"
        )
        if spot["failures"]:
            lines.append("")
            lines.append(
                _md_table(
                    ["Firm", "Leaf", "Kind", "Reason", "Locator"],
                    [
                        [
                            f["firm"],
                            f["leaf"],
                            f["evidence_kind"],
                            f["reason"],
                            f["locator"],
                        ]
                        for f in spot["failures"]
                    ],
                )
            )
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def run_eval(
    run_dir: Path,
    ground_truth: Path,
    *,
    taxonomy: Taxonomy | None = None,
) -> EvalResult:
    """Load inputs, build buckets, run the spot check. Pure — writes nothing."""
    output_csv = run_dir / "output.csv"
    if not output_csv.is_file():
        raise EvalError(f"run output not found: {output_csv}")
    if not ground_truth.is_file():
        raise EvalError(f"ground truth not found: {ground_truth}")

    model_rows = _read_rows(output_csv, commentary_column="Full Commentary")
    gt_rows = _read_rows(ground_truth, commentary_column="Full Commentary")

    result = build_eval(model_rows, gt_rows, run_dir)
    result.spot_check = quote_spot_check(result)
    return result


def write_eval_outputs(result: EvalResult, out_dir: Path) -> dict[str, Path]:
    """Write eval-report.md, eval-buckets.json, judgment-worksheet.csv."""
    out_dir.mkdir(parents=True, exist_ok=True)

    report_path = out_dir / "eval-report.md"
    report_path.write_text(render_report(result), encoding="utf-8")

    buckets_path = out_dir / "eval-buckets.json"
    buckets_path.write_text(
        json.dumps(_buckets_json(result), indent=1, ensure_ascii=False),
        encoding="utf-8",
    )

    worksheet_path = out_dir / "judgment-worksheet.csv"
    with worksheet_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=WORKSHEET_COLUMNS)
        writer.writeheader()
        writer.writerows(_worksheet_rows(result))

    return {
        "report": report_path,
        "buckets": buckets_path,
        "worksheet": worksheet_path,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.eval",
        description="Deterministic ground-truth comparison for a frozen run.",
    )
    parser.add_argument(
        "--run",
        required=True,
        type=Path,
        help="run directory containing output.csv (e.g. runs/pilot-06)",
    )
    parser.add_argument(
        "--ground-truth",
        required=True,
        type=Path,
        help="ground-truth CSV (e.g. ground-truth/pilot-ground-truth.csv)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output directory (default: <run>/eval)",
    )
    args = parser.parse_args(argv)

    # Load the taxonomy for its structural contract check; leaf matching is
    # exact-string, so validation is advisory here (surfaced, not fatal).
    taxonomy = load_taxonomy()
    result = run_eval(args.run, args.ground_truth, taxonomy=taxonomy)
    out_dir = args.out or (args.run / "eval")
    written = write_eval_outputs(result, out_dir)

    head = headline_metrics(result)
    print(
        f"{result.run_dir.name}: {head['exact_match']} exact "
        f"({head['view_agree']} agree / {head['view_disagree']} disagree / "
        f"{head['abstain_uncertain']} abstain), "
        f"{head['model_only']} model_only, {head['gt_only']} gt_only; "
        f"recall {head['raw_recall']['pct']}%"
    )
    for label, path in written.items():
        print(f"  {label}: {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
