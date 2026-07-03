# Markets Recon / Allocator Pro — POC Pipeline Plan

## Context

We need a focused POC that ingests asset-manager market-outlook documents (PDFs and HTML
pages) and produces reviewable asset-allocation **calls** — `O`/`N`/`U`/`UNCERTAIN` per
sub-asset class — each with a citation (exact supporting phrase + reasoning), a page/source
locator, and a **deterministic** confidence score.

The repo today is spec-and-data only (no code, not a git repo). Three CSVs are authoritative:

- `excel-file/Asset Class List - Locked.csv` — locked taxonomy: 396 sub-asset-class leaves;
  columns `Number, Sub-Asset Class, Asset Class Category, Asset Class` (4 tops:
  Alternatives/Equities/Fixed Income/Currencies), `Canva Groupings` (12). Once a leaf is
  chosen the other three columns are a **deterministic lookup**.
- `excel-file/Target Ingestion List.csv` — the production batch: 37 sources (~13 PDF, ~24
  HTML), with edge cases (one `read://…?url=<encoded>` reader link, one `seismic.com` link
  with `PLUSSIGN`/`___` encoding, blank IDs, tracking params).
- `excel-file/Target Output.csv` — output **shape** only (1 example row), columns:
  `Firm, Date, Source, URL, Sub-Asset Class, Asset Class Category, Canva Groupings,
  Asset Class, View, Full Commentary`.

The **"brain"** is `prev-excel/`: `…test.csv` (prior dataset reduced to input columns
`Firm, Date, Source, MR Link` — **119 distinct sources**, MR redirect links) plus the
user's separately-saved original extracted results (the O/N/U calls + commentary) used as
calibration few-shots and as pilot ground truth. It is human-made, fast, **not fully
verified** — guidance, not gospel. It contains no `uncertain` calls; `uncertain` +
confidence are new POC capabilities.

The immediate runtime is **Claude Code / Codex acting as the LLM engine** (no hosted API
yet). Every prompt/instruction is documented with rationale so a later port to the Claude
API is mechanical.

### Decisions locked in (from user)
- **First milestone:** build full scaffolding, then **pilot on the 5 sources in
  `prev-excel/pilot.csv`** (see Pilot set below). The pilot runs **blind** — the agent never
  sees the ground-truth results while generating. The user compares the frozen output against
  their saved originals afterward (see Evaluation below).
- **Checker/verification agent:** deferred to v2 (a deterministic verbatim-quote check still
  runs in v1 confidence scoring).
- **Call philosophy:** **balanced** — assign `O/N/U` for explicit and reasonably-clear
  implied calls; reserve `UNCERTAIN` for genuinely weak/conflicting evidence.
- **Stack:** Python scaffolding (rich PDF/HTML/CSV ecosystem, clean port to the Python
  Anthropic SDK later).
- **Model routing:** keep deterministic script work as deterministic code; use a tiered
  Claude Code / Codex routing policy for model work (Haiku / GPT-5.5 low for basic
  execution and summaries, Sonnet / GPT-5.5 medium for most implementation tasks, Opus /
  GPT-5.5 high for judgment-heavy extraction and review).
- **Ingestion is LLM-native (accuracy-first):** the model reads sources directly — PDFs as
  native page-range chunks (rendered pages, so view grids, arrows, and colored tables
  survive; plain-text extraction deletes them), HTML from a saved snapshot. The parsing
  libraries (`pdfplumber`, `trafilatura`) are demoted to a **thin snapshot layer**: an
  audit copy of what was processed + the reference corpus for the verbatim-quote check.
  The LLM never consumes that text. Both Claude Code and Codex CLI read PDFs natively;
  which engine handles which source is decided after the pilot, not hardcoded.
