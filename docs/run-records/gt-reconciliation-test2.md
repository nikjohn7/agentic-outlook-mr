# test2-01 GT reconciliation notes

For Markets Recon review. These notes do not change the ground-truth CSV; they
identify rows that appear to need analyst confirmation or source-scope cleanup.

## 1. T. Rowe Price UK IG Credit

**Request:** change the reference view from `U` to `N`.

The ground truth marks `UK IG Credit` as `U`, but both ingested T. Rowe Price
sources show Neutral:

- Monthly Asset Allocation Update, printed PDF p.2: the `UK IG corporates` row
  is in the gray/Neutral box.
- Global Asset Allocation: The View From the UK, PDF p.3: the `UK
  Investment-Grade (IG) Corporates` row is marked `N` / Neutral.

The model row is therefore correctly `N`; the GT `U` appears to be a reading
error.

## 2. BlackRock source scope / not-grounded rows

**Request:** confirm whether these rows should be dropped from the reference set
for this source, or whether additional BlackRock links should be added and read
together with `Equity Market Outlook Q2 2026`.

The ingested source is the equity-only BlackRock Equity Market Outlook PDF. The
judgment pass found six BlackRock GT-only rows classified as `not_grounded`
against that PDF:

| GT leaf | GT view | Why it needs reconciliation |
|---|---:|---|
| Brazil Equities | O | The ingested PDF is Asia-centric on EM equities; Brazil is not named. |
| Defence/Aerospace | O | The defence/NATO valuation discussion is absent from the ingested PDF. |
| LatAm Equities | O | No Latin America equity call appears in the ingested PDF; LatAm appears only in boilerplate/disclaimer context. |
| Quality | N | The source only mentions profitability/accounting signals obliquely; the Neutral factor call appears to come from outside this PDF. |
| UK Large Cap | O | No UK large-cap call appears in the ingested PDF; the commentary references separate MyMap/Pacific ex-Japan material. |
| UK Small Cap | O | No UK small-cap content appears in the ingested PDF; the UK-small-cap valuation thesis appears to come from another BlackRock source. |

More broadly, several BlackRock GT commentaries reference BII Global Outlook,
Weekly Commentary, MyMap multi-asset dials, bonds, credit, rates, or other
multi-asset material that is not present in the ingested equity-only PDF. Please
confirm the intended rule:

- If the source means exactly the linked Equity Market Outlook PDF, drop or
  rewrite rows that are not grounded in that PDF.
- If the source should mean BlackRock's broader outlook material generally,
  please provide the additional BlackRock links to ingest with this source.
