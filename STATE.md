# Markets Recon / Allocator Pro POC — State

_Last updated: 2026-07-04_

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

The Phase 2 analyze path is now wired end-to-end (not yet run live on the
pilot). `src/run.py` ingests each source, then for every chunk fills the
`prompts/analyze_chunk.md` template (injected taxonomy + brain few-shots +
rolling `memory.md` + the native chunk), calls the engine via `src/llm.py`,
collects candidates, and scores/assembles one run into `runs/<run-id>/`.
`analyze_chunk.md` is authored (full taxonomy injected at runtime from the
locked CSV, granularity + semantic-snapping + evidence-kind + visual-locator +
unseen-figures rules, JSON contract) and indexed in `prompts/REGISTRY.md`.
`--engine {claude,codex}` selects the engine per run. The workbook is fully
parsed; the first milestone is a blind pilot on 5 sources in
`prev-excel/pilot.csv`; `POC_PLAN.md` locks the 3-phase build order and
LLM-native ingestion design. A `.venv` holds `pdfplumber`, `pdfminer.six`,
`trafilatura`, `htmldate`, `playwright` (+ chromium). 44 unittests pass.

## Recent Changes

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
- 2026-07-04: Ran the first live blind pilot (`pilot-01`, claude/opus/medium)
  after a passing single-chunk sanity call (JPM PDF, 4 candidates, 0 failures).
  All 5 sources ingested; 24 candidates → 11 kept, 13 failed, 0 chunk failures
  (count reconciles). Frozen at `runs/pilot-01/`. Kept calls are well-formed
  with verbatim prose or forecast-table evidence. **All 13 failures are
  `quote_not_found`, and ~12 are false negatives from a native-read-vs-snapshot
  gap:** the model reads rendered PDF pages, but the deterministic verbatim
  check runs against the pdfplumber text snapshot, which scrambles/column-merges
  infographic and callout-box text. PIMCO (a 2-page infographic) lost all 10
  candidates this way — its box phrases exist on the page but not as contiguous
  strings in the snapshot (key tokens confirmed present); AllianceBernstein lost
  2 'Central Narrative' box calls. Only ~1 (JPM stitched-fragment quote) is a
  true rejection. Remediation for the next blind run: have the model classify
  densely-laid-out box/infographic prose as `evidence_kind: visual` so it gets
  the softer key-token-on-page check (which recovers all 10 PIMCO calls) instead
  of the hard verbatim check. Run not hand-edited; tuning happens between runs.
- 2026-07-04: Made model and reasoning effort explicit per run. `run.py` gained
  required `--model`/`--effort` flags (validated in `resolve_engine_settings`,
  threaded through `run_pipeline`/`analyze_source` into `llm.py`, which passes
  `--model`/`--effort` to `claude -p` and `-m`/`-c model_reasoning_effort` to
  `codex exec`). Codex is pinned to `gpt-5.5` (`CODEX_MODEL`); claude accepts
  an alias or full name and no longer silently inherits the CLI settings
  default. The manifest records a Run configuration section
  (engine/model/effort). 9 new tests; 41 total pass.
- 2026-07-04: Built `prompts/brain.md` (now v1.1, ~1.4k tokens) from the
  user's five-source ground truth in `ground-truth/ground-truth.csv` (Schwab,
  CBRE IM, Cantor Fitzgerald, BMO GAM, Barings — no pilot-source overlap, so
  blindness holds). All 69 GT rows validated against the locked taxonomy;
  every row checked against its source (3 live pages; CBRE PDF + a
  user-supplied BMO print-to-PDF saved in `ground-truth/`). Encodes
  house-scale→view translation (incl. v1.1 published-level-wins: a printed
  dial/score/tier is the call; prose tone and change verbs never override
  it), implied-call rules, not-a-call boundaries, snapping defaults, and
  `reasoning` style. Fixed the Cantor block of the GT CSV in place (its Full
  Commentary column had been sorted independently of its rows; re-paired and
  keyword-verified, views untouched). Wrote
  `ground-truth/review-notes-for-markets-recon.md` for the client: 7 disputed
  calls (4 BMO rows where prose tone was used over printed Neutral dials —
  Cash, Quality, EM Debt, CAD; 3 Barings rows), 3 possibly missing rows
  (BMO Growth/Materials, Barings US Small Cap), minor number slips. Registry
  updated; 32 tests pass.
- 2026-07-04: Wired the Phase 2 analyze path (still blind — not run live).
  `run.py` now does ingest → per-chunk `analyze_chunk.md` call with rolling
  `memory.md` → assemble into `runs/<run-id>/{output,failures,manifest}`.
  Added `taxonomy.grouped_block()` (396 leaves, ~2.3k tokens, injected so the
  prompt never drifts from the locked CSV — user chose runtime injection over
  static embedding), `{{name}}` template-var injection in `llm.py`, chunk-level
  failure handling (`json_parse_error`/`engine_error` recorded without a
  candidate, kept out of the candidate reconciliation), and a manifest section
  listing sources with `visual_heavy` flags. Authored `analyze_chunk.md` +
  registry entry. 5 new tests (analyze loop via fake runner, template fill,
  chunk-content rendering); 32 total pass. A live pilot run is intentionally
  deferred to a fresh session (blindness).
- 2026-07-04: Closed the Phase 1 gate. Check A: all 3 pilot PDFs read natively
  page-by-page — J.P. Morgan scenario/positioning table (p.3),
  AllianceBernstein forecast tables + gauge dials (p.2, p.10), PIMCO
  infographic calls (p.1) all legible; no source needs special treatment.
  Check B: real headless smoke calls through `src/llm.py` all pass — claude
  trivial + PDF-read, codex trivial + PDF-read, each `attempts=1`. An initial
  codex failure was a corrupted/stale codex install (fixed by user reinstall,
  verified working with inherited stdin afterward); `_default_runner` keeps
  `stdin=DEVNULL` as defense since `codex exec` documents that piped stdin is
  appended to the prompt. Gauge-dial test confirmed **both engines read PDFs
  visually**: codex's session log shows it rendered page 2 via `pdftoppm` to
  PNG and viewed the image, correctly reporting all three AllianceBernstein
  dial needle positions (matching claude and manual reading). Engine routing
  is therefore unconstrained by source type. Codex writes rendered pages to
  `tmp/` in the workdir (gitignored).

## Next / Open

- Phase 2 evaluation: `pilot-01` is frozen and awaits the user's blind review
  against held-back originals (per-call view/leaf/citation). Both post-pilot
  fixes (`evidence_kind: visual` for box text; print-to-PDF for visual-heavy
  HTML) are applied and the next blind run (`pilot-02`) can go whenever the
  user is ready. Still open: whether the softer key-token check should also
  cover `prose` evidence when the cited page's snapshot is detected as
  scrambled (column-merge/rotated text).
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
