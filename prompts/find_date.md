# Find the publication date of one document

You are given ONE published investment document (a fund or asset-manager
outlook). Your only job is to find the date it was **published**, and to report
where you saw it, verbatim. A downstream deterministic checker re-verifies every
date you report against the document and the web page, so you must never invent,
infer, or reformat a date — report only what is literally written, exactly as it
is written.

## What you are given (in the machine-readable inputs below)

- `firm` — the publisher.
- `title` — the document's title.
- `url` — where the document lives (an article page, or a direct link to a PDF).
- `document_text` — text already extracted from the document for you (the head
  and, when long, the tail). It may be empty or thin if the document is
  image-only or a scanned cover.
- `local_file_path` — when present, an absolute path to the document file on
  disk. You MAY open and read it directly (including reading the cover page
  visually) to find a date the extracted text missed — for example a date
  printed only inside a cover image.

## What to do, in order

1. **Look inside the document first.** Search the provided `document_text`, and —
   when `local_file_path` is present — the file itself, for an explicitly
   **stated publication date**. Look on the cover, masthead, header/footer,
   "as of" line, and the back page. Read a date printed inside a cover image
   visually if that is the only place it appears. Report every distinct stated
   date you find as a `stated_in_document` candidate.

2. **Only if the document states no date, hunt the landing page.** Find the page
   on the publisher's own site that presents THIS exact document, and read its
   published/posted date. You may use web search. If `url` is a direct link to a
   PDF, try the parent path of that URL first (the article or insight page that
   the PDF hangs off). Report the date you read there as a `landing_page`
   candidate, and give the full URL of the page you read it on as the `locator`.

## Hard rules

- **Never read file metadata.** Do not report a PDF "creation date", document
  properties, or any embedded timestamp. Those are handled by separate
  deterministic code, not by you. Report only a date that is written in the
  human-readable content.
- **Never invent or infer.** If the document says only "Midyear 2026" or
  "Q3 2026" or "Summer 2026", report it verbatim with the matching granularity —
  do not turn it into a day or a month. If you cannot find any stated date and no
  landing page gives one, return an empty candidate list.
- **Beware trap dates.** A "data as of", "source as of", "performance as of", or
  chart cut-off date is NOT the publication date. Do not report it as one.
- **Verbatim only.** `date_verbatim` and `evidence_quote` must be copied exactly
  as they appear (same words, same order, same spelling). Do not normalize,
  translate, or reformat.

## Output

Return **JSON only** — no prose, no explanation — in exactly this shape:

```json
{
  "candidates": [
    {
      "where": "stated_in_document",
      "date_verbatim": "17 June 2026",
      "locator": "1",
      "evidence_quote": "Global Fixed Income Outlook — 17 June 2026",
      "granularity": "full"
    }
  ]
}
```

Field rules, per candidate:

- `where` — `"stated_in_document"` if you read the date in the document itself,
  `"landing_page"` if you read it on the publisher's web page.
- `date_verbatim` — the date string exactly as written.
- `locator` — for `stated_in_document`, the page number the date is on (a bare
  number, e.g. `"1"`); for `landing_page`, the full URL of the page you read.
- `evidence_quote` — the verbatim line or phrase that contains the date, long
  enough to be found again (include a few surrounding words).
- `granularity` — `"full"` (day + month + year), `"month_year"` (month + year,
  no day), or `"quarter_or_season"` (a quarter, half, or season such as
  "Q3 2026", "Midyear 2026", "H2 2026", "Summer 2026").

If there is no stated date and no landing-page date, return:

```json
{"candidates": []}
```
