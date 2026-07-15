# Firm-reconcile summary

Post-run firm-reconcile stage (v1.2 item 1 — the client's dual-confidence two-pass design). Deterministic join on `src.eval`-normalized firm + sub-asset leaf; an LLM scope gate classifies each multi-row key as the same claim or distinct claims, then all merge/precedence decisions are deterministic code. Never a forced call, never a majority vote.

## Inputs

- client-runs/runs-07072026-98rows/98b-combined/output.dated.csv
- client-runs/runs-13072026-145rows/145b-combined/output.dated.csv

## Row totals

- input rows: 3801
- reconciled output rows: 3631
- rows removed (merged/superseded away): 170

## Keys

- single-row keys (passed through untouched): 3517
- multi-row keys: 133
  - all-same-view: 93
  - conflicting views: 40

Sanity anchor: the frozen crosscheck report over the same inputs found 61 keys (39 same-view, 22 conflicting). Any drift from those numbers is expected only if the underlying outputs changed since that report.

## Per-action row counts

- `winner`: 99
- `merged`: 104
- `superseded`: 7
- `kept_distinct`: 72
- `needs_human`: 2

## Needs-human keys

- **Triodos Investment Management / Oil** (views: U | UNCERTAIN) — Both center on the same forecast—"Oil prices peak in Q2 and return to pre-conflict levels"—differing only in whether the disruption risk is treated as decisive.

## Human review applied — 2026-07-15

All cross-view supersessions and the needs-human key were reviewed against the
member rows' full commentary (two verified against the live sources). 15
supersessions accepted; 4 overridden (Carmignac Europe Equities → UNCERTAIN,
AllianceBernstein Duration → U, Lion Global → Duration U, State Street Japan
Duration → U); Triodos/Oil resolved to U. Final counts: 3,630 output rows,
1,386 client failure rows. Full rationale: `review-decisions.md`; pre-review
files: `*.pre-human-review.csv`. The audit CSVs above are the unmodified
machine record and no longer reflect the four overridden picks.

## Near-leaf pass (Phase 3)

A second deterministic-candidate + LLM-partition pass over the exact-reconciled rows: same-firm related leaves are clustered by two bounded lexical lanes, an LLM groups each cluster's rows into collective calls (merged onto a validated canonical leaf) vs distinct calls (kept), and any contract violation fails closed to needs_human. Every near-leaf survivor is flagged for review in this first run.

- near-leaf candidate pairs: 831
- clusters judged: 262
- rows merged away (same-view): 44
- rows superseded (cross-view collective pick): 15
- clusters kept fully separate: 209
- clusters failed closed to needs_human: 0
- broad/specific coverage advisory rows: 183

## Scope — what this stage does NOT do

- **Cross-firm volume never decides a mapping.** Broad/specific volume is a standalone advisory (`taxonomy-coverage-review.csv`); the canonical leaf is chosen from commentary evidence, never by row counts.
- **No LLM-invented numbers.** Every merge, precedence, and near-leaf decision is deterministic code over categorical LLM judgments; the model never invents a label, a number, or a surviving row. Any LLM failure degrades that key/cluster to needs_human.
