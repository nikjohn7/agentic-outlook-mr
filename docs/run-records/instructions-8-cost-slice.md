# Instruction Set 8: paid cost/health slice before the 37-source batch

## Context (you are in a fresh session — read carefully)

This repo extracts asset-allocation calls (`O`/`N`/`U`/`UNCERTAIN`) from fund
manager documents into run-level CSVs. The ~37-source production batch is
about to run as 4 parallel splits. YOUR job is the last gate before that: one
small PAID end-to-end run over 2–3 real batch sources, serving three purposes
at once:

1. **Cost**: a per-source cost estimate for the production config.
2. **Behavioral validation**: several changes shipped since the last live run
   (checker receives whole-file memory, stated-beats-implied rule,
   dial-vs-commentary convention, document-only date extraction,
   `failures-client.csv`, the digest stage, and the ingest hardening set:
   fetch retries, browser fallback, image-only PDF OCR with the
   `ocr_pages` evidence-gate degradation) were validated only by unit tests
   and smoke stubs. This is their first full live pass.
3. **Health check**: both engine CLIs authenticated and working before a
   4-way parallel burn.

You run and verify — you change NO code, prompts, or tests.

## Read first

1. `STATE.md` — Current State + all 2026-07-07 entries.
2. `client-runs/runs-07072026-37rows/preflight-3/preflight-report.md` — the
   LATEST link preflight over the 37 sources (post-hardening; ignore the
   older `preflight/` and `preflight-2/` dirs). Pick your slice from its OK
   list.
3. `src/run.py --help` — confirm flags, including `--out-root`.
4. `docs/run-records/37run-commands.md` — the production commands your slice is a dress
   rehearsal for.

## Constraints

- Branch `phase-3`. No code/prompt/test edits. No commits.
- Never touch `runs/`, `work/`, `ground-truth/`, `docs/client-updates/2026-07-06/`, or
  the `splits/` CSVs.
- LLM spend: the slice run itself + ONE digest call. Nothing else. If the
  run errors early, you may relaunch ONCE after diagnosing; report both
  attempts.

## Task 1 — pick the slice and build its CSV

From the preflight OK list pick 3 sources covering the batch's ingestion
modes: 1 plain-or-visual-heavy HTML page WITHOUT a CSV date (e.g. an Aberdeen
or State Street piece — exercises print-capture + document-date extraction),
1 direct `.pdf` link (exercises PDF download + first-page date scan), and
**the Manulife 2026 Global Macroeconomic Outlook** (its local PDF is
image-only — 19 OCR pages — so it is the only source that live-exercises the
new OCR path and the `ocr_pages` evidence-gate degradation; expect its rows
capped sub-High and review-flagged, and its Date to be `22/06/2026` from OCR
text). Copy those rows (with the original header, INCLUDING the `local_file`
column values) from
`client-runs/runs-07072026-37rows/Target Ingestion List (with local_file).csv`
into `client-runs/runs-07072026-37rows/cost-slice/slice.csv`.

## Task 2 — run the pipeline (production config, under nohup)

```bash
nohup .venv/bin/python -m src.run \
  --sources client-runs/runs-07072026-37rows/cost-slice/slice.csv \
  --run-id cost-slice-01 \
  --out-root client-runs/runs-07072026-37rows \
  --engine codex --effort high \
  --checker-engine claude --checker-model opus --checker-effort medium \
  --arbiter-engine claude --arbiter-model sonnet --arbiter-effort high \
  > client-runs/runs-07072026-37rows/cost-slice-01.log 2>&1 &
```

Record wall-clock start/end. Watch the log; if the process dies, capture the
tail before any relaunch.

## Task 3 — one digest call

```bash
.venv/bin/python -m src.summarize digest \
  --run client-runs/runs-07072026-37rows/cost-slice-01 \
  --sources <ONE source id from the run> \
  --out-dir client-runs/runs-07072026-37rows/cost-slice/digest
```

## Task 4 — cost accounting (be honest about what is measurable)

Count exactly, from the logs/manifest: analyze calls (= chunks per source),
checker calls (1 per source with candidates), arbiter calls, digest calls.
Grep the run log for any token/usage lines the engine CLIs print and report
them if present. State plainly that the authoritative cost is the provider
billing dashboards — tell Nikhil the exact time window of the run so he can
read the dashboards before/after, and give your best per-source extrapolation
to 37 sources (with the caveat that page counts vary).

## Task 5 — behavioral verification (report, do not fix)

In `client-runs/runs-07072026-37rows/cost-slice-01/`:

- `output.csv`: 16 expected columns; every `Date` is document-extracted
  DD/MM/YYYY or blank — NEVER the CSV's `Published At` value (compare
  against slice.csv to prove it); citations carry page/locator.
- `failures.csv` AND `failures-client.csv` both written; client file uses
  plain-language labels, no internal reason codes.
- `ingest_meta.json` per source: `date_from` ∈ {html, pdf_text, ""};
  Manulife's carries `ocr_pages` 1–19 and its output rows show the
  OCR degradation (sub-High cap + review flag + OCR-specific commentary).
- Manifest count check passes (kept + failed = candidates); views/bands/
  basis distributions look sane; note any review flags and why.
- The digest JSON: grounded in that source's kept rows (spot-check 2 claims
  against the document).
- Log anomalies: rate-limit errors, retries, empty snapshots, checker
  failures.

## Task 6 — report (no commits)

Report: the 3 sources chosen and why; wall time per source; the call counts
and any usage figures; the billing window for Nikhil; every verification
outcome from Task 5 with 2–3 verbatim output rows; a clear GO / NO-GO
recommendation for launching the splits, with reasons.
