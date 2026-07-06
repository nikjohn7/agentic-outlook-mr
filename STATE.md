# Markets Recon / Allocator Pro POC — State

_Last updated: 2026-07-06_

## Current State

POC for Markets Recon / Allocator Pro: ingest fund/asset-manager outlook sources
(PDF/HTML) and produce reviewable sub-asset-class allocation calls (`O`/`N`/`U`/
`UNCERTAIN`) with citations, against the locked taxonomy in `excel-file/Asset
Class List - Locked.csv`. Full spec: `CLAUDE.md`, `POC_CONTEXT.md`,
`WORKBOOK_SCHEMA.md`, `POC_PLAN.md`. Deterministic joins, lookups, and
arithmetic (taxonomy, confidence, assembly) never route through a model;
Haiku/Sonnet/Opus tiers are reserved for judgment-heavy extraction and review.

The deterministic spine is `src/taxonomy.py` (exact 396-leaf validation +
`grouped_block()` for prompt injection), `src/schemas.py` (LLM candidate
contract), `src/confidence.py` (quote/table/visual evidence checks + rubric
scoring, incl. per-source read-quality floors: PDF chars/page, HTML snapshot
length, and a scrambled-page prose fallback that degrades to the key-token
check with a confidence cap + review flag), `src/assemble.py`
(`output.csv`/`failures.csv`/`manifest.md`), `src/ingest.py` (thin
pilot/source loading, snapshots, chunk boundaries, visual-heavy detection,
deterministic scrambled-page (column-interleave) detection, Playwright
print-to-PDF capture of visual-heavy HTML),
and `src/llm.py` (swappable headless `claude -p` / `codex exec`, with
`{{name}}` template-var injection). Output rows are the 10 workbook columns
plus `confidence`, `band`, and `review_flag`; one-hot columns are
intentionally omitted. A `.claude/settings.json` hook blocks git commits
containing Claude/Anthropic self-attribution — commit messages stay plain.

The pipeline runs up to four LLM steps, each with explicit per-step
engine/model/effort flags (codex pinned to `gpt-5.5`; claude requires an
explicit model): analyze (per-chunk extraction via `prompts/analyze_chunk.md`:
injected taxonomy + conventions + brain examples + rolling `memory.md` +
native chunk), a second-reader checker (`prompts/check_candidates.md`, one
call per source, categorical verdicts feeding the deterministic rubric —
never a self-confidence number; any `fail` verdict hard-fails the candidate,
anything short of all-pass caps confidence at 74, so High means a second
model confirmed the evidence; default codex/gpt-5.5/high), a conflict arbiter
(`prompts/arbitrate_conflict.md`, fires only on surviving view conflicts;
default codex/gpt-5.5/medium), and a group-notes resolver
(`prompts/resolve_groups.md`, only when `--group-notes` supplies analyst
free-text pairing notes; default codex/gpt-5.5/low). The normative house
rules live in `prompts/conventions.md`, injected into analyze, checker, and
arbiter alike; `brain.md` carries worked examples + reasoning style,
analyze-only. Both engine CLIs read PDF pages visually (codex renders pages
to PNG itself), so engine routing is unconstrained by source type. All
prompts are indexed in `prompts/REGISTRY.md`. The pilot set
(`prev-excel/pilot.csv`) is now 7 docs / 2 grouped pairs (Schroders
review+outlook, JPM GFICC+GAA). Blind pilots are frozen in `runs/`
(gitignored; frozen runs are force-added when committed): `pilot-01`/`pilot-02`
(analyze only, original 5 sources — pilot-01's 12 false `quote_not_found`
failures drove the `evidence_kind: visual` tagging rule and print-to-PDF ingest
of visual-heavy HTML), `pilot-03` (first with checker + arbiter), `pilot-04`
(first with grouping), and `pilot-05` (first over all 7 docs / 2 groups, and
first with providers swapped: codex/gpt-5.5/high analyze, claude checker/
arbiter/grouper). `POC_PLAN.md` locks the 3-phase build order and LLM-native
ingestion design. A `.venv` holds `pdfplumber`, `pdfminer.six`,
`trafilatura`, `htmldate`, `playwright` (+ chromium). 95 unittests pass.

