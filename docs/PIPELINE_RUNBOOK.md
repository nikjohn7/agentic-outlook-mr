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

Headless Codex calls include `-c features.hooks=false` per process. Interactive
lifecycle hooks can append a new question after a reader's JSON answer and
replace the extracted candidates with the answer to that question. This does
not edit global configuration or change the selected model/effort or sandbox.
See the [October quality validation record](run-records/oct2026-quality-validation.md)
for the observed failure and the revised split launch commands. A zero Python
exit requires a manifest check: engine/chunk failures are audited and can still
produce a completed run with no useful calls.

Sanity check before a production batch:

```bash
.venv/bin/python -m unittest discover -s tests
```

### Model matrix (the selected model choices)

The code defaults ARE the selected configuration (revamped 2026-10-23, regression
net `tests/test_model_matrix.py`). A bare invocation of any tool — no
engine/model/effort flags — resolves to this matrix. Only pass model flags to
deliberately deviate.

| Step | Engine | Model | Effort |
|---|---|---|---|
| analyze (per-chunk extraction) | codex | gpt-6.1-sol | high |
| checker (per-source second reader) | claude | claude-opus-5-5 | high |
| conflict arbiter | claude | claude-haiku-5-5 | high |
| group-notes resolver | codex | gpt-6-luna | high |
| tier-3 visual quote verifier | codex | gpt-6-luna | high |
| scout (pre-run grouping proposals) | codex | gpt-6-luna | medium |
| preflight content-sanity call | claude | claude-sonnet-5-5 | low |
| reconcile scope gate (+ near-leaf pass, inherited) | claude | claude-opus-5-5 | medium |
| datefill primary | codex | gpt-6-luna | high |
| datefill cascade (fallback) | claude | claude-sonnet-5-5 | medium |
| crosscheck (report only) | claude | claude-sonnet-5-5 | medium |
| summarize digest | claude | claude-sonnet-5-5 (pinned) | medium |
| summarize firmpages | claude | claude-sonnet-5-5 (pinned) | high |
| shared-evidence gate (in-run, and `src.sharedevidence` CLI) | claude | claude-opus-5-5 | medium |

Claude stages use full model IDs, not aliases (`opus`, `sonnet`), so a batch
cannot silently change model when the CLI's alias moves.

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
  dates come only from the documents themselves, client decision 11),
  `local_file` and `pdf_file` (section 1.7).
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
  `<batch>/manual-sources/` and put its path in `local_file`. PDFs, `.txt`
  transcripts and Markdown (`.md`) are supported. When `local_file` is set the URL is kept for
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
  --out <batch>/scout/group-notes.md
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

### 1.7 Markdown + PDF sources

When the client supplies each document as Markdown plus the original PDF, the
source CSV needs both columns on every row: `local_file` (the `.md`, read as
the document) and `pdf_file` (the paired `.pdf`). Paths are relative to the
repo root; a missing file or a `pdf_file` that is not a `.pdf` stops the load.

In a run, each such source is read as its Markdown text plus only the PDF
pages flagged by a deterministic inventory (graphics-heavy pages, or pages
whose words are under 90% covered by the Markdown). Those pages are read as
rendered pages in `pdf:` chunks after the Markdown chunks, and their quotes
are validated against the PDF only (Markdown quotes against the Markdown
only). A call read from the PDF ends its Locator with `(read from PDF)`.

Preview the flagged pages before a run (no model calls):

```bash
.venv/bin/python -m src.pdfinventory \
  --sources <batch>/splits/split-N.csv \
  --out <batch>/pdf-inventory/split-N
```

It writes `pdf-inventory.csv` (one row per PDF page, with every measure) and
`pdf-inventory-summary.md` (flagged pages per document). Every row must have a
Markdown `local_file` and a `pdf_file`.

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

1. **Ingest** (`src/ingest.py`) — detect type (PDF / HTML / txt / md); fetch with
   retries and a browser fallback for blocked pages; visual-heavy HTML is
   printed to PDF headlessly so charts are readable; image-only PDF pages are
   OCR'd; scrambled text layers detected; per-page snapshots + chunking;
   document date extracted (HTML via htmldate; PDF first-page worded-date
   scan; strict DD/MM/YYYY or blank — CSV dates and PDF metadata never used).
   An ingest failure becomes an `ingest_error` failure row; the run continues.
   A Markdown source with a `pdf_file` also gets `pdf:` chunks for its flagged
   PDF pages (section 1.7).
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
   segments); O-vs-U sibling tripwire; page check (rows whose source has a
   `pdf_file`: each Markdown quote keeps a page only when it is found on
   exactly one page of the PDF text, otherwise the page is removed with a
   reviewer note; a `(read from PDF)` passage keeps its page); writes the run
   files.

### 2.3 Per-run outputs (in `<batch>/<run-id>/`)

- `output.csv` — kept calls: the 10 workbook columns + `confidence`, `band`,
  `review_flag`, `basis`, `checker_strength`, `call_language`, `quote_match`,
  `evidence_origin` (`markdown` | `pdf` | `both`, from the row's retained
  passages; internal only).
- `failures.csv` — internal: every dropped candidate with reason codes.
- `failures-client.csv` — the client-facing subset, plain-language "What
  happened" labels, grouped and sorted most-important-first.
