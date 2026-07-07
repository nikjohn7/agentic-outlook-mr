# Markets Recon / Allocator Pro POC — State

_Last updated: 2026-07-07_

## Current State

POC for Markets Recon / Allocator Pro: ingest fund/asset-manager outlook sources
(PDF/HTML) and produce reviewable sub-asset-class allocation calls (`O`/`N`/`U`/
`UNCERTAIN`) with citations, against the locked taxonomy in `excel-file/Asset
Class List - Locked.csv`. Full spec: `CLAUDE.md`, `POC_CONTEXT.md`,
`WORKBOOK_SCHEMA.md`, `POC_PLAN.md`; the client's binding decisions and the
v1.2/v2 backlog live in `ROADMAP.md`. Deterministic joins, lookups, and
arithmetic (taxonomy, confidence, assembly) never route through a model; LLM
tiers are reserved for judgment-heavy extraction and review, and always return
categorical judgments, never numbers.

The deterministic spine: `src/taxonomy.py` (exact 396-leaf validation),
`src/schemas.py` (LLM candidate contract; `basis` stated/forecast_delta/
inferred; multi-span `evidence_quote`), `src/confidence.py` (verbatim quote /
table-visual key-token evidence gates, Rubric v2 scoring off the checker's
categorical `evidence_strength`, read-quality floors, and degraded-prose paths
for scrambled and OCR pages: key-token fallback, cap 74, forced review),
`src/assemble.py` (`output.csv`/`failures.csv`/`failures-client.csv`/
`manifest.md`; cross-leaf dedup; deterministic stated-beats-implied with
`implied_challenges_stated` logged; O-vs-U sibling tripwire), `src/ingest.py`
(header-alias CSV loading with optional `local_file`, snapshots, chunking,
visual-heavy detection + Playwright print-to-PDF, scrambled-page detection,
retry-hardened fetches with browser fallback for blocked HTML, per-page OCR
for image-only PDFs, document-date extraction), and `src/llm.py` (headless
`claude -p` / `codex exec`, `{{name}}` template vars). Dates are
document-only (client decision 11): the CSV date is discarded; HTML via
htmldate, PDF via a first-page worded-date scan; strict DD/MM/YYYY or blank;
`date_from` ∈ {html, pdf_text, ""}; PDF metadata never used (carries capture
dates). Output rows are the 10 workbook columns plus `confidence`, `band`,
`review_flag`, `basis`, `checker_strength`, `call_language`; grouped rows
pipe-join Source/URL and non-blank Dates; one-hot columns intentionally
omitted. `output-guide.html` (repo root) explains the output and
failures-client files to the client. A `.claude/settings.json` hook blocks
commits containing Claude/Anthropic self-attribution — commit messages stay
plain. Tests: `.venv/bin/python -m unittest discover -s tests` (the `-s
tests` is required); 291 pass (1 skip).

The run pipeline (`src/run.py`, ≤20 sources per run, `--out-root` to redirect
artifacts) runs up to four LLM steps with per-step engine/model/effort flags:
analyze (per-chunk, rolling `memory.md`, `prompts/analyze_chunk.md` +
`conventions.md` + `brain.md`), checker (`check_candidates.md`, one call per
source, sees the whole-file memory; any `fail` hard-fails, `thin` caps below
High), conflict arbiter (`arbitrate_conflict.md`), and a group-notes resolver
(`resolve_groups.md`). Both engine CLIs read PDF pages visually, so image
content reaches the model even when snapshot text is thin. The proven
production config (pilot-05 onward): analyze codex/gpt-5.5/high, checker
claude/opus/medium, arbiter claude/sonnet/high, grouper claude/sonnet/medium.
Prompts are versioned in `prompts/REGISTRY.md`.