## Recent Changes

- 2026-07-06: User replaced `prev-excel/PIMCO.pdf` with the correct/full
  Cyclical Outlook source (the previous file was the 2-page infographic only).
  This makes the 5 PIMCO GT rows judged "not grounded in the ingested source"
  in the pilot-05 comparison in-scope for the next run, and largely resolves
  client question 5 (PIMCO source scope) in
  `runs/pilot-05/gt-comparison.md`. No re-run yet.
- 2026-07-06: Ran the pilot-05 GT comparison (deterministic firm+leaf join +
  five parallel per-firm judgment agents verifying against the ingested
  sources; report `runs/pilot-05/gt-comparison.md`, row-level JSONs in
  `runs/pilot-05/gt-judgments/`). Recall 53/82 (65%, from 51% in pilot-04;
  70% excluding 6 PIMCO GT rows not grounded in the ingested 2-pager —
  GT authored from the full Cyclical Outlook article). All 6 view
  disagreements judged convention disputes (mostly table-vs-prose), none a
  reading error; all 24 JPM GAA dial signs verified correct; Schroders
  28/28 again. Of 66 unmatched model rows: 38 defensible GT omissions, 18
  convention disputes, 10 overreaches (8 pass at 75/High because the
  forecast-delta convention has no materiality floor — AB 4-14bp deltas;
  the rubric's 5 review flags correctly caught the prose-soft rows).
  Dominant remaining miss cause is analyst-style inference depth (20/29),
  a scope question, not a bug. New client questions: delta materiality
  floor + prose-over-table precedence, inference-depth scope, dial level
  policy, PIMCO source scope.
- 2026-07-06: Ran pilot-05 blind — the provider-swap run over all 7 docs
  (analyze codex/gpt-5.5/high, checker claude/opus/medium, arbiter
  claude/sonnet/high, grouper claude/sonnet/medium, `--group-notes`). Single-
  chunk smoke first (JPM GFICC, codex/high): 5 candidates, first attempt, no
  repair — codex feeds the span-list schema natively. Grouper resolved both
  pairs with zero warnings. 131 candidates → 119 kept, 12 failed (10
  `duplicate_same_view` cross-doc dedups within the two groups, 1
  `checker_sign_mismatch`, 1 `quote_not_found`); count check pass, 0 chunk
  failures. Breadth up sharply vs pilot-04's 48 kept: Aberdeen now emits 4
  (was 0 — priority-1 recall gap closed), AB 30, Schroders multi-asset 38, JPM
  GAA 47 (the previously-uningested combined source), Schroders review-alone
  still 0 (correct). Views O 59 / N 31 / U 29; bands High 114 / Medium 5;
  5 review-flagged (all conf 74). Two non-dedup failures for analyst review:
  AB JPY=N killed by opus checker reading USD/JPY 155→145 as monotonic yen
  appreciation (→O, not two-sided-nets-to-N); JPM Short-Dated Bonds=O
  `quote_not_found` (scrambled-page family, not rescued this run). Frozen on
  disk at `runs/pilot-05/`; not yet committed.
- 2026-07-05: Closed the JPM combined-source scope gap from the GT evaluation:
  added a 7th pilot row ("Global Asset Allocation Views 2Q 2026", 4/30/2026,
  local PDF in `prev-excel/`), a group note pairing it with the GFICC doc
  (mirrors the Schroders pair), and title-disambiguated JPM entries in
  `_pilot_local_pdf_for` (firm-only mapping would have sent both JPM rows to
  `jp-morgan.pdf`). Pilot is now 7 docs / 2 groups. Tests updated (source
  limit 7, JPM pair mapping); 95 pass. The recorded MR URL ends in "/c"
  (likely truncated) — metadata only, the local PDF is ingested.
- 2026-07-05: First ground-truth evaluation completed. User authored
  `ground-truth/pilot-ground-truth.csv` (82 rows, all 5 pilot firms); a codex
  agent ran the hybrid comparison against `runs/pilot-04-rescored/` (results
  condensed in `runs/pilot-04-rescored/gt-comparison.md`). Direction accuracy
  is excellent: 42/50 model rows align with GT, zero opposite-sign errors,
  and the rubric's review flags caught the one overreach. Recall is the gap
  (42/82 raw): 14 misses from Aberdeen emitting zero candidates, 16 from
  table/infographic breadth (AB forecast table, PIMCO implication rows), 5
  from JPM extraction breadth, and 5 because GT pairs JPM's GFICC doc with
  "Global Asset Allocation Views 2Q 2026", which the pilot never ingested
  (Schroders-style combined source). The grouped Schroders pair scored
  28/28. Seven near-leaf disputes cluster on broad-vs-specific leaf snapping
  (needs a convention decision).
- 2026-07-05: Built `runs/pilot-04-rescored/` — the GT-comparison artifact for
  the pilot-04 review. Both pilot-04 `quote_not_found` failures were re-scored
  deterministically (no LLM calls; script + provenance README in the artifact)
  under the new gates and pass: AB Euro Govt Bonds `N` via multi-span (2 spans,
  clean p.7, strict verbatim path) and JPM Short-Dated US Treasuries `O` via
  the scrambled-page fallback (p.2), both at 74/Medium/review (checker verdicts
  were not persisted by the run, so the checker-unconfirmed cap applies; the
  README documents all reconstruction assumptions). The 48 frozen rows are
  preserved verbatim; rescued rows are inserted in their firm blocks;
  `failures.csv` is header-only.
- 2026-07-05: Ran the grouping live test blind (`pilot-04`, claude/opus/medium
  + checker/arbiter/grouper on codex/gpt-5.5) with `--group-notes
  prev-excel/group-notes.md`. The Schroders pair grouped with no warnings
  (review doc alone: 0 candidates; outlook doc: 32). 50 candidates → 48 kept,
  2 `quote_not_found`, 0 chunk failures, count check pass. Aberdeen emitted 0
  candidates. Frozen at `runs/pilot-04/`. The two failures were diagnosed as
  an honest elided quote (AB) and a column-interleaved text layer (JPM) —
  drove the multi-span and scrambled-page fixes below.
- 2026-07-05: Added deterministic scrambled-page detection to rescue prose
  calls on column-interleaved PDF pages (the JPM pilot-04 JPM
  `quote_not_found`: pdfplumber merges the two columns line-by-line, so no
  contiguous quote of the rendered page survives the verbatim check).
  `src/ingest.detect_scrambled_page` flags a page (1-indexed) when it has a
  near-empty full-body-height vertical gutter separating two populated
  columns — line length alone fails to separate them (AB's wide single-column
  pages run longer than JPM's two-column page). Flags land in `IngestedSource`,
  `ingest_meta.json`, and the run manifest source line. In `src/confidence.py`
  a prose call citing a flagged page falls back to the key-token overlap check
  (like table/visual), capping confidence at 74 (below High), forcing
  `review`, and recording the degradation in the output/failure row; a clean
  page still enforces verbatim, and a non-cited scrambled page is unaffected.
  Threaded `scrambled_pages` through `assemble_candidates`/`score_candidate`.
  Validated: flags JPM p.2 (+p.1/p.4), does NOT flag AB clean single-column
  pages (incl. p.7, whose failure is a genuine stitched quote that stays
  failed); only AB's real 3-col grid (p.3) and forecast table (p.10) flag.
  Deterministically re-scored the frozen JPM pilot-04 `quote_not_found`: now
  passes at confidence 74 / Medium / review / degraded. 11 new tests (real
  word-box fixtures from JPM p2 + AB p7); 94 pass. No pilot re-run.
- 2026-07-05: `evidence_quote` now accepts a list of verbatim spans (a lone
  string is still one span), so an honest elision — two real passages joined
  with "..." — no longer fails the prose quote gate. Schema
  (`src/schemas.py`) parses string-or-list into `evidence_spans`;
  `evidence_quote` became a property joining spans with " ... " (commentary,
  failures, checker/arbiter inputs unchanged). The prose gate
  (`src/confidence.py`) verifies each span verbatim individually and,
  deterministically for multi-span only, enforces max 3 spans, ≥4 meaningful
  tokens per span, and document order (blocks reversed stitching); ellipses
  are never parsed out of free text — only an explicit list splits. Prompts:
  `analyze_chunk.md` v1.3 emits span lists for elided prose (no more inline
  "..."), `check_candidates.md` v1.2 reads the ` ... `-joined spans as one
  body of evidence. Deterministically re-scored the frozen pilot-04 AB Euro
  Govt Bonds `quote_not_found` (split its recorded quote on the ellipsis into
  2 spans): now passes against the frozen snapshot; the JPM
  `quote_not_found` (no ellipsis, a separate ingestion issue) still fails,
  untouched. 6 new tests; 83 pass. No pilot re-run.
- 2026-07-05: Applied both pilot-03 diagnosability fixes (delegated to an
  Opus 4.8 subagent). (1) `failures.csv` now records `evidence_quote` (column
  after `evidence_kind`; empty for chunk-level failures) so `quote_not_found`
  rows are diagnosable at a glance. (2) `normalize_quote_text` canonicalizes
  typographic/extraction seams symmetrically on quote and snapshot: soft
  hyphen removed, all dash variants folded to `-`, and intra-word hyphens
  removed after whitespace collapse — so a hyphenated word consumed by a PDF
  line break ("AI-related" → "AIrelated") can no longer sink a correct quote,
  in either direction. Word content/order still must match exactly
  (paraphrase/stitch/reorder tests still fail). 6 new tests; 76 pass.

## Next / Open

- Freeze pending: `runs/pilot-05/` (and still `runs/pilot-04/` +
  `runs/pilot-04-rescored/`) are on disk but not yet committed — `runs/` is
  gitignored; freeze by force-add when the analyst review confirms.
- Pilot-05 fix list from the GT comparison (`runs/pilot-05/gt-comparison.md`),
  priority order: (1) deterministic materiality gate for forecast-delta
  evidence (bp/FX-% floor; possibly prose-over-table precedence) — removes
  most of the 10-overreach tail that currently passes at 75/High; (2) dedup
  the same call emitted across multiple leaves (AB global-duration on 3
  leaves, identical Asia series on 2); (3) convention fixes: "close an
  overweight" → N not U, hedged risk notes → UNCERTAIN; (4) client
  questions before encoding conventions: delta-as-view + materiality floor,
  inference-depth scope (analyst-style macro→allocation inference bounds
  recall at ~70-75% if out of scope), dial main+sub level policy,
  leaf-snapping for the 9 near-leaf pairs. (PIMCO source scope is resolved:
  the user replaced `prev-excel/PIMCO.pdf` with the full source on
  2026-07-06.)
- Grouping client questions from pilot-04: which Date/URL a combined
  pipe-joined row should carry, and confirm the outlook-beats-review arbiter
  rule.
- Disputed ground-truth calls (7: BMO Cash/Quality/EM Debt/CAD prose-vs-dial,
  Barings Hedge Funds/EM Equities/TIPs) and possibly-missing rows (3: BMO
  Growth and Materials, Barings US Small Cap) were sent to the Markets Recon
  team as review notes (drafted 2026-07-04, kept outside the repo); update
  the GT CSV and, if conventions change, `prompts/brain.md` when they
  respond.
- Reconcile source count with client (user says 38, workbook CSV has 37) and
  pick an output date-format policy (see `POC_PLAN.md` open items).
- Open questions tracked in `CLAUDE.md` (View legend confirmation, ground-truth
  availability, page-number requirements, HTML source locators, confidence
  threshold).
