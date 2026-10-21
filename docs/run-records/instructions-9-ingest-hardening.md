# Instruction Set 9: ingest hardening — retries, browser fallback, image-only PDF OCR

## Context (you are in a fresh session — read carefully)

This repo extracts asset-allocation calls from fund managers' outlook
documents. A preflight sweep over the 37-source production batch exposed three
ingestion weaknesses that must be fixed BEFORE the batch runs:

1. **Slow servers time out.** `_fetch_html` uses `timeout=30`, `_download_pdf`
   `timeout=60`, single attempt. Eastspring-class sources failed on this.
2. **Bot-blocked sites (403/406/consent walls).** Several firms (Invesco,
   Manulife, others) refuse plain `requests` fetches. They were worked around
   by manual download + `local_file` wiring, which stays — but Kyle's final
   CSV may carry new links, and manual downloading does not scale to the
   ~70-row batch 2.
3. **Image-only PDFs produce a near-empty text layer.** The Manulife PDF
   (`client-runs/runs-07072026-37rows/manual-sources/manulife-2026-global-macro-outlook.pdf`,
   19 pages) extracts to ~36 characters. The analyze model reads the RENDERED
   pages (it opens the native PDF), so it can see the content — but the
   deterministic evidence gate verifies every `evidence_quote` against the
   snapshot text, so with an empty snapshot EVERY call from this source dies
   as `quote_not_found` / zero key-token overlap. The source would contribute
   zero output rows. The snapshot is also the date-extraction and
   read-quality corpus.

You fix all three. Everything here is deterministic code — **zero live LLM
calls** except the single batched content check inside any preflight re-run
(Task 4), which is already part of that tool.

## Read first

1. `STATE.md` — Current State + all 2026-07-07 entries.
2. `src/ingest.py` — the whole file, especially: `create_snapshot`,
   `_fetch_html` (~line 636), `_download_pdf` (~line 517), `print_url_to_pdf`
   (Playwright already a dependency — headless Chromium print-capture with
   consent-dialog handling), `_extract_pdf_text`, `detect_scrambled_page`
   (and the long comment above its constants — the scrambled-page mechanism
   is your template for OCR pages).
3. `src/confidence.py` — the read-quality floor (`MIN_PDF_CHARS_PER_PAGE`),
   `snapshot_read_quality`, and the scrambled-page degradation path
   (`SCRAMBLED_PROSE_CAP`, `EvidenceCheck.degraded`): verbatim prose falls
   back to key-token overlap, score capped below High, review-flagged,
   degradation recorded. OCR pages get the same treatment.
4. `src/assemble.py` — how `scrambled_pages` threads from ingest_meta through
   `run.py` into the evidence check (mirror this for `ocr_pages`).
5. `src/preflight.py` — the sweep tool you re-run in Task 4.
6. `client-runs/runs-07072026-37rows/preflight/preflight-report.md` and
   `preflight-2/preflight.csv` — which links failed, timed out, or captured
   blank; your live test fixtures.

## Constraints

- Branch `phase-3`. Plain factual commit messages (a repo hook blocks
  Claude/Anthropic self-attribution). No em dashes in any client-facing text.
- Never modify `runs/`, `work/`, `ground-truth/`, `docs/client-updates/2026-07-06/`, or
  the existing artifacts under `client-runs/` (your preflight re-run writes
  to a NEW out-dir, Task 4).
- The 20-source per-run cap and all pipeline semantics stay untouched.
- LLM budget: only the one batched content check the preflight tool already
  makes. The confidence rubric stays deterministic — no model anywhere in it.
- System dependencies for OCR (Task 3): check availability first
  (`tesseract --version`, and ghostscript if your chosen route needs it).
  If missing, install via Homebrew; if installation fails, STOP and report
  rather than shipping untested OCR code.
- Full suite before/after: `.venv/bin/python -m unittest discover -s tests`
  (`-s tests` required; record the baseline count; end green).

## Task 1 — retry + longer timeouts for plain fetches

