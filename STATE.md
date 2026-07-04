# Markets Recon / Allocator Pro POC — State

_Last updated: 2026-07-05_

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
length), `src/assemble.py` (`output.csv`/`failures.csv`/`manifest.md`),
`src/ingest.py` (thin pilot/source loading, snapshots, chunk boundaries,
visual-heavy detection, Playwright print-to-PDF capture of visual-heavy HTML),
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
call per source, categorical verdicts feeding the deterministic rubric;
default codex/gpt-5.5/high), a conflict arbiter
(`prompts/arbitrate_conflict.md`, fires only on surviving view conflicts;
default codex/gpt-5.5/medium), and a group-notes resolver
(`prompts/resolve_groups.md`, only when `--group-notes` supplies analyst
free-text pairing notes; default codex/gpt-5.5/low). The normative house
rules live in `prompts/conventions.md`, injected into analyze, checker, and
arbiter alike; `brain.md` carries worked examples + reasoning style,
analyze-only. Both engine CLIs read PDF pages visually (codex renders pages
to PNG itself), so engine routing is unconstrained by source type. All
prompts are indexed in `prompts/REGISTRY.md`. Blind pilots `pilot-01`,
`pilot-02`, and `pilot-03` (5 sources from `prev-excel/pilot.csv`; `pilot-03`
is the first with the checker + arbiter steps active) are frozen in `runs/`
awaiting user review; `POC_PLAN.md` locks the 3-phase build order and
LLM-native ingestion design. A `.venv` holds `pdfplumber`, `pdfminer.six`,
`trafilatura`, `htmldate`, `playwright` (+ chromium). 77 unittests pass.

## Recent Changes

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
- 2026-07-05: Factored the normative house rules out of `brain.md` into
  `prompts/conventions.md` (one edit point when client feedback changes a
  convention), injected as `{{conventions}}` into analyze, checker, and
  arbiter; the checker is told to never fail a call for following a listed
  convention. Live smoke: the pilot-03 JPY `checker_sign_mismatch` (extractor
  applied two-sided-nets-to-`N`; the convention-blind checker killed it) now
  passes 3-for-3. Added analyst group-notes handling for combined sources
  (the GT combines some same-firm review+outlook pairs into one pipe-joined
  source — discovered via Schroders, whose pilot doc alone correctly yields
  no calls): `--group-notes <file>` free text is resolved once at run start
  by `prompts/resolve_groups.md` into `work/<run>/groups.json` (unknown ids
  and unmatched notes are warned in the manifest, never guessed; resolver
  failure degrades to an ungrouped run). Grouped docs chain rolling memory,
  assembly dedups/arbitrates on the group (`duplicate_same_view` failures
  keep reconciliation exact; cross-doc corroboration noted in commentary;
  arbiter rule: outlook beats review), and grouped rows pipe-join
  Source/Date/URL. 70 tests pass.
- 2026-07-04: Ran the first blind pilot with checker + arbiter active
  (`pilot-03`, claude/opus/medium; checker codex/gpt-5.5/high, arbiter
  codex/gpt-5.5/medium) after a passing single-chunk sanity call (JPM PDF, 4
  candidates). 33 candidates → 29 kept, 4 failed (3 `quote_not_found`, 1
  `checker_sign_mismatch`), 0 chunk failures, count check pass. PIMCO recovered
  to 9 candidates (0 in pilot-01) and total failures fell to 4 (from 13),
  consistent with the `evidence_kind: visual` fix. Frozen at `runs/pilot-03/`.
  Added a `schroders` → local-PDF entry in `_pilot_local_pdf_for` and re-ran
  Schroders alone (`pilot-03-schroders`): the native-PDF read again yields 0
  calls (backward-looking Q1 recap, no forward stance) — the earlier HTML
  zero was not an ingestion artifact.
