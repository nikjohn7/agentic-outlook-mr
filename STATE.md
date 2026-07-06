# Markets Recon / Allocator Pro POC — State

_Last updated: 2026-07-03_

## Current State

POC for Markets Recon / Allocator Pro: ingest fund/asset-manager outlook sources
(PDF/HTML) and produce reviewable sub-asset-class allocation calls (`O`/`N`/`U`/
`UNCERTAIN`) with citations, against the locked taxonomy in `excel-file/Asset
Class List - Locked.csv`. Full spec: `CLAUDE.md`, `POC_CONTEXT.md`,
`WORKBOOK_SCHEMA.md`, `POC_PLAN.md`.

Currently in Phase 1 scaffolding. The deterministic spine now includes
`src/taxonomy.py` for exact 396-leaf taxonomy validation, `src/schemas.py` for
the LLM candidate contract, `src/confidence.py` for quote/table/visual evidence
checks and rubric scoring, `src/assemble.py` for `output.csv`/`failures.csv`/
`manifest.md`, `src/ingest.py` for thin pilot/source loading and snapshot
helpers, and `src/llm.py` for swappable headless `claude -p` / `codex exec`
calls. Output rows are the 10 workbook columns plus `confidence`, `band`, and
`review_flag`; one-hot columns are intentionally omitted. Phase 2 LLM analysis
is not wired yet, and `src/run.py` currently supports ingest-only scaffolding;
an ingest-only smoke run on the 5 pilot sources produced non-empty snapshots
and chunk manifests under ignored `work/phase1-smoke/`. The workbook has been
fully parsed and documented, the first milestone is a blind pilot on 5 sources
in `prev-excel/pilot.csv`, and `POC_PLAN.md` locks the 3-phase build order and
LLM-native ingestion design. A `.venv` is scaffolded with `pdfplumber`,
`pdfminer.six`, `trafilatura`, `htmldate`.

## Recent Changes

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
- 2026-07-03: Extended Phase 1 scaffolding with shared candidate schemas,
  deterministic confidence/evidence checks, assembly writers for output and
  failure files, swappable Claude/Codex subprocess adapter config, thin ingest
  helpers, run skeleton, `requirements.txt`, and `DESIGN.md`/prompt registry
  skeletons; unittest coverage now exercises taxonomy, confidence, assembly,
  LLM parsing/repair, and ingestion helpers.
- 2026-07-03: Added `work/` to `.gitignore` and smoke-tested ingest-only on the
  5-source pilot set; all five sources produced non-empty snapshot text and
  persisted chunk manifests.
- 2026-07-03: Added the first Phase 1 deterministic spine slice:
  `src/taxonomy.py` exact-label validation and deterministic lookup over
  `excel-file/Asset Class List - Locked.csv`, plus unittest coverage proving
  all 396 locked leaves round-trip and unknown/non-exact labels are rejected.
- 2026-07-03: Revised `POC_PLAN.md` after design review — LLM-native ingestion
  (native PDF/HTML reading; parsing libs demoted to snapshot layer), concrete
  headless `llm.py` spec with JSON repair-retry, `failures.csv` split
  (`UNCERTAIN` reserved for source ambiguity; pipeline failures get reason
  codes), contract updates (`taxonomy_match: exact|semantic|none`,
  `evidence_kind: prose|table|visual`, table/figure-specific locators for
  visual evidence), granularity rule + full in-prompt taxonomy, quote-check
  normalization spec, eval recall/abstain metrics, and session-separated
  brain-building for pilot blindness. (uncommitted)
- 2026-07-01: Added model-routing policy to `POC_PLAN.md` — deterministic
  taxonomy lookups/validation/arithmetic never go through a model; Haiku/
  Sonnet/Opus tiers reserved for judgment-heavy extraction and review.
  (uncommitted)
- 2026-06-30: Locked the 5-source pilot set (`prev-excel/pilot.csv`) as the
  first milestone, ahead of the full 37-source `Target Ingestion List.csv`
  batch; pilot is blind (no ground truth shown to the building agent).
- 2026-06-27: Parsed and documented the workbook — locked taxonomy
  (`Asset Class List - Locked.csv`), target sources (`Target Ingestion
  List.csv`), and output shape (`Target Output.csv`) confirmed as canonical
  in `WORKBOOK_SCHEMA.md`.

## Next / Open

- Before Phase 2 LLM analysis, manually review pilot PDF page-range chunks for
  rendered table/grid legibility.
- Phase 2 remains open: author `prompts/analyze_chunk.md` and pilot-excluded
  `brain.md`, then run blind LLM analysis over pilot chunks.
- Reconcile source count with client (user says 38, workbook CSV has 37) and
  pick an output date-format policy (see `POC_PLAN.md` open items).
- Open questions tracked in `CLAUDE.md` (View legend confirmation, ground-truth
  availability, page-number requirements, HTML source locators, confidence
  threshold).
