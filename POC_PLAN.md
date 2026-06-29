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
    ingest.py                  # resolve source URL (MR redirect / read:// / seismic) + detect type + fetch + chunk w/ locators
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
    manifest.md                # lists every source processed + counts + flags
  DESIGN.md                    # pipeline + data contract + confidence rubric + API-porting notes (one doc)
  requirements.txt
```

Deps: `pdfplumber` (PDF text + page numbers), `trafilatura` (HTML main-content, `requests`
fallback). Stdlib `csv`/`json` otherwise.

## Pipeline stages (deterministic code vs LLM)

1. **Ingest** — *deterministic* (`ingest.py`). Resolve the source URL (user supplies real
   URLs for MR-redirect pilot rows; unwrap `read://…?url=`, decode seismic
   `PLUSSIGN`/`___`, strip tracking params); detect PDF vs HTML; fetch (PDF via
   `pdfplumber` keyed by page, HTML via `trafilatura`, WebFetch fallback noted in manifest);
   chunk — PDF 5 pages/chunk, HTML ~6–8k chars/chunk — each chunk tagged with a locator
   (`p.N` or `char:start-end`).
2. **Analyze chunk** — *the one LLM step* (`prompts/analyze_chunk.md`), guided by
   `brain.md` few-shots. Input: one chunk + the source's rolling `memory.md`. In a single
   call it finds candidate calls, snaps each to a locked sub-asset leaf, and assigns
   `view` (`O/N/U/UNCERTAIN`) with the explicit-vs-implied flag, a **verbatim** evidence
   quote, reasoning, and locator. Output: candidate-call JSON (the contract below) + a
   one-paragraph chunk summary appended to `memory.md`. *(Extract → map → call are one
   prompt, not three round-trips; the deterministic guardrails below catch what it gets
   wrong.)*
3. **Validate + score** — *deterministic* (`taxonomy.py` + `confidence.py`). Reject any
   label not among the 396 leaves (drop or mark `needs-review`); confirm the evidence quote
   appears verbatim in the source text; then compute the confidence score/band and decide
   `UNCERTAIN`/review flags. The LLM never sets the score.
4. **Assemble** — *deterministic* (`assemble.py`). Merge/dedup candidates for the same leaf
   across chunks (conflicts resolved via the rubric), deterministically fill
   category/asset-class/canva, write `output.csv` (+ the one-hot `U/N/O` columns) and
   `manifest.md`.

## Inter-step data contract

One JSON object per candidate call (a dataclass in `assemble.py`; this schema is the LLM's
output format) — token-efficient, explicit:

```json
{
  "source_id": "aberdeen-em-q2-2026",
  "chunk_id": "p11-15",
  "sub_asset_raw": "Taiwanese equities",     // model's words, pre-snap
  "sub_asset_class": "Taiwan Equities",       // snapped locked leaf (validated)
  "taxonomy_match": "exact|normalized|none",
  "view": "O|N|U|UNCERTAIN",
  "call_language": "explicit|implied|none",
  "evidence_quote": "verbatim sentence(s) from the source",
  "locator": "p.13" ,                          // or "char:8200-8900"
  "reasoning": "one sentence why this view",
  "conflict": false
}
```

Deterministic fields added downstream: `quote_found_in_source` (bool), `confidence`,
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
| `evidence_quote` found verbatim in normalized source | 25 / 0 (+hallucination flag) |
| `taxonomy_match` = exact / normalized / none         | 20 / 5 / reject |
| No cross-chunk conflict for this leaf                 | 15 (−10 if conflict) |
| Source extraction quality (length/clean/page parsed)  | 10 / 0 |

Bands & balanced policy: **≥75 High** keep call; **50–74 Medium** keep + review flag;
**<50, or quote not found, or `taxonomy_match=none`, or unresolved conflict → `view`
becomes `UNCERTAIN` + review flag.** Threshold lives in one constant so it is tunable after
the pilot. (Documented in `DESIGN.md`.)

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

## Claude-Code-now → API-later boundary

All LLM I/O goes through one thin adapter (`src/llm.py`, `call(prompt_file, inputs) ->
json`). In v1 the engine is Claude Code / Codex executing the documented prompt; porting =
swapping the adapter body for an Anthropic SDK call with the **same** prompt files and
**same** JSON contract. Prompts live only in `prompts/*.md` (never inline), indexed in
`REGISTRY.md` with rationale + version; `DESIGN.md` notes the suggested model tier.

## Build order (3 phases)

- **Phase 1 — deterministic spine + run scaffolding.** `taxonomy.py` (+ unit test over all
  396 leaves), `ingest.py`, `confidence.py`, `assemble.py`, `llm.py` stub, `run.py`,
  `requirements.txt`, `DESIGN.md`/`REGISTRY.md` skeletons. Run ingest on the 4–5 pilot
  sources and **manually verify** extracted text + locators + chunk boundaries before any
  LLM step.
- **Phase 2 — LLM analyze + first output (BLIND).** Author `analyze_chunk.md` + `brain.md`
  (pilot sources excluded from the brain); run analyze over pilot chunks with rolling memory
  → validate/score/assemble → `output.csv` + `manifest.md`. The agent must **not** be given
  the ground-truth results during this phase. Freeze the output, then go to Evaluation.
- **Phase 3 — scale.** Run a full ≤20-item batch on `Target Ingestion List.csv`; produce one
  run-level output file. *(v2 backlog: cheap checker agent, more API providers.)*

## Evaluation (runs AFTER the output is frozen)

The pilot is a blind test. Ground truth is introduced only here, never during generation:

- **Primary: user review.** The user opens the frozen `output.csv` and checks it against
  their saved originals — does each call's view, leaf, and citation hold up?
- **Optional automated diff.** A standalone `eval.py` (separate entry point, run only after
  freeze) loads the held-back ground truth and reports view-agreement, leaf-match,
  quote-verbatim, and uncertain rates. It reads the frozen output; it cannot influence it.
- Tuning the threshold/prompt happens *between* runs based on eval results — re-run blind,
  re-evaluate. Never edit a single run's output to match.

## Verification (engineering correctness, independent of ground truth)

- Taxonomy unit test: every locked leaf round-trips to its category/asset-class/canva; an
  unknown label is rejected.
- Ingest spot-check: PDF page locators and HTML anchors point at the real text; seismic /
  `read://` URLs resolve.
- Confidence unit test: explicit+quote-found → High; missing quote → UNCERTAIN; conflict →
  flagged.
- Output shape: columns exactly match `Target Output.csv`; one-hot maps
  `U→(1,0,0) N→(0,1,0) O→(0,0,1) UNCERTAIN→(0,0,0)`.

## Open items needing user input
- Pilot inputs are **provided** (`prev-excel/pilot.csv` + 3 local PDFs). **Ground truth is
  NOT supplied to the building agent** — keep it held back for your post-run review (and for
  `eval.py` if used).
- OK to install Python deps (`pdfplumber`, `trafilatura`) locally.
