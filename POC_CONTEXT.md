# Allocator Pro POC Context

Status date: 2026-06-26

## Purpose

This project is a clean POC for Markets Recon / Allocator Pro. It should not inherit the old `initial-test` codebase structure. The project should be designed around the new workbook, target sources, and expected output provided by Markets Recon.

## Business Problem

Markets Recon needs to process fund and asset manager outlook materials and convert them into structured allocation intelligence for Allocator Pro.

At a simple level, the system needs to:

1. Read a source document or HTML/scraped source.
2. Identify the topic.
3. Identify referenced asset classes.
4. Identify relevant sub-asset classes.
5. Determine the call for each relevant sub-asset:
   - `overweight`
   - `neutral`
   - `underweight`
   - `uncertain`
6. Attach evidence for each found call.
7. Produce a simple output that can be reviewed and compared against expected results.

## Primary Input: Excel Workbook

The Markets Recon workbook has arrived. It was extracted to three CSVs (one per
sheet) in `excel-file/` and is fully documented in `WORKBOOK_SCHEMA.md`. That
schema doc is the canonical reference; this section summarizes it.

The workbook is the primary source of truth and contains:

- The canonical taxonomy — `excel-file/Asset Class List - Locked.csv`
  ("Locked" = authoritative). 396 sub-asset classes, 31 categories, 4 top-level
  asset classes (Alternatives, Equities, Fixed Income, Currencies), plus 12
  Canva Groupings. Hierarchy: Asset Class → Asset Class Category → Sub-Asset
  Class. **There is no "Topic" column** (see Open Questions / reconciliation).
- The target source list — `excel-file/Target Ingestion List.csv`. 37 sources
  from 18 firms (~13 PDF, ~24 HTML). 37 > the 20-per-run cap, so ≥2 runs.
- The output shape — `excel-file/Target Output.csv`. A single worked example
  row defining the output schema and the `View` call code (O/N/U/UNCERTAIN).
  It is a **format example only**, not a benchmark to reproduce.

The taxonomy labels must be used exactly — no remapping, no invented labels.

## Source Inputs

The POC should support source material in the forms required by the workbook and Markets Recon.

Likely source types:

- PDF outlook documents.
- HTML files or scraped HTML output.
- Possibly cleaned text/Markdown derived from webpages.

The POC should not assume Markets Recon wants a full scraper. If website scraping is needed, assume Markets Recon may provide scraped output unless told otherwise.

## Required Output

The output schema is defined by `excel-file/Target Output.csv` (see
`WORKBOOK_SCHEMA.md`). Its columns are:

- `Firm`, `Date`, `Source` (title), `URL` — source identification.
- `Sub-Asset Class` — the taxonomy leaf the call is made against.
- `Asset Class Category`, `Asset Class`, `Canva Groupings` — **deterministic
  lookups** from the locked taxonomy once the sub-asset class is chosen.
- `View` — the call code: `O` overweight, `N` neutral, `U` underweight,
  `UNCERTAIN` uncertain.
- `Full Commentary` — evidence / citation text supporting the call.

Note there is **no Topic column** — the output is keyed on sub-asset class, not
topic. For internal review the POC may still add confidence/review status and a
page-or-source locator alongside these columns.

The output should be one file per run, referencing every source processed in that run.

Each run should process no more than 20 input items. The 37 target sources
therefore span at least 2 runs.

## Citation Requirement

Every found call must include a clear citation comment. The citation comment should briefly explain the evidence that supports the call.

For PDFs, include page number where applicable.

For HTML or non-paginated sources, include the best available source locator, such as heading, section name, element anchor, URL, or nearby text span.

If no clear evidence exists, the call should be `uncertain` or flagged for human review.

## Confidence And Review

The confidence score should not be a model's free-form self-assessment.

Confidence should be based on explicit, documented checks such as:

- Is the call language explicit or implied?
- Does the evidence directly support the call?
- Does the sub-asset class match the workbook taxonomy clearly?
- Is the rationale grounded in the source?
- Is there conflicting or stale language?
- Is the source extraction quality good enough?

Low-confidence or ambiguous items should be flagged for review.

## Analyst Brain

The system should be guided to think like a Markets Recon analyst.

Useful calibration examples should include:

- Source excerpt.
- Topic.
- Asset class.
- Sub-asset class.
- Analyst-approved call.
- Evidence span.
- Why the call was selected.
- Why alternative interpretations were rejected.
- Confidence or review outcome.

These examples should guide extraction and confidence calibration. They should not be treated as evidence for a new source.

## POC Scope

In scope:

- Local-first POC.
- Folder or workbook-driven source list.
- PDF and HTML/scraped-source ingestion as needed by the target sources.
- Topic, asset class, and sub-asset class extraction.
- OW/N/UW/UNCERTAIN call extraction.
- Citation comments and page/source references.
- Confidence/review flags.
- One run-level output file.
- Comparison against workbook expected output.

Out of scope for the first POC:

- Production Markets Recon front end.
- Full website integration.
- User management.
- Large-scale batch infrastructure.
- Production database/API design.
- Copying the old `initial-test` architecture without redesign.

## Current Client-Facing Commitments

From the client-facing spec:

- Timeline: three weeks end to end.
- One output file per run.
- No more than 20 input items per run.
- Local-first is acceptable for the POC.
- Front end is handled separately by Markets Recon.
- Payment terms are TBD.
- Contracting through Deel is acceptable.

## Key Open Questions

Resolved now that the workbook is in hand (see `WORKBOOK_SCHEMA.md`):

1. **Authoritative?** Yes — the taxonomy sheet is named "Locked" and is treated
   as the source of truth.
2. **Canonical taxonomy sheet?** `Asset Class List - Locked.csv`.
3. **Target sources sheet?** `Target Ingestion List.csv` (37 sources).
4. **Expected output sheet?** `Target Output.csv` — but it is a single
   format-example row, **not** a benchmark/ground-truth set.

Reconciliation: the locked taxonomy has **no Topic level**. Earlier docs over-index
on "topic"; treat it as optional internal context, not a required output field.

Still open:

- Confirm the `View` legend (O/N/U/UNCERTAIN) with the client.
- Is a fuller analyst-reviewed ground-truth set available for evaluation?
- Are expected calls human-reviewed or best-effort?
- Exact run-output file format: CSV, XLSX, HTML, JSON, or a combination?
- What confidence threshold should auto-pass?
- What source locator should be used for HTML inputs?
- Which AI model providers are allowed?
- Are all source materials public, or are any confidential?

## First Steps

1. ~~Place the workbook in `reference/`.~~ Done — extracted to `excel-file/`.
2. ~~Create `WORKBOOK_SCHEMA.md` after inspecting the workbook.~~ Done — see
   `WORKBOOK_SCHEMA.md`.
3. ~~Define the exact output schema from the workbook.~~ Done — documented in
   `WORKBOOK_SCHEMA.md` (Output shape).
4. Fetch/stage the 37 target sources (PDF + HTML) for processing, or document
   where they live.
5. Build only the minimum implementation needed to reproduce that output shape
   for the target sources (split across ≥2 runs of ≤20 items).