- `output-client.csv/.xlsx`: the client twin (section 3.4).
- `manifest.md` — per-source counts, tier counts, run config (the manifest
  records exactly which models ran), and `## Provenance`: the code commit,
  uncommitted changes under `src/`, `prompts/` and the taxonomy, one
  implementation hash over all of them, and each prompt's and the taxonomy's
  hash (`src/provenance.py`; recorded, never blocking). Compare two runs by
  their implementation hash. With paired PDFs it also lists PDF chunks
  and flagged pages per source, kept rows by evidence origin, and a
  `## Page check (paired PDFs)` section.
- `pagecheck-audit.csv`: paired-PDF runs only: one row per checked quote
  with its outcome (`confirmed`, `corrected`, `added`, `ambiguous`,
  `not_found`, `pdf_read`, `no_pdf`).

### 2.4 After each split finishes

The page check for Markdown + PDF sources runs inside every run (section 2.2,
step 8), so the run's own `output.csv` is what combine reads. For a Markdown
run made before the check was built in (code before `c9fb437`, e.g. the
October split 1 trial), the standalone CLI writes a page-checked copy (no
model calls; never edits the run):

```bash
.venv/bin/python -m src.pagecheck \
  --output <batch>/<run-id>/output.csv \
  --sources <batch>/splits/split-N.csv \
  --out-dir <batch>/<run-id>/pagecheck
```

Run the digest step (1 LLM call per source; feeds the firm pages later).
Every run writes into the same flat `<batch>/digests` folder (one
`<source_id>.json` per source; source ids are firm + title slugs, so runs do
not collide), which is the folder `firmpages --digests` reads:

```bash
.venv/bin/python -m src.summarize digest \
  --run <batch>/<run-id> \
  --out-dir <batch>/digests
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

- `output.csv` — the reconciled master (internal: every column).
- **`output-client.csv` + `output-client.xlsx` — what the client receives.**
  Written automatically next to every `output.csv` (run, reconcile) and on
  demand by `python -m src.export --output <output.csv> --out-dir <dir>
  [--failures <failures-client.csv>]`. Client feedback (2026-10) fixed here, so
  it can never recur: `Full Commentary` is split into `Reasoning` /
  `Verbatim Evidence` / `Locator` / `Reviewer Notes`; internal diagnostics
  (`basis`, `checker_strength`, `call_language`, `quote_match`) are dropped;
  HTML/transcript `char:` locators are blank; **one row per firm and asset**
  (a same-firm, same-asset pair is resolved like reconcile does — same view
  merged, conflicting views by recency → basis → confidence, the loser a
  "Superseded" failure row, the kept row noting the other view; only a tie
  keeps both, flagged); **`review_flag` means a judgment** (caps such as
  inferred basis or a degraded quote match are carried by `band` only, so
  the flagged set is the real analyst queue); files are BOM-prefixed UTF-8
  with single-line cells (Excel on Windows rendered the old files as mojibake
  and split rows on embedded newlines), plus a native `.xlsx` twin.
- `failures-client.csv` (+ `.xlsx`) — all dropped/merged rows with
  plain-language labels, importance-sorted (labels explained in
  `output-guide.html`). Combine writes the `.csv` only and reconcile's
  automatic export writes no failures file, so finish every batch with one
  explicit export over the combined folder; it rewrites both client files
  (BOM + `.xlsx`) and folds in any shared-key losers:

  ```bash
  .venv/bin/python -m src.export --output <combined>/output.csv --out-dir <combined> \
      --failures <combined>/failures-client.csv
  ```
- `output.pre-reconcile.csv`, `output.dated.csv` — kept intermediates.
- `manifest.md` — per-split counts + reconcile provenance.
- internal: `failures.csv`, audit CSVs.

**Shared-evidence gate.** Inside every run (`src.run`, on by default;
`--no-evidence-gate` skips it) one passage cited for several sub-asset classes
keeps only the call(s) it most compellingly supports — an LLM picks
categorically, deterministic code applies, losers land in the failures file as
"Evidence better supports a related asset", survivors that still share a
passage are flagged. For an output produced BEFORE the gate existed (the July
master), run it post-hoc — it writes new files, never edits in place:

```bash
.venv/bin/python -m src.sharedevidence --output <output.csv> --out-dir <dir> --dry-run   # counts only
.venv/bin/python -m src.sharedevidence --output <output.csv> --out-dir <dir>             # ~20 groups per LLM call
```

The gated `<dir>/output.csv` gets an `output-client.csv/.xlsx` twin, but the
gate's dropped rows stay in `<dir>/shared-evidence-failures-client.csv`. Fold
them into the batch's `failures-client.csv` (label-block order), then re-run
the export with that folded file:

```bash
.venv/bin/python -m src.export --output <dir>/output.csv --out-dir <dir> \
    --failures <folded failures-client.csv>
```

### 3.5 Optional reader deliverable — firm pages + Word binder

```bash
# 1 call per firm; --digests is one flat folder of every split's digest JSONs.
.venv/bin/python -m src.summarize firmpages --digests <batch>/digests \
  --outputs <combined>/output.csv \
  --crosscheck <combined>/firmpages-crosscheck-empty.csv \
  --out-dir <batch>/firmpages
.venv/bin/python -m src.summarize bind --pages <batch>/firmpages --out <combined>/firm-summaries.docx
```

`firmpages` refuses a firm with several digests unless `--crosscheck` is
given. Reconcile has already settled each firm and asset, so pass a
header-only file (the July master precedent):
`printf 'Firm,Sub-Asset Class,bucket,agent_verdict,note,needs_human\n' > <combined>/firmpages-crosscheck-empty.csv`.
Any pair reconcile kept both of shows on the firm page as a divergence note.
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
