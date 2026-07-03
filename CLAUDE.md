# Markets Recon / Allocator Pro POC

## Mission

Build a focused POC for Markets Recon / Allocator Pro that processes fund and asset manager outlook sources and produces reviewable asset-class allocation calls.

The POC should identify topics, asset classes, and sub-asset classes, then assign each relevant sub-asset an `overweight`, `neutral`, `underweight`, or `uncertain` call with clear supporting evidence.

## Read First

Before making implementation decisions, read:

1. `STATE.md` — current project state and recent changes; read this first for orientation
2. `WORKBOOK_SCHEMA.md` — the canonical reference for the workbook
3. `POC_CONTEXT.md`
4. The workbook CSVs in `excel-file/` (one CSV per sheet)
5. The client-facing spec `allocator-pro-poc-spec.html`

The workbook (in `excel-file/`) is the primary source of truth for:

- Asset classes, asset-class categories, and sub-asset classes
  (`Asset Class List - Locked.csv` — the locked taxonomy)
- Target sources (`Target Ingestion List.csv` — 37 sources)
- Output shape (`Target Output.csv` — one format-example row)

Note: the locked taxonomy has **no Topic column**. Output is keyed on sub-asset
class. `Target Output.csv` defines shape only — it is not a benchmark set.

If the workbook conflicts with older notes, prefer the workbook unless the user says otherwise.

Also read `POC_PLAN.md` — the agreed implementation plan and build order.

## Current Pilot Run

The first milestone is a **blind pilot on the 5 sources in `prev-excel/pilot.csv`** (not the
37-source `Target Ingestion List.csv`, which is the later full batch). Of the 5: 3 are local
PDFs in `prev-excel/` (`alliance-bernstein.pdf` = AllianceBernstein, `jp-morgan.pdf` = J.P.
Morgan, `PIMCO.pdf` = PIMCO); 2 are HTML fetched from their URLs (Aberdeen Investments,
Schroders). Map a pilot row to a local PDF by firm name when the file exists, else fetch the
URL.

Pilot rules:
- **Blind:** the building agent must NOT be given the saved ground-truth results while
  generating. The user reviews the frozen output afterward. `prompts/brain.md` few-shots must
  exclude these 5 sources.

See `POC_PLAN.md` → "Pilot set" for the full table.

## Critical Constraints

- Do not copy or assume the old `initial-test` code architecture.
- Do not begin by creating a production API, database schema, or front end.
- Do not invent taxonomy labels. Use the workbook labels exactly.
- Do not force a call when the evidence is weak. Use `uncertain`.
- Every found call must include a clear citation comment explaining the supporting evidence.
- Include page number where applicable. For HTML or non-paginated sources, include the best available source locator.
- Confidence must be computed from a deterministic rubric or explicit documented rules, not accepted from an LLM self-confidence score.
- Each run should process no more than 20 input items.
- Each run should produce one reviewable output file that references all files processed in that run.

## POC Workflow Target

```text
input folder or source list
  -> detect source type
  -> ingest/normalize source text
  -> identify topic, asset class, and sub-asset class
  -> extract OW/N/UW/UNCERTAIN calls
  -> attach citation comment and page/source reference
  -> score confidence and flag review items
  -> produce one run-level output file
```

## Output Requirements

The output should be simple enough for analyst review and comparison against the
workbook's output shape. The columns are fixed by `Target Output.csv` (see
`WORKBOOK_SCHEMA.md`):

- `Firm`, `Date`, `Source` (title), `URL` — source identification
- `Sub-Asset Class` — taxonomy leaf the call is made against (no Topic column)
- `Asset Class Category`, `Asset Class`, `Canva Groupings` — deterministic
  lookups from the locked taxonomy once the sub-asset class is chosen
- `View` — call code: `O` overweight, `N` neutral, `U` underweight,
  `UNCERTAIN` uncertain
- `Full Commentary` — evidence / citation text supporting the call

For internal review the POC may add, alongside the above: confidence or review
status, page number where applicable, and a source locator where a page number
is not applicable.

Matrix or one-hot output may also be needed:

```text
underweight, neutral, overweight
```

`View` maps one-hot as: `U` → (1,0,0), `N` → (0,1,0), `O` → (0,0,1). For
`UNCERTAIN`, all one-hot call columns should be zero.

## Analyst Review Philosophy

The goal is not just extraction volume. The goal is reviewable accuracy.

The system should make it easy for an analyst to see:

- What call was made
- Which source text caused that call
- Why the evidence supports it
- Whether the model was uncertain
- Which calls require human review

## Implementation Guidance

Start from the workbook and expected output. Design the smallest workflow that can reproduce the expected output for the target sources.

Prefer:

- Plain, inspectable files
- Deterministic parsing where possible
- Small, testable functions
- Explicit schemas once the workbook structure is known
- Local-first execution for the POC

Avoid:

- Large framework decisions before the workbook is understood
- Premature production hosting
- Hidden prompt-only logic for confidence
- Silent taxonomy remapping
- Treating neutral as a fallback for unclear evidence

## Open Questions To Track

Resolved (see `WORKBOOK_SCHEMA.md`):

- ~~Which sheet defines the canonical taxonomy?~~ `Asset Class List - Locked.csv`.
- ~~Which sheet defines the target sources?~~ `Target Ingestion List.csv`.
- ~~Which sheet defines the expected output?~~ `Target Output.csv` — shape only,
  one example row, not a benchmark set.
- ~~Is the workbook authoritative?~~ The taxonomy is "Locked" / authoritative;
  `Target Output.csv` is a format example, not ground truth.

Still open:

- Confirm the `View` legend (O/N/U/UNCERTAIN) with the client.
- Is a fuller analyst-reviewed ground-truth set available for evaluation?
- Are page numbers required for all PDFs, or only when extractable?
- What locator should be used for HTML sources?
- What confidence threshold should trigger human review?
- Which model providers are acceptable for the POC?

## Definition Of Done

The POC is done when:

- The workbook has been parsed and documented.
- The exact taxonomy and expected output are understood.
- Up to 20 target sources can be processed in one run.
- One run-level output file is produced.
- Output rows match the workbook-defined shape.
- Each found call includes a citation comment and page/source locator.
- Weak or ambiguous evidence is marked `uncertain` or flagged for review.
- The POC can be compared against the expected output in the workbook.

