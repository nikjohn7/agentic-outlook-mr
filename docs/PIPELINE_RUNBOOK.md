# Allocator Pro POC — Pipeline Runbook

_Internal reference, written 2026-07-13. This is the exact, current process for
taking a client source list through the pipeline to a reviewable deliverable.
A client-facing "how to reproduce this" document should be derived from this
file (strip the internal-review notes and the repo-history asides; keep the
commands)._

Everything below is run from the repo root. `python` means `.venv/bin/python`.

---

## 0. One-time environment setup

Prerequisites (already satisfied on the current machine):

- Python virtualenv at `.venv` with: `pdfplumber`, `pdfminer.six`,
  `trafilatura`, `htmldate`, `playwright` (+ `playwright install chromium`),
  `python-docx` (see `requirements.txt`).
- Tesseract (5.5.2) and Poppler on the PATH — used only for image-only PDF
  pages (OCR).
- The two engine CLIs, installed and authenticated:
  - `claude` (Claude Code CLI) — runs headless via `claude -p`.
  - `codex` (Codex CLI) — runs headless via `codex exec`.
- The locked taxonomy at `excel-file/Asset Class List - Locked.csv` (396
  leaves). This file is the authority for every taxonomy field in the output;
  it is never edited.

Sanity check before a production batch:

```bash
.venv/bin/python -m unittest discover -s tests
```

### Model matrix (the selected model choices)

The code defaults ARE the selected configuration (wired 2026-07-10, regression
net `tests/test_model_matrix.py`). A bare invocation of any tool — no
engine/model/effort flags — resolves to this matrix. Only pass model flags to
deliberately deviate.

| Step | Engine | Model | Effort |
|---|---|---|---|
| analyze (per-chunk extraction) | codex | gpt-5.6-sol | high |
| checker (per-source second reader) | claude | opus | medium |
| conflict arbiter | codex | gpt-5.6-luna | high |
| group-notes resolver | claude | haiku | high |
| tier-3 visual quote verifier | codex | gpt-5.6-luna | high |
| scout (pre-run grouping proposals) | codex | gpt-5.6-luna | medium |
| preflight content-sanity call | codex | gpt-5.6-luna | high |
| reconcile scope gate (+ near-leaf pass, inherited) | claude | opus | medium |
| datefill primary → cascade | codex gpt-5.6-luna high → claude sonnet medium |
| crosscheck (report only) | claude | sonnet | medium |
| summarize digest | claude | claude-sonnet-5 (pinned) | high |
| summarize firmpages | claude | claude-sonnet-4-6 (pinned) | high |

Everything that is a join, lookup, count, score, or merge rule is plain
deterministic code — no model ever produces a number (confidence comes from
the rubric in `src/confidence.py`) and no model ever invents a taxonomy label
(`src/taxonomy.py` validates every leaf against the locked list, exact).

---

## 1. BEFORE the run

### 1.1 Receive and place the client source list

- The client provides a CSV of sources. Required columns (header aliases are
  accepted, case-insensitive): **Firm** (`Firm`/`Entity Name`/`Manager`/…),
  **Title** (`Title`/`Source`/`Document Title`), **URL**
  (`Source Link`/`URL`/`Link`/`External link`). Optional: `Date` (ignored —
  dates come only from the documents themselves, client decision 11) and
  `local_file`.
- Drop the file, under the client's original name, into `excel-file/`
  (canonical received copy — never edited).

### 1.2 Create the batch folder

All artifacts for one client batch live under one folder:

```
client-runs/runs-<DDMMYYYY>-<N>rows/
```

(e.g. `client-runs/runs-07072026-98rows/` for the first batch). Everything
below — the wired master CSV, preflight, scout, splits, per-split runs, logs,
datefill, reconcile, combined deliverable — goes inside it.

### 1.3 Wire the working master CSV

Copy the received CSV into the batch folder and add a `local_file` column:

