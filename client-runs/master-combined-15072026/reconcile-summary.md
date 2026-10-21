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

## Addendum — 145b re-ingest fold-in (2026-07-15)

Four 145-batch sources that failed ingest upstream (empty work dirs, zero kept
rows, recorded as `ingest_error`) were re-run end-to-end as `145b-retry1` (bare
model matrix; no flags) and surgically integrated here. **The full 98b+145b
cross-batch reconcile was NOT re-run** — that would re-invoke the nondeterministic
scope gate and silently discard the four human overrides in `review-decisions.md`.
Integration was append-only.

Re-ingest outcome (probed first with a browser UA; three of the four original
failures were transient DNS `NAME_NOT_RESOLVED` blips):

- **Nomura** (IWM Q2 2026 Outlook) — ingested (native 28p PDF), **34 kept rows**,
  document date 24/04/2026 (PDF-metadata CreationDate; native download, so the
  browser print-capture guard does not apply).
- **Quintet Private Bank** (Counterpoint 2026 Mid-Year) — ingested via a
  same-command mini-run (`145b-retry1-quintet`, merged per the split-8 HSBC
  precedent) after the Playwright fetch hit the same intermittent local-DNS flap;
  **11 kept rows**, date 08/06/2026 (HTML htmldate).
- **Commerce Trust** (2026 Midyear podcast page) — ingested (HTML → printed PDF),
  **0 calls** (podcast landing page, no transcript/allocation content). No output
  or failure rows; its `ingest_error` is resolved.
- **Carlyle** (Midyear Outlook Whitepaper) — **still un-fetchable**: the PDF sits
  behind a Cloudflare bot-challenge (HTTP 403, "Attention Required") that defeats
  both curl and the headless-chromium fallback. Not a transient DNS case; left as
  an `ingest_error` failure row. Flagged for the client (needs a client-supplied
  copy or an allowlisted fetch).

Integration mechanics:

- Overlap check (src.eval firm normalization): none of the four retry firms had
  any pre-existing rows in the reviewed master → true no-overlap case.
- `src.reconcile --near-leaf` was run over **only** `145b-retry1/output.dated.csv`
  (45 rows). Result: 45 → 45, **0 merged, 0 superseded, 0 needs_human**; the
  near-leaf pass judged 3 clusters (Nomura Japan/Australia & short/long govt
  bonds; Quintet equities family) all **distinct/kept, review_required=false**.
  Nothing intra-retry required human review.
- The 45 reconciled rows were appended at the end of `output.csv` (the file is
  input-concatenation-ordered, 98b then 145b; retry1 is effectively a
  "split-15" after split-14 Zurich). The first 3,630 rows are byte-for-byte
  unchanged — every human-review decision is intact.
- `failures-client.csv`: the three resolved `ingest_error` rows (Commerce,
  Nomura, Quintet) removed; Carlyle's kept; Nomura's 3 new dropped-candidate
  rows inserted into their existing label groups (block-order preserving).

Resulting master counts: **output.csv 3,630 → 3,675** (+34 Nomura, +11 Quintet);
**failures-client.csv 1,386 → 1,386** (−3 resolved ingest_errors + 3 new Nomura
housekeeping rows; Carlyle's retry ingest-failure deduped against the existing
Carlyle row). Pre-fold master files preserved as `*.pre-retry1.csv`.

## Addendum — 145b-retry2 clean-copy fold-in (2026-07-16)

Nine 145-batch firms that produced **zero kept calls in the original run** (bad
ingest: scrambled/visual-heavy pages, blocked or wrong-content links — their
digests had been routed to `digests-all/_zero_call_no_page/`) were re-run
end-to-end as `145b-retry2` from clean, manually-sourced local copies (10 PDFs +
2 `.txt` transcripts in `145b-retry2/manual-sources/`; the CSV's `local_file`
paths were rewired repo-relative so they resolve against `PROJECT_ROOT`, original
preserved as `*.as-provided.csv`). Bare model matrix, no flags. Two scout groups
accepted and applied: **Apollo** (Academy + Institutional → one publication) and
**Insight Investment** (Monthly + Quarterly fixed-income → one combined source).
**As with retry1, the full 98b+145b cross-batch reconcile was NOT re-run** — that
would re-invoke the nondeterministic scope gate and discard the five human
overrides in `review-decisions.md`. Integration was append-only.

Re-ingest outcome: **all 9 firms recovered** (none zero-call again). Run: 155
candidates → **93 kept**, all 11 sources emitted candidates. Datefill filled 5 of
8 undated sources (Apollo 30/06, GCM 22/06 landing pages; VLK 05/06, Payden
02/07, Royal London 02/07 native-PDF metadata); Fiera and Matthews left blank
(image-only / out-of-window discards — acceptable). CCLA and Insight were dated
in-run.

Integration mechanics:

- Overlap check (`src.eval` firm normalization): none of the 9 firms had any
  pre-existing rows in the reviewed master → true no-overlap case, as retry1.
- `src.reconcile --near-leaf` was run over **only** `145b-retry2/output.dated.csv`
  (93 rows). Result: 93 → **92** rows. Exact-leaf pass: **0 multi-row keys**
  (0 merged / 0 superseded / 0 needs_human). Near-leaf pass: 5 clusters, **1
  same-view merge** (Van Lanschot Kempen — an AI-theme O row folded onto the
  `IT/Tech/Telecomms (inc. AI)` leaf, same doc, same overweight), 0 cross-view
  supersessions, 0 needs_human. Nothing intra-retry required human review.
- The 92 reconciled rows were appended at the end of `output.csv`; the first
  3,675 rows are **byte-for-byte unchanged** (verified: prefix `cmp`-identical) —
  every human-review decision intact.
- `failures-client.csv`: one now-resolved stale row dropped — **Payden & Rygel /
  EM Debt - General / "Evidence could not be verified visually"** (a dropped O
  candidate from the bad-ingest run, now directly superseded by the clean run's
  kept `EM Debt - General | O` call). 16 new dropped-candidate rows inserted in
  label-block order (15 from the run: 9 "Change too small to call", 4 "Duplicate
  — same call, related asset", 1 "Evidence is about a different asset", 1
  "Skipped — asset not on the list"; + 1 "Merged — same view on a closely related
  asset" from the near-leaf merge). No ingest_error rows existed for these firms
  (Carlyle remains the only "Document could not be ingested" row).

Resulting master counts: **output.csv 3,675 → 3,767** (+92); **failures-client.csv
1,386 → 1,401** (−1 resolved + 16 retry2). Reader deliverable: the 11 retry2
digests moved to `digests-all/` top level (`145b-retry2__*`), the 11 superseded
zero-call digests removed from `_zero_call_no_page/` (Commerce Trust stays — not a
retry2 firm, still zero-call). Firm pages regenerated for the 9 new firms only
(bare default, empty crosscheck); coverage now **150 pages == 150 output firms**,
exact 1:1. Binder re-bound to `firm-summaries.docx`. Pre-fold master files
preserved as `*.pre-retry2.csv`. Carlyle + Commerce Trust remain correctly absent.
