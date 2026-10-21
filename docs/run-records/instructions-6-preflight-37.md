# Instruction Set 6: client-runs scaffolding + 37-source link preflight

## Context (you are in a fresh session — read carefully)

This repo is a POC that reads fund managers' outlook documents (PDF/HTML) and
extracts asset-allocation calls into run-level CSVs. The ~37-source production
batch runs soon, split into 4 parallel runs. Before that, two things:

1. Production run artifacts must live under a new **`client-runs/`** tree,
   NOT the existing `runs/`+`work/` (those hold test/pilot history). Layout
   for this batch: `client-runs/runs-07072026-37rows/` containing everything —
   downloaded PDFs/snapshots, the split runs' outputs, and preflight results.
2. A **link preflight over all 37 sources**: fetch every source (read HTML,
   or download the PDF locally), flag every link that doesn't work or doesn't
   look like the titled document.

## Read first

1. `STATE.md` — Current State + 2026-07-07 entries (document-date work and
   the instruction-set-5 date policy both touch ingest).
2. `src/ingest.py` — `load_pilot_sources`, `create_snapshot` (does the whole
   fetch: HTML fetch, visual-heavy print-to-PDF, remote PDF download with a
   `%PDF` magic guard, snapshot text, document-date extraction into
   `ingest_meta.json`), `enforce_source_limit` (20 cap — per RUN, see Task 2).
3. `src/run.py` — where `work/<run-id>` and `runs/<run-id>` are hardcoded
   (~lines 92, 244, and in `main`), and the `--ingest-only` flag.
4. `excel-file/Target Ingestion List.csv` — the 37 sources (header family:
   `Id`,`Firm`,`Title`,`Published At`,`Source Link` → loaded by
   `load_target_sources`; check which loader fits and use it).
5. `src/llm.py` — `call_parsed` conventions (codex pinned `gpt-5.5`).

## Constraints

- Branch `phase-3`. Plain factual commit message (a repo hook blocks
  Claude/Anthropic self-attribution).
- Never modify `runs/`, `ground-truth/`, `docs/client-updates/2026-07-06/`.
- LLM budget: **exactly ONE live call** (Task 3, codex `gpt-5.5` effort
  medium). The fetching itself is deterministic code — no model involved.
- Live network use: the 37 fetches themselves (that is the job) — nothing
  else.
- Full suite before/after: `.venv/bin/python -m unittest discover -s tests`
  (the `-s tests` is required; record baseline; end green).

## Task 1 — `--out-root` for run.py + client-runs housekeeping

Add an optional `--out-root <dir>` argument to `src/run.py` (threaded through
`run_pipeline`): when given, the run writes to `<out-root>/<run-id>/`
(output.csv etc.) and `<out-root>/work/<run-id>/` (snapshots, memory, native
PDFs) instead of `runs/<run-id>` and `work/<run-id>`. Default (absent) keeps
today's paths exactly. Unit-test both (stub runner, tiny fixture source).

Add `client-runs/` to `.gitignore` (same convention as `runs/`: ignored,
frozen artifacts force-added when the time comes).

## Task 2 — preflight fetch of all 37 sources (`src/preflight.py`)

A small standalone command:

```
.venv/bin/python -m src.preflight --sources <csv> --out-dir <dir>
```

For THIS run: `--sources "excel-file/Target Ingestion List.csv"
--out-dir client-runs/runs-07072026-37rows/preflight`.

It loops over every row (it is NOT a pipeline run, so the 20-source run cap
does not apply — do not call `enforce_source_limit`; do not weaken the cap
for real runs) and calls the existing `create_snapshot` per source into
`<out-dir>/work/`, each wrapped so one failure never stops the sweep. Reuse
ingest wholesale — no new fetch logic. Record per source:

- ok / FAILED (with the exception's message: HTTP status, non-PDF body,
  timeout, etc.)
- source_type, page_count or snapshot char count, visual_heavy/printed_pdf
- the document-extracted date from `ingest_meta.json` (`date`, `date_from`)
  — this doubles as the first at-scale validation of the new date policy

Outputs in `--out-dir`: `preflight.csv` (one row per source with the fields
above) and `preflight-report.md` (summary counts, the FAILED list first,
then a date-extraction table: how many got dates, from where, how many
blank). Downloaded PDFs and print-captured pages stay under
`<out-dir>/work/` — they are the "PDF downloaded locally" deliverable.

Unit tests: stub the network (existing test patterns show how printer/
downloader/_fetch_html are stubbed); cover one ok row, one failing row
(sweep continues), and the CSV/report shape.

## Task 3 — one content-sanity LLM pass (the only live call)

After the sweep, ONE batched codex `gpt-5.5` effort-medium call
(new prompt `prompts/preflight_content_check.md` + REGISTRY entry). For every
successfully fetched source it gets: firm, expected title, and the first
~400 characters of the snapshot text. It returns, per source, a categorical
verdict only:

- `looks_right` — the text plausibly belongs to the titled document
- `suspect` — looks like a consent wall / cookie page / teaser / listing
  page / wrong document (one short reason)

No numbers, no scores (house rule). A failed call degrades to every source
marked `unchecked` with a note — never a crash. Verdicts go into
`preflight.csv` (a `content_check` column) and the report (suspects listed
right under the FAILED links).

## Task 4 — record, commit, report

- STATE.md: Recent Changes entry (out-root flag, client-runs convention,
  preflight tool + what the 37-sweep found).
- Commit code + prompt + tests + REGISTRY + STATE.md + `.gitignore` on
  `phase-3`. The `client-runs/` contents stay uncommitted.
- Your final summary MUST include: the FAILED links (firm + title + error),
  the `suspect` list, the date-extraction counts, and where the artifacts
  are. Note: this ran against the workbook copy of the list; the client's
  final CSV may carry different links, so the sweep gets re-run (cheaply)
  when that file arrives.

## Acceptance checklist

- [ ] `--out-root` works and defaults to today's paths; tested both ways.
- [ ] `client-runs/` gitignored.
- [ ] Preflight sweeps all 37 rows; one failure never stops the sweep; no run-cap applied to the sweep and no cap change for real runs.
- [ ] PDFs/snapshots under `client-runs/runs-07072026-37rows/preflight/work/`; `preflight.csv` + `preflight-report.md` written.
- [ ] Exactly one live LLM call (batched content check, categorical, graceful degrade).
- [ ] Date-extraction results for all 37 reported (validates the document-only date policy at scale).
- [ ] Suite green (before/after counts); REGISTRY + STATE.md updated; one plain commit; no edits to `runs/`, `ground-truth/`, `docs/client-updates/2026-07-06/`.
