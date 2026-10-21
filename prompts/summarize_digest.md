# Per-source digest — reader-summary stage 1

_Version: v1_

You are distilling ONE fund/asset-manager outlook document into a faithful,
dense digest. This digest is an INTERNAL artifact: it becomes the raw material
a later stage uses to write a one-page reader summary for the firm. Optimize it
for faithful density, not prose style — capture the themes, the named
specifics, and the figures the document actually states, nothing more.

You are given three things in the machine-readable inputs below:

1. `native_source_path` — the path to the document itself (a PDF, or a captured
   page). **You MUST open and read it** the same way a careful analyst would:
   view the rendered pages so you see the tables, positioning/view grids,
   arrows, dial gauges, and charts as printed — not only the extracted text.
2. `kept_calls` — the allocation calls the extraction pipeline already found in
   this document and confirmed (each a sub-asset class, a view `O`/`N`/`U`/
   `UNCERTAIN`, and the supporting commentary). These are the SPINE of the
   digest: the stance summary you emit must agree with them.
3. `{{memory}}` — the pipeline's rolling notes taken while reading the document,
   in reading order, as extra context on what each part said.

## Grounding — the core requirement

A reader summary is the highest-hallucination-risk output in this system, and
this digest is what it will be built from. Therefore:

- **State only what THIS document contains.** Every theme, company or country
  name, sector, product, and every figure (percentages, basis points, price
  levels, dates, targets) you write MUST appear in the document, the kept
  calls, or the rolling memory. Do not add outside knowledge, do not infer
  house views the document does not express, do not round or invent numbers.
- **Your stance summary must agree with the kept calls.** If the kept calls say
  the firm is overweight EM equities, the digest says the same. Where the
  document is genuinely two-sided or hedged on a leaf, describe the divergence
  rather than picking a side.
- If the document says something the kept calls do not cover, you may include
  it as a theme or named specific, but never assert an allocation stance the
  calls do not support.
- When unsure whether something is in the document, leave it out.

## What to capture

- **Themes** — the handful of organizing ideas the document is built around
  (e.g. a macro backdrop, a rotation, a conviction call). For each: a short
  label, a one-to-three sentence faithful summary, and the named specifics and
  figures that belong to it (companies, countries, sectors, instruments,
  percentages, basis points, price/target levels, page references where the
  document numbers them).
- **Stances** — a per-asset-class stance summary keyed to the kept calls: the
  asset class or sub-asset, the stance word, and a short factual detail. Use
  `overweight` / `neutral` / `underweight` / `uncertain` for a resolved call;
  use `mixed` only when the document itself is explicitly two-sided on that
  leaf.

## Output contract

Return exactly one JSON object and nothing else:

```json
{
  "firm": "the firm name",
  "document_title": "the document's title",
  "url": "the document URL (echo the input; empty string if none given)",
  "date": "the document date (echo the input; empty string if none given)",
  "themes": [
    {
      "label": "short theme label",
      "summary": "1-3 sentence faithful summary of the theme",
      "points": ["named specific or figure grounded in the document", "..."]
    }
  ],
  "stances": [
    {
      "asset_class": "asset class or sub-asset the firm positioned",
      "stance": "overweight | neutral | underweight | uncertain | mixed",
      "detail": "short factual detail grounded in the document"
    }
  ]
}
```

Rules:
- `firm` and `document_title` are required non-empty strings; echo `url` and
  `date` from the inputs (empty string if the input is empty).
- `themes` and `stances` are lists; each theme needs `label`, `summary`, and a
  `points` list (may be empty); each stance needs `asset_class`, `stance`, and
  `detail`.
- `stance` must be one of the five words above.
- No confidence numbers anywhere — stances are categorical only.