- For any source that cannot be fetched (paywalled, bot-blocked, link-rot) or
  that the client supplied as a file, place the file under
  `<batch>/manual-sources/` and put its path in `local_file`. PDFs and `.txt`
  transcripts are both supported. When `local_file` is set the URL is kept for
  the output row but never fetched.
- The wired copy is named `<original name> (with local_file).csv` and is the
  single source of truth for the batch.

### 1.4 Preflight — verify every link before spending run money

```bash
.venv/bin/python -m src.preflight \
  --sources "<batch>/<master>.csv" \
  --out-dir <batch>/preflight
```

Fetch-only sweep (no 20-row cap): fetches every row, snapshots content, and
makes ONE batched LLM content-sanity call (does the fetched content plausibly
match the row's firm/title?). Produces `preflight.csv` + a report.

Review loop: fix broken links (with the client if needed), download manual
copies for blocked sources into `manual-sources/` and wire them into
`local_file`, drop rows the client confirms are dead or wrong-content (e.g. a
link now serving a prior year's outlook), then re-sweep only the failures
until every remaining row is fetch-safe and content-verified.

### 1.5 Scout — propose companion-document groups

```bash
.venv/bin/python -m src.scout \
  --sources "<batch>/<master>.csv" \
  --out-dir <batch>/scout
```

Metadata-only (nothing is fetched): proposes which same-firm rows are parts of
ONE publication (e.g. a four-region "Midyear Outlook", or a Credit + Rates
pair of the same series) and writes `scout/group-notes.md`. Deliberately
conservative — same firm alone never groups.

**Human gate:** read `group-notes.md`, accept or strike each proposal. Only
splits containing an accepted group get the `--group-notes` flag at run time.

### 1.6 Split the batch

Hard cap: **≤20 sources per run**; production practice is 10. Split
**firm-whole** (all of a firm's rows in the same split, so in-run grouping and
the arbiter see the whole firm) into `<batch>/splits/split-N.csv`, keeping
grouped companions together.

---

## 2. DURING the run

### 2.1 Launch

One command per split. Bare = the model matrix; add the two group lines only
for splits with accepted scout groups.

```bash
nohup .venv/bin/python -m src.run \
  --sources <batch>/splits/split-N.csv \
  --run-id <batch-id>-splitN \
  --out-root <batch> \
  [--group-notes <batch>/scout/group-notes.md] \
  > <batch>/<batch-id>-splitN.log 2>&1 &
```

Operational rules (learned on the 98-row batch):

- Launch under `nohup` (a wrapper teardown once killed a run on macOS).
- **At most 2 splits in parallel**, launched a few minutes apart; watch each
  log's first minutes for rate-limit errors:
  `tail -f <batch>/<id>-splitN.log`
- Keep the machine awake (`caffeinate -i` in another terminal).
- Budget ~8 min/source → a 10-row split ≈ 1–1.5 h. Ten splits, two at a time
  ≈ 5–7 h.
- If a split dies mid-run: check the log tail, relaunch the same command
  (redoes that split only). If one source inside a split needs redoing, rerun
  it as its own mini-split and merge its rows into the split's files (the
  split-8 HSBC precedent).

### 2.2 What one run does internally (per source)

1. **Ingest** (`src/ingest.py`) — detect type (PDF / HTML / txt); fetch with
   retries and a browser fallback for blocked pages; visual-heavy HTML is
   printed to PDF headlessly so charts are readable; image-only PDF pages are
   OCR'd; scrambled text layers detected; per-page snapshots + chunking;
   document date extracted (HTML via htmldate; PDF first-page worded-date
   scan; strict DD/MM/YYYY or blank — CSV dates and PDF metadata never used).
   An ingest failure becomes an `ingest_error` failure row; the run continues.
2. **Analyze** (per chunk, rolling `memory.md`) — the extraction model
   proposes candidate calls: taxonomy leaf, view (O/N/U/UNCERTAIN), basis
   (stated / forecast_delta / inferred), multi-span evidence quotes, page.
   Both engine CLIs read PDF pages visually, so chart/table content reaches
   the model even when the text layer is thin.
3. **Deterministic gates** — leaf validated against the locked taxonomy
   (exact, no remapping); the evidence quote must be found in the source
   (tiered: exact → normalized → bounded subsequence, recorded as
   `quote_match`); table/visual evidence takes a key-token route; the
   materiality gate drops sub-threshold forecast deltas.
4. **Checker** (one call per source, sees the whole-file memory) — a second
   model grades each candidate's evidence (categorical only); any `fail`
   hard-fails the candidate, `thin` caps confidence below High.
5. **Arbiter** — resolves intra-source view conflicts; stated-beats-implied
   is deterministic, with `implied_challenges_stated` logged.
6. **Tier-3 visual quote verifier** — only for quotes that failed the
   deterministic tiers; fail-closed (verbatim kept capped + flagged for
   review; paraphrase/absent dropped).
7. **Confidence** (`src/confidence.py`) — Rubric v2, fully deterministic:
   checker strength + quote-match tier + read-quality floors → score, band,
   `review_flag`. Never an LLM self-score.
8. **Assemble** (`src/assemble.py`) — cross-leaf dedup; same-view dedup folds
   the losing citation into the kept row (`" |||| "`-separated labeled
   segments); O-vs-U sibling tripwire; writes the four run files.

### 2.3 Per-run outputs (in `<batch>/<run-id>/`)

- `output.csv` — kept calls: the 10 workbook columns + `confidence`, `band`,
  `review_flag`, `basis`, `checker_strength`, `call_language`, `quote_match`.
- `failures.csv` — internal: every dropped candidate with reason codes.
- `failures-client.csv` — the client-facing subset, plain-language "What
  happened" labels, grouped and sorted most-important-first.
- `manifest.md` — per-source counts, tier counts, run config (the manifest
  records exactly which models ran).

### 2.4 After each split finishes

Run the digest step (1 LLM call per source; feeds the firm pages later):

```bash
.venv/bin/python -m src.summarize digest \
  --run <batch>/<run-id> \
  --out-dir <batch>/digests/<run-id>
```

---

## 3. AFTER the run(s)

Order matters: **combine → datefill → reconcile**. Reconcile's recency rule
reads dates, so the date patch must land first. For the 98-row batch all of
this is wrapped in `scripts/combine-98b.py`; for a new batch, generalize that
script or run the stages by hand as below. Every stage writes NEW files —
frozen run outputs are never edited in place.

### 3.1 Combine the splits

Concatenate the splits' `output.csv` / `failures.csv` / `failures-client.csv`
(in split order) into `<batch>/<batch>-combined/output.pre-reconcile.csv` and
the two failure files.

### 3.2 Datefill — backfill missing document dates

```bash
# Report (read-only; one date-hunt agent per undated source):
.venv/bin/python -m src.datefill \
  --output <combined>/output.pre-reconcile.csv \
  --sources "<batch>/<master>.csv" \
  --out-dir <batch>/datefill

# Human gate: review datefill/datefill.csv + summary, then apply:
.venv/bin/python -m src.datefill --apply \
  --output <combined>/output.pre-reconcile.csv \
  --patch <batch>/datefill/datefill.csv \
  --write <combined>/output.dated.csv
```

Every found date is deterministically verified fail-closed (a stated quote
must reappear in the snapshot; a landing page must reference the document;
strict parsing, year-windowed). Precedence: stated-in-document → PDF metadata
(excluding browser print-capture signatures) → landing page → `01/MM` from a
month-year partial. Quarter/season partials never fill. Blank is an acceptable
outcome.

### 3.3 Reconcile — firm-level merging (exact leaf + near leaf)

```bash
.venv/bin/python -m src.reconcile \
  --outputs <combined>/output.dated.csv \
  --near-leaf \
  --out-dir <combined-or-sibling-dir>
```

Two passes, both fail-closed to `needs_human`:

1. **Exact-leaf pass** — groups rows on (firm, sub-asset-class leaf). A
   batched categorical scope gate decides `same_claim` vs `distinct_claims`
   per key. Same-view same-claim rows merge deterministically (max
   confidence; pipe-joined Source/URL/Date; `||||` labeled commentary).
   Conflicting views resolve by a precedence ladder — recency → basis
   (stated > forecast_delta > inferred) → band/confidence → `needs_human`
   (all rows kept, flagged) — never majority vote, never forced.
2. **Near-leaf pass** (`--near-leaf`, runs over the exact pass's survivors) —
   deterministic candidate generation pairs a firm's *related* locked leaves
   (structural similarity or short-label containment, e.g. `AI` ↔
   `IT/Tech/Telecomms (inc. AI)`), clusters them per firm, and a batched LLM
   partitions each cluster into collective `same_claim` (merged onto a
   canonical leaf from the cluster's own locked labels; taxonomy fields
   rebuilt from the locked list) vs `distinct` (kept). Every near-leaf
   survivor is force-flagged for review.

Outputs: reconciled `output.csv`, `reconcile-audit.csv` (per-row
dual-confidence trail), `reconcile-nearleaf-audit.csv`,
`taxonomy-coverage-review.csv` (context only), `reconcile-summary.md`, and
`merged_by_reconcile` / `superseded_by_reconcile` / `near_leaf_*` failure rows
that fold into the batch's failure files.

**Human gate:** review the audit CSVs — especially superseded rows,
cross-view near-leaf supersessions, and `needs_human` keys — before calling
the reconciled file final.

### 3.4 Final deliverable files (in `<batch>/<batch>-combined/`)

- `output.csv` — the reconciled master (the deliverable).
- `failures-client.csv` — all dropped/merged rows with plain-language labels,
  importance-sorted (labels explained in `output-guide.html`).
- `output.pre-reconcile.csv`, `output.dated.csv` — kept intermediates.
- `manifest.md` — per-split counts + reconcile provenance.
- internal: `failures.csv`, audit CSVs.

### 3.5 Optional reader deliverable — firm pages + Word binder

```bash
.venv/bin/python -m src.summarize firmpages --digests <batch>/digests --out-dir <batch>/firmpages   # 1 call per firm
.venv/bin/python -m src.summarize bind --firmpages <batch>/firmpages --out <combined>/firm-summaries.docx
```

(Digests per split must exist first; rebuild firm pages if reconcile changed
the combined output.)

---

## 4. Combining MULTIPLE client batches into one master deliverable

When a second (third, …) batch has been processed to its dated combined
output, the cross-batch merge is the SAME reconcile stage run once across all
batches — `--outputs` accepts multiple files:

```bash
.venv/bin/python -m src.reconcile \
  --outputs <batch1-combined>/output.dated.csv \
            <batch2-combined>/output.dated.csv \
  --near-leaf \
  --out-dir client-runs/<master-combined-dir>
```

Rules:

- Reconcile across batches from the **pre-reconcile dated concatenations**,
  not from already-reconciled outputs — one reconcile layer across
  everything, no stacked merges.
- Same-firm rows from different batches land in the same (firm, leaf) keys
  and cluster together automatically; the scope gate + precedence ladder and
  the near-leaf pass then merge or keep them exactly as within one batch.
- The master failures file = both batches' `failures-client.csv` rows + the
  cross-batch reconcile failure rows, re-sorted by the canonical label order.
- Result: ONE `output.csv` (final calls per sub-asset class) + ONE
  `failures-client.csv` for everything processed to date.

---

## 5. Review philosophy (what the human checks at each gate)

1. Preflight report — before any run money is spent.
2. Scout group proposals — before launch.
3. Log tails during the first minutes of each split.
4. Per-split `manifest.md` + `failures-client.csv` after each split.
5. Datefill patch before `--apply`.
6. Reconcile audits (superseded rows, cross-view merges, `needs_human`)
   before the reconciled file becomes the deliverable.
7. `review_flag` rows in the final output are the analyst's queue.
