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
scoring), `src/assemble.py` (`output.csv`/`failures.csv`/`manifest.md`),
`src/ingest.py` (thin pilot/source loading, snapshots, chunk boundaries,
visual-heavy detection), and `src/llm.py` (swappable headless `claude -p` /
`codex exec`, with `{{name}}` template-var injection). Output rows are the 10
workbook columns plus `confidence`, `band`, and `review_flag`; one-hot columns
are intentionally omitted.

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
`trafilatura`, `htmldate`. 32 unittests pass.

## Recent Changes

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
- 2026-07-03: Added a `.claude/settings.json` hook blocking git commits with
  Claude/Anthropic self-attribution (co-author lines, "Generated with...",
  anthropic.com/claude.ai links) while allowing genuine mentions like
  `CLAUDE.md`; needs a `/hooks` reload or restart to take effect.
- 2026-07-03: Added HTML visual-heavy detection to `src/ingest.py`:
  `count_visual_markup`/`is_visual_heavy` count content graphics (img/canvas/
  figure; svg excluded as icon noise) in raw HTML, flag sources at ≥5, and
  persist per-source `ingest_meta.json` (source type, page count, visual
  counts, flag); `POC_PLAN.md` gains the matching ingest/prompt rules and a v2
  backlog item for headless-browser screenshots of visual-heavy pages. On the
  pilot: Aberdeen flags, Schroders does not.
- 2026-07-03: Made the rubric's 10-point read-quality signal real:
  `snapshot_read_quality` in `src/confidence.py` (PDF ≥200 chars/page to catch
  scanned/image-only text layers; HTML ≥1,000 chars to catch blocked/empty
  fetches), `page_count` threaded through `IngestedSource` and
  `assemble_candidates`; previously the signal was always 10.
- 2026-07-03: Revised `POC_PLAN.md` after design review — LLM-native ingestion
  (native PDF/HTML reading; parsing libs demoted to snapshot layer), concrete
  headless `llm.py` spec with JSON repair-retry, `failures.csv` split
  (`UNCERTAIN` reserved for source ambiguity; pipeline failures get reason
  codes), contract updates (`taxonomy_match: exact|semantic|none`,
  `evidence_kind: prose|table|visual`, table/figure-specific locators for
  visual evidence), granularity rule + full in-prompt taxonomy, quote-check
  normalization spec, eval recall/abstain metrics, and session-separated
  brain-building for pilot blindness.

## Next / Open

- Phase 2 remaining: build `prompts/brain.md` in a **separate session** (needs
  the user's saved ground truth, pilot sources excluded), then run the blind
  pilot in another fresh session (`python -m src.run --sources pilot --run-id
  <id> --engine <claude|codex>`), freeze `runs/<id>/output.csv`, and evaluate
  against held-back originals. The analyze path itself is wired and unit-tested
  but has never been run live — first live action of the pilot session is a
  single-chunk sanity call.
- Reconcile source count with client (user says 38, workbook CSV has 37) and
  pick an output date-format policy (see `POC_PLAN.md` open items).
- Open questions tracked in `CLAUDE.md` (View legend confirmation, ground-truth
  availability, page-number requirements, HTML source locators, confidence
  threshold).