Give `_fetch_html` and `_download_pdf` a shared retry helper: up to 3
attempts, exponential backoff (e.g. 2s/8s between attempts), per-attempt
timeout raised to 90s. Retry on timeout and connection errors and on
5xx; do NOT retry on 4xx (that is Task 2's job — a 403 will 403 again).
Constants at the top of `src/ingest.py` with a comment saying why. Unit
tests with a stubbed session: succeeds-on-second-try, exhausts-and-raises,
4xx-raises-immediately.

## Task 2 — Playwright fallback when plain HTML fetch is blocked

When `_fetch_html` finally fails with an HTTP status in {401, 403, 406, 429}
or exhausts retries on timeout, fall back to fetching the page with the
headless browser machinery that already exists for `print_url_to_pdf`:
navigate, run the same consent-dialog handling, take `page.content()` as the
HTML. Everything downstream (visual-heavy detection, print-capture decision,
text snapshot, date extraction) is unchanged — the fallback only replaces the
raw-HTML acquisition step.

- Record the acquisition path in `ingest_meta.json`: `fetched_via` ∈
  {`requests`, `browser`} (and existing fields unchanged).
- Factor the consent-dialog logic so print-capture and the fallback share it
  rather than duplicating it.
- Do NOT apply the browser fallback to PDF downloads in this set; blocked
  PDF links keep the manual `local_file` path. (If trivially cheap via the
  browser context, you may propose it in your report — do not build it.)
- Unit tests: stub the browser (existing test patterns show how the printer
  is stubbed); cover 403-triggers-fallback, fallback-result-flows-into-
  snapshot, and requests-success-never-touches-the-browser.

## Task 3 — image-only PDF pages: OCR the text layer

Detection: `_extract_pdf_text` already walks pages; compute per-page char
counts. A page whose extracted text falls below a per-page floor (new
constant, e.g. `OCR_MIN_CHARS_PER_PAGE`, aligned with the existing
`MIN_PDF_CHARS_PER_PAGE = 200` in confidence.py — state your choice) is an
OCR candidate. If a PDF has any such pages, OCR them and use the OCR text as
those pages' snapshot text.

Implementation route is your call — `ocrmypdf` on the file (then re-extract;
simplest, adds a real text layer) or per-page render + tesseract. Either way:

- `ingest_meta.json` gains `ocr_pages` (list of page numbers, like
  `scrambled_pages`).
- Thread `ocr_pages` through `run.py` → `assemble.py` exactly as
  `scrambled_pages` is threaded.
- Evidence gate: a call citing an OCR page gets the scrambled-page
  treatment — try verbatim first (OCR text may match), fall back to
  key-token overlap, cap the score at the same sub-High cap, review-flag the
  row, and record the degradation with an OCR-specific message (reuse the
  scrambled machinery; do not fork a parallel copy of it).
- Read-quality (`snapshot_read_quality`) evaluates the post-OCR snapshot —
  a fully-OCR'd document can pass the floor, but its rows still carry the
  OCR cap + review flag, which is the honest signal.
- Date extraction runs on the post-OCR snapshot (first-page scan may now
  find a date).
- OCR tools missing at runtime must degrade gracefully: keep the old
  (near-empty) snapshot, record `ocr_pages` as detected-but-not-ocred via a
  note in ingest_meta, never crash the sweep or run.

Unit tests: stub the OCR call; cover detection threshold, meta recording,
evidence-gate degradation on an OCR page, and the graceful no-tool path.

**Live validation (free, no LLM):** run `create_snapshot` (or the preflight
tool pointed at a 1-row CSV) against the real Manulife PDF at
`client-runs/runs-07072026-37rows/manual-sources/manulife-2026-global-macro-outlook.pdf`.
Report: page count, pre-OCR vs post-OCR snapshot char counts, `ocr_pages`,
and whether a date was extracted. Write this scratch output under
`tmp/` or a new dir in `client-runs/runs-07072026-37rows/` — never over the
existing preflight artifacts.

## Task 4 — re-run the preflight sweep to prove it live

Re-run the full 37-source preflight against the local_file-wired list:

```bash
.venv/bin/python -m src.preflight \
  --sources "client-runs/runs-07072026-37rows/Target Ingestion List (with local_file).csv" \
  --out-dir client-runs/runs-07072026-37rows/preflight-3
```

(Check first that preflight honors the `local_file` column — it uses
`create_snapshot`, so it should. If its loader does not accept that header
family, fix the loader gap, don't work around it.)

Compare against `preflight-2`: previously timed-out links now ok (Task 1),
any previously-403 HTML rows without local_file now ok via `fetched_via:
browser` (Task 2), Manulife now shows a real snapshot char count and its
content check verdict (Task 3). Report the before/after table.

## Task 5 — record, commit, report

- STATE.md: one Recent Changes entry covering the three hardening changes +
  what the preflight-3 sweep proved.
- Commit code + tests + STATE.md on `phase-3` (plain message). The
  `client-runs/` contents stay uncommitted.
- Final summary MUST include: per-task what changed and where; the
  Manulife before/after numbers; the preflight-2 → preflight-3 diff
  (fixed / still-failing / new-failures); any source still requiring manual
  handling; suite counts before/after; and any system dependency you
  installed (name + version).

## Acceptance checklist

- [ ] Retries + 90s timeouts on plain fetches; 4xx not retried; tested.
- [ ] Browser fallback on blocked HTML, `fetched_via` recorded, consent logic shared, tested.
- [ ] Image-only pages detected, OCR'd, `ocr_pages` in meta and threaded to the evidence gate with scrambled-style degradation + cap + review flag; graceful when OCR tools absent; tested.
- [ ] Manulife PDF live-validated: real text, evidence-gate corpus exists.
- [ ] preflight-3 sweep run; before/after reported; no edits to existing preflight artifacts.
- [ ] Suite green before/after; STATE.md updated; plain commit(s); zero LLM calls beyond preflight's one content check.
