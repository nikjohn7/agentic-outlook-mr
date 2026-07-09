# What to share with Kyle — internal note

_Not client-facing. This is a recommendation for you, Nikhil, on which result files to attach._

## Share these

**1. `pilot-results.csv`** (in this folder) — a client-safe copy of the pilot run's `output.csv`.
- Kept: the 10 workbook columns (`Firm`, `Date`, `Source`, `URL`, `Sub-Asset Class`, `Asset Class Category`, `Canva Groupings`, `Asset Class`, `View`, `Full Commentary`) plus `confidence`, `band`, and `review_flag`.
- **Dropped:** `basis` and `checker_strength`. Both are internal diagnostics — `basis` records whether a call was stated vs inferred vs a forecast-delta, and `checker_strength` records the internal reviewer's evidence grade. They're useful to us for tuning but they expose internal mechanics and add noise for a non-technical reader, so they're not in the client copy.
- 106 rows, same as the frozen run.

**2. `pilot-ground-truth.csv`** (in this folder) — copied unchanged from the reference set you authored. This is the set of reference calls the pilot output was checked against; sharing it lets Kyle compare the two side by side. Referenced in the email as "the reference calls we checked the pilot against."

## Do NOT share (for this update)

- The run manifest from the pilot folder. I looked at it and it reads as internal: it names the specific engines and model tiers, run codenames, "scrambled pages", checker-strength counts, and failure reason codes. Too much internal machinery for a client update — skip it. (If you want to give Kyle a run summary later, it should be rewritten in plain language rather than shared as-is.)
- The internal evaluation and diagnostic artifacts — the judgment files, the comparison write-up, and the eval outputs. These are our own scoring and quality-diagnosis tooling, not results, and they reveal how we grade the system internally; they don't belong in a client-facing package.

## Optional — only if Kyle asks

- The pilot **failures file** (the calls the system attempted but rejected, each with a reason). The progress summary mentions this as a feature, so it's fine to describe. But don't attach it by default — offer it only if Kyle wants to see the rejected calls. If you do share it, make a client-safe copy the same way as the results CSV (drop `basis` and `checker_strength`).

## Second test — status: COMPLETE

The second test (seven sources across five firms: BlackRock, Franklin Templeton, Goldman Sachs AM, T. Rowe Price, and Wellington Management) is finished and evaluated. Three files are ready to share:

**3. `second-test-results.csv`** (in this folder) — a client-safe copy of the second-test output.
- Kept: the same 13 columns as `pilot-results.csv` (10 workbook columns + `confidence`, `band`, `review_flag`).
- **Dropped:** `basis`, `checker_strength`, and `call_language` — internal diagnostics, same reasoning as the pilot copy.
- **145 rows.** This is the current system's output: the frozen second-test calls plus the calls we recovered after teaching the independent reviewer to verify dial graphics from the page image. Two firms publish their views as dial graphics on web pages we capture as PDF; the first pass had rejected some of those, and the visual re-check restored them. Where a recovered dial call named an asset the run had already captured from that firm's paired document, we kept the original row rather than listing the asset twice, so there are no duplicate or conflicting rows in the file.

**4. `second-test-ground-truth.csv`** (in this folder) — the reference calls the second-test output was checked against, copied unchanged. Same rationale as the pilot ground truth: it lets Kyle compare the two side by side. (89 reference calls.)

**5. `second-test-report.html`** (in this folder) — a one-page, plain-language comparison of the second-test output against those reference calls. Safe to send as-is or printed to PDF; no internal jargon, no run codenames, no model names.

**6. `second-test-failures.csv`** (in this folder) — a client-safe copy of the second-test failures, prepared the same way as `pilot-failures.csv` (same six plain-language columns: Firm, Sub-Asset Class, Attempted Call, Why It Was Not Kept, Supporting Text, Page Reference). 108 rows.
- The 3 dial calls recovered by the visual re-check that were genuinely new are NOT in this file — they moved into `second-test-results.csv`.
- The other dial rows the first pass rejected are listed with their true final state: "verified from the page image, but the same call was already recorded from the firm's companion document."
- Same sharing policy as the pilot failures file: fine to attach, or hold back and offer if Kyle asks. Most of its rows (about 80 of 108) are duplicates from the paired-document reading — that's the pairing working, not errors; see `failures-and-misses-talking-points.md` for how to explain it.

### Still do NOT share for the second test

- The internal evaluation and judgment artifacts for the second test (the comparison write-up and eval outputs we used to derive the report's numbers) stay internal, same as the pilot.