- 2026-07-04: Added the checker and arbiter LLM steps. Checker: categorical
  verdicts (`supports_view`, `forward_looking`, `asset_match`:
  pass|unclear|fail + note) — never a self-confidence number; any `fail`
  hard-fails the candidate (`checker_sign_mismatch` /
  `checker_not_forward_looking` / `checker_asset_mismatch`), anything short
  of all-pass caps confidence at 74, so High means "a second model confirmed
  the evidence"; rubric weights unchanged for cross-run comparability.
  Arbiter: names a winner (kept, forced `review`, reasoning in commentary;
  losers `arbitrated_out`) or null → `unresolved_conflict`; not shown
  deterministic scores (anchoring). Call failures degrade to capped+review,
  never promote. Manifest gains step config + failure-reason breakdown.
- 2026-07-04: Applied both post-pilot-01 fixes. (1) `analyze_chunk.md` v1.1:
  text inside a designed layout artifact (callout box, sidebar, banner, stat
  panel, infographic column) must be tagged `evidence_kind: visual`, not
  `prose`, so it gets the key-token-on-page check instead of the hard verbatim
  check that rejected 12 correct pilot calls. (2) `visual_heavy` HTML sources
  are now printed to PDF at ingest (`print_url_to_pdf` in `src/ingest.py`:
  headless chromium, screen-CSS emulation, consent/investor-gate overlay
  dismissal, slow scroll + settle wait so lazy images and scroll-triggered JS
  charts finish rendering) and flow through the native PDF path — page chunks,
  `p.N` locators, per-page pdfplumber snapshot; `printed_pdf` recorded in
  `IngestedSource`, `ingest_meta.json`, and the run manifest, and the chunk
  prompt names the capture's origin URL. Live-verified on Aberdeen: 10-page
  capture with all three JS charts fully rendered (a fast scroll left chart
  bodies blank; the consent modal otherwise masked every printed page).
  Playwright + chromium added to the venv; chromium-printed fixture at
  `tests/fixtures/printed_page.pdf`; 44 tests pass. PDF rasterization was
  explicitly skipped — Phase 1 proved both engines already read PDFs visually.
- 2026-07-04: Ran the first live blind pilot (`pilot-01`, claude/opus/medium).
  24 candidates → 11 kept, 13 failed (count reconciles), frozen at
  `runs/pilot-01/`. All 13 failures were `quote_not_found`; ~12 were false
  negatives from a native-read-vs-snapshot gap — the model reads rendered PDF
  pages but the verbatim check ran against the pdfplumber snapshot, which
  scrambles infographic/callout-box text (PIMCO lost all 10 candidates this
  way). Led directly to the `evidence_kind: visual` and print-to-PDF fixes.
- 2026-07-04: Built `prompts/brain.md` (v1.1, ~1.4k tokens) from the user's
  five-source ground truth in `ground-truth/ground-truth.csv` (Schwab, CBRE
  IM, Cantor Fitzgerald, BMO GAM, Barings — no pilot-source overlap, so
  blindness holds). All 69 GT rows validated against the locked taxonomy and
  verified against their sources; the Cantor block's mis-sorted Full
  Commentary column was re-paired in place (views untouched). Wrote
  `ground-truth/review-notes-for-markets-recon.md` for the client: 7 disputed
  calls (4 BMO prose-vs-dial rows, 3 Barings rows), 3 possibly missing rows,
  minor number slips.

## Next / Open

- Phase 2 evaluation: `pilot-01`, `pilot-02`, and `pilot-03` (plus the
  single-source `pilot-03-schroders`) are frozen and await the user's blind
  review against held-back originals (per-call view/leaf/citation). Still open:
  whether the softer key-token check should also cover `prose` evidence when
  the cited page's snapshot is detected as scrambled (column-merge/rotated
  text).
- Grouping live test (pilot-04) is staged: pilot.csv gained the second
  Schroders doc ("Our multi-asset investment views – March 2026", 3/20/2026,
  user-downloaded PDF in `prev-excel/`; the pilot firm→PDF mapping now
  disambiguates on title), notes at `prev-excel/group-notes.md`; a live
  resolver smoke grouped the pair with no warnings. Run blind in a fresh
  session with `--group-notes prev-excel/group-notes.md`. Client questions:
  which Date/URL a combined pipe-joined row should carry, and confirm the
  outlook-beats-review arbiter rule.
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