Standalone tools around the pipeline: `src/eval.py` (deterministic GT
comparison harness + judgment worksheet; never influences a run),
`src/scout.py` (layer 1 of grouping: pre-run metadata-only companion
proposals emitting a `--group-notes` file; conservative — same firm alone
never groups), `src/crosscheck.py` (layer 3: post-run same-firm (firm, leaf)
overlap report across any number of run outputs; same-view auto-marked, view
conflicts get one batched categorical pass, degrade to `needs_human`),
`src/preflight.py` (fetch-only sweep of a source CSV, no run cap, one batched
content-sanity call, `preflight.csv` + report), and `src/summarize.py`
(reader summaries: per-source `digest` → deterministic reconcile →
per-firm `firmpages` (claude/sonnet/high, no em dashes, v1.1) → `bind` to a
python-docx Word binder; sample approved by Nikhil, opus/medium is the
escalation if a page reads flat).

Run history (all blind; GT never opened during a run): pilots 01–06 over the
7-doc pilot set and `test2-01` over 7 new firms drove the hardening arc —
multi-span quotes, scrambled-page rescue, the visual/dial evidence route for
print-captured grids, Rubric v2, the inference tier, cross-leaf dedup, the
reduce/neutralize→resulting-stance convention, and country-granularity
inference. The materiality gate is code-live but CLOSED as unexercised (0
forecast_delta in three runs). Quality at close of phase 2: pilot-06 true
recall 85.4% (view agreement 92.5%, overreach 1/106); test2-01 raw recall
76.4% / grounded-adjusted ~90.4%, view agreement 92.6%, quote check 142/142,
precision 1 overreach / 74 model_only. GT sets: `ground-truth/pilot-*.csv`
(82 rows), `test2-ground-truth.csv` (89 rows; carries ≥1 known GT error + 6
not-grounded rows pending analyst reconciliation). Frozen in git:
`runs/test2-01`, `runs/test2-01-rescored`, `runs/pilot-05`, pilot-06
judgment artifacts; disk-only: `runs/pilot-04`(+`-rescored`); `work/` keeps
only `test2-01`. `runs/` and `client-runs/` are gitignored; frozen artifacts
are force-added.

Production batch state: the ~37-source batch (18 firms; Aberdeen ×7, Invesco
×6, State Street ×5, Columbia ×3, PGIM ×2, Impax ×2 + 12 singles) runs as 4
splits (10/9/9/9, every multi-source firm whole within one split) under
`client-runs/runs-07072026-37rows/` via `--out-root`. Preflight-3 over the
local_file-wired list: 37/37 fetch-safe (12 manual PDFs wired incl. the
image-only Manulife, OCR-validated 36 → 25,733 chars, date found), 36
`looks_right` + 1 transient JPM error since wired local. Scout over the 37:
0 groups proposed (all same-firm sources are distinct desk pieces). The
operator command sheet is `tmp/37run-commands.md`; splits regenerate from the
client's final CSV when it arrives. A second ~70-source batch follows; both
batches combine into ONE deliverable (combined output CSV + crosscheck across
all outputs + firm pages/binder at the combine step). Runs launch under
`nohup` (a wrapper teardown killed a run once on macOS), ≤2 parallel,
staggered. `.venv` holds pdfplumber, pdfminer.six, trafilatura, htmldate,
playwright (+ chromium), python-docx; Tesseract 5.5.2 + Poppler for OCR.

## Recent Changes

- 2026-07-07: Split CSVs rebuilt from the local_file-wired master (the four
  splits predated the manual-download wiring and pointed 11 rows at blocked
  URLs; rebuilt by firm — 11 workbook rows have blank Ids, so Ids are
  unusable as keys). J.P. Morgan's PDF (transient connection error in
  preflight-3) re-downloaded and wired as the 12th `local_file`. Cost-slice
  instructions (set 8) updated to read preflight-3 and include Manulife (the
  only source exercising the OCR evidence-gate path live).
