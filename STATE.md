# Markets Recon / Allocator Pro POC — State

_Last updated: 2026-07-03_

## Current State

POC for Markets Recon / Allocator Pro: ingest fund/asset-manager outlook sources
(PDF/HTML) and produce reviewable sub-asset-class allocation calls (`O`/`N`/`U`/
`UNCERTAIN`) with citations, against the locked taxonomy in `excel-file/Asset
Class List - Locked.csv`. Full spec: `CLAUDE.md`, `POC_CONTEXT.md`,
`WORKBOOK_SCHEMA.md`, `POC_PLAN.md`.

Currently in the planning/scaffolding stage. The first deterministic spine
slice is implemented: `src/taxonomy.py` loads the locked 396-leaf taxonomy,
validates exact `Sub-Asset Class` labels, and returns deterministic lookup
fields for `Asset Class Category`, `Asset Class`, and `Canva Groupings`; the
unit tests read the real workbook CSV and verify all 396 leaves round-trip.
No ingestion, extraction, LLM adapter, assembly, or scoring code has been
written yet. The workbook has been fully parsed and documented (taxonomy,
target sources, output shape), the first milestone (a blind pilot on 5 sources
in `prev-excel/pilot.csv`) is defined, and `POC_PLAN.md` locks a 3-phase build
order (deterministic spine → LLM analyze → scale), a model-routing policy
(deterministic joins/lookups/arithmetic stay as plain code;
Haiku/Sonnet/Opus or GPT-5.5 tiers for judgment only), and an LLM-native
ingestion design: the engine reads PDFs as rendered page ranges and HTML from
saved snapshots, while `pdfplumber`/`trafilatura` serve only as a thin snapshot
layer (quote-check corpus + audit trail). Planned LLM calls will run as
headless `claude -p` / `codex exec` subprocesses behind `src/llm.py` with JSON
schema-validation and repair-retry; both CLIs read PDFs natively, so the
Claude-vs-Codex split is decided after the pilot. A `.venv` is scaffolded
with `pdfplumber`, `pdfminer.six`, `trafilatura`, `htmldate`.

## Recent Changes

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

- Continue Phase 1 deterministic spine + run scaffolding: thin `ingest.py`,
  `confidence.py`, `assemble.py`, `llm.py`, `run.py`, `requirements.txt`, and
  `DESIGN.md`/`prompts/REGISTRY.md` skeletons.
- Reconcile source count with client (user says 38, workbook CSV has 37) and
  pick an output date-format policy (see `POC_PLAN.md` open items).
- Open questions tracked in `CLAUDE.md` (View legend confirmation, ground-truth
  availability, page-number requirements, HTML source locators, confidence
  threshold).