- **Failure handling:** pipeline failures (hallucinated quote, unmappable label, parse
  error, unresolved conflict) are never relabeled `UNCERTAIN` and never dropped silently —
  they go to `runs/<run-id>/failures.csv` with a reason code. `UNCERTAIN` in `output.csv`
  is reserved for genuine source ambiguity (the manager's stance is unclear), a judgment
  the model makes from the evidence itself.

## Pilot set (`prev-excel/pilot.csv`)

Five sources — 3 local PDFs (already downloaded in `prev-excel/`) + 2 HTML (fetch from URL):

| # | Firm                          | Type | Source / locator                                                                 |
|---|-------------------------------|------|----------------------------------------------------------------------------------|
| 1 | Aberdeen Investments          | HTML | `https://www.aberdeeninvestments.com/.../emerging-markets-q2-2026-outlook-shifting-sands` |
| 2 | AllianceBernstein             | PDF  | `prev-excel/alliance-bernstein.pdf`  (Global Macro Outlook: Second Quarter 2026) |
| 3 | Schroders                     | HTML | `https://www.schroders.com/.../quarterly-markets-review---q1-2026/`              |
| 4 | J.P. Morgan Asset Management  | PDF  | `prev-excel/jp-morgan.pdf`  (Global Fixed Income Views 2Q 2026)                  |
| 5 | PIMCO                         | PDF  | `prev-excel/PIMCO.pdf`  (Layered Uncertainty: Conflict, Credit Stress, and AI)   |

`pilot.csv` is clean (5 rows, columns correctly aligned). `ingest.py` maps a pilot row to a
local PDF by **firm name** when a matching file exists in `prev-excel/`, otherwise fetches
the HTML URL. (Rows 2/4/5 still carry an MR-redirect or web URL in the CSV — ignore it for
those three; the local PDF is the source of truth.)

## Proposed structure

```
poc/
  excel-file/                  # existing (authoritative inputs)
  prev-excel/                  # existing (brain: test.csv + saved ground truth)
  src/
    taxonomy.py                # load locked CSV; leaf -> (category, asset class, canva); validate a label
    ingest.py                  # thin: resolve URL (MR redirect / read:// / seismic) + download/snapshot + define chunk boundaries w/ locators
    confidence.py              # deterministic rubric -> score, band, uncertain/review flags
    assemble.py                # cross-chunk dedup/merge; deterministic taxonomy fill; write output.csv + manifest
    llm.py                     # thin adapter: call(prompt_file, inputs) -> parsed JSON  (the API-port seam)
    run.py                     # orchestrator over a chosen source set (<=20)
  prompts/
    REGISTRY.md                # index + rationale + version log for every prompt (the porting contract)
    analyze_chunk.md           # the ONE LLM prompt: find calls -> snap to taxonomy -> assign view + quote + locator
    brain.md                   # few-shot examples distilled from prev-excel, label-reconciled
  work/<run-id>/<source-id>/   # downloaded text + rolling memory.md + candidates.json
  runs/<run-id>/
    output.csv                 # the reviewable run-level file (Target Output shape + confidence cols)
    failures.csv               # every candidate that died in the pipeline + reason code
    manifest.md                # lists every source processed + counts + flags
  DESIGN.md                    # pipeline + data contract + confidence rubric + API-porting + client-runbook notes (one doc)
  requirements.txt
```

Deps: `pdfplumber` + `trafilatura` (with tables kept) — **snapshot layer only**: the
quote-check reference corpus and audit copy, never LLM input (the LLM reads sources
natively). `requests` fallback for fetching. Stdlib `csv`/`json` otherwise.

## Pipeline stages (deterministic code vs LLM)

1. **Ingest (thin)** — *deterministic* (`ingest.py`). Resolve the source URL (user supplies
   real URLs for MR-redirect pilot rows; unwrap `read://…?url=`, decode seismic
   `PLUSSIGN`/`___`, strip tracking params); detect PDF vs HTML; **download + snapshot**:
   PDFs saved locally as-is, HTML fetched once and saved raw plus a text rendering
   (`trafilatura` with tables kept; `pdfplumber` text for PDFs). The snapshot has exactly
   two jobs — the reference corpus for the verbatim-quote check, and the audit/eval trail
   (web pages change; a run must record what it processed). **The LLM never reads the
   snapshot text for PDFs** — it reads the PDF natively. Chunk boundaries: PDF = 5-page
   ranges (`p.N–M`), HTML = ~6–8k chars of the saved text (`char:start-end`). Keep this
   module deliberately simple; it earns complexity only if a source proves unfetchable.
2. **Analyze chunk** — *the one LLM step* (`prompts/analyze_chunk.md`), guided by
   `brain.md` few-shots. The engine reads the chunk **natively**: PDF chunks as rendered
   page ranges (tables, arrow grids, and colored view dashboards are seen as designed),
   HTML chunks from the saved snapshot text. Input: one native chunk + the source's rolling
   `memory.md` + the **full 396-leaf taxonomy embedded in the prompt** (~4–5k tokens). In a
   single call it finds candidate calls, snaps each to a locked leaf, and assigns `view`
   (`O/N/U/UNCERTAIN`) with the explicit-vs-implied flag, evidence (quote or table/visual
   reference), reasoning, and locator. Output: candidate-call JSON (the contract below) + a
   one-paragraph chunk summary appended to `memory.md`. *(Extract → map → call are one
   prompt, not three round-trips; the deterministic guardrails below catch what it gets
   wrong.)* Two prompt rules are load-bearing:
   - **Granularity:** make the call at the level the source actually states — "we favor EM
     equities" → `Emerging Markets Equities`, not fan-out to `Taiwan Equities` etc. Never
     propagate a parent view to child leaves; broad and fine calls may legitimately coexist
     in one source.
   - **Semantic snapping is the LLM's job; validation is code's job.** The model maps
     acronyms/synonyms/paraphrases ("EM equities" → `Emerging Markets Equities`) and
     records how it matched (`exact` vs `semantic`); code only verifies the snapped label
     is one of the 396.
3. **Validate + score** — *deterministic* (`taxonomy.py` + `confidence.py`),
   **evidence-kind-aware**. Reject any label not among the 396 leaves; check evidence:
   `prose` → the quote must appear verbatim in the **normalized** snapshot (normalization
   spec: unicode NFKC, dehyphenate line breaks, collapse whitespace, straighten quotes —
   applied identically to both sides); `table`/`visual` → softer check (key tokens from the
   evidence present in the cited page's snapshot text) since grid content has no contiguous
   sentence to match, plus the locator must name the specific table/figure (see contract).
   Then compute the confidence score/band. The LLM never sets the score. **Hard-check
   failures route to `failures.csv` with a reason code** (`taxonomy_no_match`,
   `quote_not_found`, `unresolved_conflict`, `json_parse_error`, …) — they never become
   `UNCERTAIN` and are never silently dropped.
4. **Assemble** — *deterministic* (`assemble.py`). Merge/dedup candidates for the same leaf
   across chunks (conflicts resolved via the rubric), deterministically fill
   category/asset-class/canva, write `output.csv` (+ the one-hot `U/N/O` columns),
   `failures.csv`, and `manifest.md`.

## Inter-step data contract

One JSON object per candidate call (a dataclass in `assemble.py`; this schema is the LLM's
output format) — token-efficient, explicit:

```json
{
  "source_id": "aberdeen-em-q2-2026",
  "chunk_id": "p11-15",
  "sub_asset_raw": "EM equities",             // model's words, pre-snap
  "sub_asset_class": "Emerging Markets Equities", // snapped locked leaf (validated)
  "taxonomy_match": "exact|semantic|none",    // exact = source wording is the leaf label; semantic = acronym/synonym/paraphrase mapping
  "view": "O|N|U|UNCERTAIN",                  // UNCERTAIN = the source itself is ambiguous, never a pipeline-failure label
  "call_language": "explicit|implied|none",
  "evidence_kind": "prose|table|visual",
  "evidence_quote": "verbatim sentence(s); for table/visual, the cell/arrow content as read",
  "locator": "p.13",                          // prose: "p.N" or "char:start-end"; table/visual: page + the specific table/figure — caption, grid title, or figure number, e.g. "p.5 — 'Fixed Income Views' grid"
  "reasoning": "one sentence why this view",
  "conflict": false
}
```

**Locator rule for table/visual evidence:** a page number alone is not enough — the locator
must identify *which* table/figure/grid on that page produced the call (caption, printed
title, or figure number; nearest heading if the graphic is unlabeled), so a reviewer can
find the exact artifact even when no verbatim sentence exists.

Deterministic fields added downstream: `evidence_check` (pass/fail), `confidence`,
`band`, `review_flag`, and the looked-up `asset_class_category/asset_class/canva_groupings`.

## Rolling memory (`work/<run-id>/<source-id>/memory.md`)

Append-only markdown so later chunks never re-read earlier ones:

```
# <Firm> — <Source title>  (source_id, URL)
## Chunk p.11–15
Summary: <one paragraph>
Candidates: Taiwan Equities=O[p.13]; Oil=U[p.14]
## Chunk p.16–20
...
## Running call ledger
| Sub-Asset Class | Views seen (locator) | Status |
| Taiwan Equities | O[p.13], O[p.18]     | consistent |
| Oil             | U[p.14], N[p.22]     | CONFLICT  |
```

The ledger is what `assemble.py` reads to dedup and to detect cross-chunk conflicts.

## Deterministic confidence rubric (`confidence.py`)

The LLM supplies *observations* (flags + the quote); code computes the score (0–100):

| Signal (deterministic input)                         | Points |
| ---------------------------------------------------- | ------ |
| `call_language` = explicit / implied / none          | 30 / 15 / 0 |
| Evidence check: `prose` quote verbatim in normalized snapshot; `table`/`visual` key tokens on cited page + specific table/figure locator | 25 / fail → `failures.csv` |
| `taxonomy_match` = exact / semantic / none           | 20 / 10 / fail → `failures.csv` |
| No cross-chunk conflict for this leaf                 | 15 (−10 if conflict) |
| Chunk read quality (page rendered/readable, snapshot non-empty) | 10 / 0 |

Bands & balanced policy: **≥75 High** keep call; **50–74 Medium** keep + review flag;
**<50 Low** keep + strong review flag. **Hard-check failures** (evidence check failed,
`taxonomy_match=none`, unresolved conflict, malformed JSON) never reach `output.csv` —
they route to `failures.csv` with a reason code and the partial call preserved, so nothing
disappears silently and `UNCERTAIN` keeps its meaning (source ambiguity only). Thresholds
live in one constant so they are tunable after the pilot. (Documented in `DESIGN.md`.)

## Brain usage & label reconciliation

- Pre-build `prompts/brain.md`: representative human examples per Canva Grouping (source
  excerpt → sub-asset → view → rationale), used as few-shots in `analyze_chunk.md`.
  **Reconcile every example's label to a locked leaf**; drop/relabel any that don't map.
  (Overlap is substantial but imperfect — the deterministic validator is the guardrail; the
  brain is guidance only and never overrides the locked taxonomy.)
- **No leakage into the pilot:** `brain.md` must **exclude every pilot source** (filter out
  rows whose `Firm`+`Source` match a pilot item). Otherwise the agent sees the pilot's own
  answers as few-shots, which defeats the blind test. The brain teaches *style of judgment*
  from other sources, never the answer for a source under test.
- **Session separation, not just file filtering:** building `brain.md` requires reading the
  saved ground truth (which includes pilot answers), so brain-building and pilot generation
  happen in **two completely separate sessions** — an agent whose context window has seen
  pilot answers must never run the pilot. Known residual leak, accepted for the pilot:
  `test.csv` row multiplicity reveals roughly how many calls the analyst made per source;
  long-term the brain moves to one-source-at-a-time context that exposes no counts.

## Claude-Code-now → API-later boundary

All LLM I/O goes through one thin adapter (`src/llm.py`, `call(prompt_file, inputs) ->
json`). **v1 adapter body = headless subprocess:** compose the prompt from the prompt file
+ inputs, invoke `claude -p` (or `codex exec`) with tool access to read the chunk's native
source (PDF page range / snapshot file), capture stdout, then **parse → schema-validate →
repair-retry**: on malformed or contract-violating JSON, retry once or twice feeding the
validation error back; still-failing calls land in `failures.csv` as `json_parse_error`.
Each chunk call runs with a fresh context (reproducible-ish, protects blindness, and is a
true rehearsal of the API port). Engines are interchangeable behind the same signature —
both Claude Code and Codex CLI read PDFs natively, so the per-source engine split is an
empirical decision after the pilot, not an architectural one.

Porting = swapping the adapter body for an Anthropic SDK call with the **same** prompt
files and **same** JSON contract. Prompts live only in `prompts/*.md` (never inline),
indexed in `REGISTRY.md` with rationale + version; `DESIGN.md` notes the suggested model
tier, and keeps an **API-port notes** list: subscription headless mode has no
temperature/seed control (run-to-run variance is accepted for the POC — record it);
the API port gains temperature/seed, structured outputs, and batch pricing.

**Client handoff (later deliverable):** a short runbook showing the client how to run this
on their **own Claude / Codex subscriptions** — install the CLI, log in, `python run.py
--sources <set>` — no API keys required. The headless-subprocess architecture is chosen
precisely so that handoff is trivial.

### Model routing

Do not route deterministic joins, checks, or arithmetic through a model. These stay as
plain scripts and tests:

- locked-taxonomy lookup: `Sub-Asset Class` -> `Asset Class Category`, `Asset Class`,
  `Canva Groupings`
- exact taxonomy-label validation
- output-column ordering and one-hot mapping
- batch splitting under the 20-source cap
- verbatim quote-found checks
- confidence arithmetic once deterministic signals are known

Use models only where language understanding, engineering judgment, or analyst judgment is
actually needed:

- **Haiku / GPT-5.5 low:** simple script execution, deterministic-output summaries, row
  counts, file inventories, and low-risk mechanical checks.
- **Sonnet / GPT-5.5 medium:** most pipeline implementation, tests, URL cleanup, PDF/HTML
  ingestion, chunking mechanics, manifests, JSON parsing, and ordinary debugging.
- **Opus / GPT-5.5 high:** chunk quality review, `analyze_chunk.md`, `brain.md`, ambiguous
  source-language interpretation, exact sub-asset judgment, final `O/N/U/UNCERTAIN` call
  extraction, conflict review, and confidence-threshold tuning after pilot evaluation.

Claude Code and Codex CLI both read PDFs natively, so source-type does not force an
engine. Run the pilot on one engine (or split it) and let the frozen-output comparison
decide the Claude-vs-Codex routing for the full batch.

## Build order (3 phases)

- **Phase 1 — deterministic spine + run scaffolding.** `taxonomy.py` (+ unit test over all
  396 leaves), `ingest.py` (thin: snapshot + chunk boundaries), `confidence.py`,
  `assemble.py`, `llm.py` (headless subprocess + JSON repair-retry), `run.py`,
  `requirements.txt`, `DESIGN.md`/`REGISTRY.md` skeletons. Run ingest on the 4–5 pilot
  sources and **manually verify**: snapshots saved and non-empty (quote-check corpus),
  locators + chunk boundaries sane, and the native page-range chunks render view
  tables/grids legibly — before any LLM step.
- **Phase 2 — LLM analyze + first output (BLIND).** Author `analyze_chunk.md` (full
  taxonomy embedded, granularity + semantic-snapping rules, `evidence_kind` + visual-locator
  rules) + `brain.md` (pilot sources excluded; built in a separate session from the pilot
  run); run analyze over pilot chunks with rolling memory → validate/score/assemble →
  `output.csv` + `failures.csv` + `manifest.md`. The agent must **not** be given the
  ground-truth results during this phase. Freeze the output, then go to Evaluation.
- **Phase 3 — scale.** Run a full ≤20-item batch on `Target Ingestion List.csv`; produce one
  run-level output file. *(v2 backlog: cheap checker agent, more API providers.)*

## Evaluation (runs AFTER the output is frozen)

The pilot is a blind test. Ground truth is introduced only here, never during generation:

- **Primary: user review.** The user opens the frozen `output.csv` and checks it against
  their saved originals — does each call's view, leaf, and citation hold up?
- **Optional automated diff.** A standalone `eval.py` (separate entry point, run only after
  freeze) loads the held-back ground truth and reports view-agreement, leaf-match,
  quote-verbatim, and uncertain rates — **and missed calls** (ground-truth calls the
  pipeline never emitted; for this product a missed call is likely costlier than a wrong
  one). `UNCERTAIN` outputs score as *abstain* (reported separately as coverage), not as
  wrong, since the ground truth contains no uncertains. It reads the frozen output; it
  cannot influence it. `eval.py` also doubles as the engine bake-off harness: same prompts,
  same frozen pilot, swap the `llm.py` engine, compare rates.
- Tuning the threshold/prompt happens *between* runs based on eval results — re-run blind,
  re-evaluate. Never edit a single run's output to match.

## Verification (engineering correctness, independent of ground truth)

- Taxonomy unit test: every locked leaf round-trips to its category/asset-class/canva; an
  unknown label is rejected.
- Ingest spot-check: PDF page locators and HTML anchors point at the real text; snapshots
  exist and are non-empty; view tables/grids legible in native chunks; seismic / `read://`
  URLs resolve.
- Quote-check unit test: the normalization spec (NFKC, dehyphenate line breaks, collapse
  whitespace, straighten quotes) passes known-good quotes containing ligatures/hyphenation;
  a fabricated quote fails.
- Confidence unit test: explicit + evidence-check pass → High; evidence-check fail →
  `failures.csv` with reason code (never `UNCERTAIN`); conflict → flagged; table/visual
  evidence without a specific table/figure locator → flagged.
- Output shape: `output.csv` columns exactly match `Target Output.csv`; one-hot maps
  `U→(1,0,0) N→(0,1,0) O→(0,0,1) UNCERTAIN→(0,0,0)`; every dropped candidate appears in
  `failures.csv` (counts reconcile: candidates = kept + failed).

## Open items needing user input
- Pilot inputs are **provided** (`prev-excel/pilot.csv` + 3 local PDFs). **Ground truth is
  NOT supplied to the building agent** — keep it held back for your post-run review (and for
  `eval.py` if used).
- OK to install Python deps (`pdfplumber`, `trafilatura`) locally.
- Reconcile source count with client: user has said **38** sources; the workbook CSV has
  **37** rows — one row may have been dropped in export.
- Output date-format policy: `Target Ingestion List.csv` uses `DD/MM/YYYY` with many
  blanks; pilot/prev-excel use `M/D/YYYY`. Pick one output format; `htmldate` (installed)
  is the fallback for blank `Published At`.