- 2026-07-07: Ingest hardening (set 9, commit 93ad0e9): fetch retries (3
  attempts, 90s, backoff, no 4xx retry), Playwright HTML fallback on
  401/403/406/429 with shared consent handling (`fetched_via` in meta), and
  per-page OCR (pdftoppm + Tesseract) for image-only PDFs with `ocr_pages`
  threaded into the evidence gate (scrambled-style cap + review). Preflight-3:
  Eastspring timeout fixed, AEW + Manulife suspect → looks_right. Suite 279 →
  291.
- 2026-07-07: `--out-root` for run.py, the `client-runs/` convention, and
  `src/preflight.py` (set 6). First live 37-sweep: 30 ok / 7 blocked (6
  Invesco 406, Manulife 403) / 3 consent-wall suspects; 17/30 dated, all from
  HTML — led to manual local_file wiring and set 9. Suite 268 → 279.
- 2026-07-07: 37-batch prep: split CSVs generated, run/scout/digest/
  crosscheck commands filled out in `tmp/37run-commands.md`; repo cleanup
  194M → 22M (pilot work/ dirs and tmp scratch deleted; pilot-05 eval
  spot-check now skips without its snapshots); `runs/test2-01` +
  `runs/pilot-05` force-added; `summarize_firm_page.md` v1.1 (no em dashes).
- 2026-07-07: Document-only dates (set 5; supersedes the same-day CSV-fallback
  version): ingestion always extracts the date from the document and discards
  the CSV value; strict DD/MM/YYYY or blank. Plus `failures-client.csv`
  (plain-language labels for every internal reason_code, mapping
  test-enforced complete) and `output-guide.html`. Suite 260 → 268.
- 2026-07-07: Reader summaries shipped (set 4): `src/summarize.py`
  digest/reconcile/firmpages/bind + python-docx binder; smoke over test2-01
  produced client-example-quality pages; sample later approved. Suite 250 →
  260 (incl. the document-date fallback tests folded into set 5's policy).
- 2026-07-07: Checker-context wave (set 1): checker sees whole-file memory
  (`{{memory}}`), implied calls always analyzed and never silently dropped,
  deterministic stated-beats-implied with logged `implied_challenges_stated`
  recommendation, dial-vs-commentary convention line (`conventions.md` v1.3,
  `check_candidates.md` v1.7, `brain.md` v1.6).
- 2026-07-07: Grouping layers 1 and 3 shipped (sets 2–3): `src/scout.py`
  pre-run companion scout (37-list smoke: 0 groups, correctly conservative)
  and `src/crosscheck.py` post-run firm cross-check (join key imported from
  `src.eval`). Client's written answers recorded in `ROADMAP.md` the same
  day. Suite 201 → 235.

## Next / Open

- **Cost slice (set 8) is the GO/NO-GO gate before the splits**: launch
  `tmp/instructions-8-cost-slice.md` (3 sources incl. Manulife, production
  config, cost + first live pass of everything shipped 2026-07-07), review,
  then Nikhil runs the four split commands from `tmp/37run-commands.md`
  (≤2 parallel). Regenerate splits + re-run preflight/scout when the client's
  final CSV arrives (wire local_file first).
- Second ~70-source batch → one combined deliverable (combined CSV,
  crosscheck across all batch outputs, firm pages + Word binder at the
  combine step).
- `runs/pilot-04` + `runs/pilot-04-rescored` remain disk-only (freeze
  pending user decision).
- GT reconciliation with the Markets Recon team: test2 GT has ≥1 error (TRP
  UK IG Credit) + 6 not-grounded rows (`tmp/gt-reconciliation-test2.md`);
  earlier pilot GT disputes sent 2026-07-04, awaiting response.
- Reconcile source count with client (user says 38, workbook CSV has 37).
- Still open: fuller analyst-reviewed ground-truth set; acceptable model
  providers. Deferred work is tracked in `ROADMAP.md` (v1.2/v2 backlog).
