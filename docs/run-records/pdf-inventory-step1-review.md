# PDF foundations calibration review

Date: 2026-10-24. Branch: `markdown-batch-oct2026`.

Implemented the Step 1 pairing fields, deterministic inventory CLI and unused
`PDF_READ_MARKER` constant. No extraction pipeline, model call or commit was made.
The test suite's model-call-looking output comes from its mocked fixtures.

## Inventory and thresholds

A deterministic shortlist for visual reading, not proof of missing content.
Page numbers are physical PDF pages (1-indexed).

| Firm | Document | Flagged pages | Flagged / total |
| --- | --- | --- | ---: |
| NYL Investment Management | H2 2026 Private Markets Outlook: Resilience in an Evolving Landscape | 5, 6, 13, 14, 17 | 5 / 24 |
| Amova | Global Investment Strategy Committee Outlook Q4 2026 | 2, 3, 4, 6, 7, 8, 10, 11, 13, 14, 15 | 11 / 17 |
| Neuberger Berman | Equity Outlook 4Q 2026: Rising Rates Shift the Balance of Risks | 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 18, 19, 20, 21 | 18 / 23 |
| Neuberger Berman | Asset Allocation Outlook 4Q 2026: Repricing Risk, Recalibrating Exposure | 4, 5, 6, 7, 9 | 5 / 12 |
| Allianz | House View Q4 2026: Renewed momentum | 2 | 1 / 7 |
| Lombard Odier | Simply put Q4 2026: Multi asset - adapting to an expensive expansion | 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14 | 12 / 16 |
| Columbia Threadneedle Investments | Multi-Asset Investment Outlook 2027: Technological tailwind, geopolitical headwind | 6, 8, 11, 12, 15, 16, 18, 20, 21, 23, 25, 26, 28, 30 | 14 / 32 |
| L&G | Q4 2026 ETF Outlook: getting more from core | 3, 6 | 2 / 8 |
| State Street Investment Management | Sector Market Perspectives: Q4 2026 | 3, 6 | 2 / 14 |
| Robeco | Fundamental Equity Quarterly Q4 2026: Parallel worlds | 5, 7 | 2 / 16 |
| Tikehau Capital | CIO Outlook 2027: Start, Middle or End of the Cycle? | none | 0 / 23 |
| BlackRock | Equity Market Outlook Q4 2026 | 4, 5, 10 | 3 / 13 |
| Franklin Templeton | Hedge Fund Strategy Outlook Q4 2026 | 6, 7, 8, 9, 10, 11, 12 | 7 / 15 |
| MetLife IM | Relative Value & Tactical Asset Allocation Q4 2026 | 2, 6, 9, 12, 15 | 5 / 17 |
| State Street Investment Management | Emerging markets: Inflation, central banks, and near-term debt outlook | 2, 3, 4 | 3 / 5 |

Total: 90 flagged pages / 242 pages in 15 documents.

Thresholds (calibrated on the 15 October 2026 pairs):
- Unfilled body curves or body rectangles >= 60, or repeated small round marks of one rounded square size >= 7.
- Filled body curves >= 100 (dense visual symbols, with a higher floor to skip logos).
- Body line segments >= 60 (NYL forecast paths use lines, not curves).
- Captioned charts with >= 10 body rectangles (bars/cells).
- Unfilled body curve bounding-box area >= 8%, or captioned raster area >= 5%.
- Normalized PDF word coverage in rendered Markdown < 90%.
- Body: 8% to 92% of page height; objects > 40% of page area excluded as backgrounds.
- Round marks: 1 to 12 pt per side, aspect 0.9 to 1.1.
- Opening boilerplate headings excluded; sparse pages (< 40 words) excluded only when graphics are below threshold.
- Graphics and low coverage are independent reasons; high coverage never clears graphics.

Why these thresholds: the object floors retain dense allocation grids and
heatmaps. Seven same-size round marks retain the smaller Franklin dials and
Neuberger sector grid. Filled curves need a higher floor because logos use
filled letter shapes. Sixty line segments catch NYL rate forecasts that use
PDF lines rather than curves. Captioned ten-rectangle charts catch L&G's
sector bars even when all axis labels survive in Markdown. The 8% unfilled
curve area and 5% captioned image area catch substantial plots while avoiding
large filled text panels, portraits and photographs. Raster captions must
start a text line; references to a figure on the following page beside a
portrait do not count as raster evidence. Small marks are 1-12 PDF points,
aspect 0.9-1.1, with equal sides after rounding to whole points.

Coverage is the fraction of normalized PDF word occurrences whose word occurs
anywhere in the whole rendered Markdown. Both texts use `normalize_quote_text`,
case folding and word tokenization. Order, numeric associations and visual
positions are not checked by this measure. A 90% floor catches substantial
loss without selecting pages for a few footnote or hyphenation differences.
Textless raster pages are candidates. The 40-word floor only suppresses weak
coverage signals on sparse pages; it cannot suppress a graphics-heavy page.
Opening disclaimer/contact headings are excluded, using the first three text
lines, so legal footnotes beneath a real chart do not clear that chart.

## Visual review evidence

All 242 pages were rendered with pdfplumber at 100 dpi and viewed in contact
sheets, including all selected pages and covers, prose, blanks and disclaimers.
These are overview inspections, not detailed transcription of every plot.
The rendered pages and contact sheets are in
[`tmp/pdfpass/step1-scratch/`](../../tmp/pdfpass/step1-scratch/):
`dNN-pPP.png` identifies document number and physical PDF page;
`contact-dNN-K.jpg` groups consecutive sets of up to nine pages.
Document numbers below follow the 15-row source CSV.

Individual page images were also opened for:
1: 5, 17; 2: 12; 3: 16, 18, 20, 21; 4: 4, 8; 5: 3;
6: 12; 7: 15; 8: 3, 5, 6; 9: 4; 10: 5, 6; 11: 16;
12: 7; 13: 3, 12; 14: 2, 16; 15: 1.
The table below records final flags, including pages added after initial
unflagged inspection. Where no unflagged data chart/table exists, that is
stated explicitly instead of inventing a sample.

| # | Document | Selected pages seen in overview: what they showed | Final unflagged samples viewed | What those samples showed |
| ---: | --- | --- | --- | --- |
| 1 | NYL Investment Management: H2 2026 Private Markets Outlook: Resilience in an Evolving Landscape | Rate and economic paths (5-6), credit-demand paths (13), spread bars (14), property-price plot and text table (17). | 7, 10, 20 | Data-centre financing bars, exits/secondary-volume bars, infrastructure/power bars. Numeric labels survive; no allocation dots. The line-only forecast on p.5 was an initial miss and is now selected. |
| 2 | Amova: Global Investment Strategy Committee Outlook Q4 2026 | Market/rate/FX/equity plots (2-4, 6-8, 10-11); forecast tables (13-15). | 12 | Bordered prose panel, not a chart. All chart/table pages in this document are selected; there is no unflagged chart/table sample. |
| 3 | Neuberger Berman: Equity Outlook 4Q 2026: Rising Rates Shift the Balance of Risks | Economic/earnings/rate plots (3-14, 16-19); regional recommendation dots/arrows (20), sector recommendation dots (21). | 15 | Grouped portfolio risk/earnings text blocks. All plotted chart and recommendation-grid pages are selected; no unflagged chart/table remains. |
| 4 | Neuberger Berman: Asset Allocation Outlook 4Q 2026: Repricing Risk, Recalibrating Exposure | Allocation-dot grids and change arrows (4-7); breakeven-inflation chart (9). | 8 | Top-ten EM index-weight bars with explicit numeric labels and a drawdown table. This is data/context, with the EM stance stated in prose. No lost positioning dots in this sample. |
| 5 | Allianz: House View Q4 2026: Renewed momentum | Confidence line chart on page 2. | 3 | Asset icons and equity-conviction bullets. The only chart page is selected, so no unflagged chart/table exists. |
| 6 | Lombard Odier: Simply put Q4 2026: Multi asset - adapting to an expensive expansion | Growth/volatility figures (2-3), portfolio bars and heatmaps (5-8), macro charts (9-10), liquid-alternative and ARP comparisons (11-14). | 4 | Prose continuation and a boxed conclusion. All chart pages are selected; no unflagged chart/table remains. Page 12 was also inspected individually: correlation bars plus the return/drawdown scatter. |
| 7 | Columbia Threadneedle Investments: Multi-Asset Investment Outlook 2027: Technological tailwind, geopolitical headwind | Economic/rate plots (6, 8, 11-12), expected-return table/scatter (15-16), equity/credit/real-asset figures (18, 20-21, 23, 25-26, 28, 30). | 10, 14, 17, 19, 29, 31 | GDP table; annual return bars; industry/sector charts; volatility and return components; commodity bars; stock/bond correlation lines. The table and many bars retain explicit numeric labels. Smaller historical plots remain below the thresholds; these are not verified loss-free. |
| 8 | L&G: Q4 2026 ETF Outlook: getting more from core | Illustrative factor/sector/region tilt bars (3); sector premium/discount bars (6). | 5 | Market-breadth history above explanatory prose, with no positioning symbols. Page 3 was also inspected individually: Markdown preserves every named tilt and its signed value. Page 6 was an initial miss: axes and names survive but bar heights do not; it is now selected. |
| 9 | State Street Investment Management: Sector Market Perspectives: Q4 2026 | Main sector-view table (3), sector earnings/yields raster chart (6). | 4 | Table continuation explicitly says Utilities Neutral and Health Care Positive, with rationale text. Both cells and their stances survive in Markdown; the table does not rely solely on coloured markers. |
| 10 | Robeco: Fundamental Equity Quarterly Q4 2026: Parallel worlds | Momentum/election-cycle plots (5), EM earnings-growth bars (7). | 6, 11, 13 | Korea versus EM ROE history, Argentina sector-GDP bars, bond-issuance bars. Historical data rather than allocation dials; smaller plots remain below threshold. Page 6 was inspected individually. |
| 11 | Tikehau Capital: CIO Outlook 2027: Start, Middle or End of the Cycle? | None. The document uses text, large decorative fills and small thematic icons. | 16 | Two-column credit/private-debt prose with a small decorative chart icon. No data chart/table/grid was found in the overview of this document, so there is no eligible unflagged chart sample. |
| 12 | BlackRock: Equity Market Outlook Q4 2026 | Valuation/income plot (4), income versus price-return plot (5), regional earnings bars (10). | 7, 9 | Five-year EPS growth plot and copper price/capital-return area chart. Narrative and labelled headline numbers remain; there are no allocation dots. These smaller historical/context charts remain below the thresholds. |
| 13 | Franklin Templeton: Hedge Fund Strategy Outlook Q4 2026 | Historical charts/map (6-8), strategy pendulum dials (9-11), conviction-symbol and z-score tables (12). | 3 | Strategy-description table with decorative icons and explicit worded stances. Every actual data-chart and dial page is selected; this is the remaining text-table sample. |
| 14 | MetLife IM: Relative Value & Tactical Asset Allocation Q4 2026 | Tactical-allocation radar (2), credit spread markers (6), leverage/cash bars (9), refinance path (12), surprise-index lines (15). | 16 | Contributor-name grid and explicit cash-underweight prose. All plotted chart pages are selected; no unflagged data chart remains. |
| 15 | State Street Investment Management: Emerging markets: Inflation, central banks, and near-term debt outlook | Scenario table and debt-return bars (2), inflation/rate paths (3), yield curves (4). | 1 | Portrait, bullets and two-column prose. All data chart/table pages are selected, so no eligible unflagged data-chart sample remains. |

The three mandatory examples are selected despite almost complete text
coverage: Neuberger AAO p.4 (99.77%, dots/arrows), MetLife p.2 (100%, radar
positions), Franklin p.12 (100%, conviction pendulums). Neuberger equity
p.20-21 also retain their recommendation dots in this shortlist.

A selected page is not proof of missing information, and an unselected page
is not proof of a complete conversion. Some historical/context plots remain
unselected and may lose their curves or bar heights. No further visual-only
allocation grid was identified in the overview or the individual samples.
This calibration is scoped to these 15 documents, not a general graphics
recognizer or a benchmark acceptance decision. All selected pages should be
reviewed by the later PDF reader before the final batch is generated.

## Validation and reproduction

- `.venv/bin/python -m unittest discover -s tests`: 506 tests, OK, 1 skipped (at the Step 1 commit; later
  steps raised the count to 517).
- Exactly two new basic tests: optional PDF pairing and a synthetic graphics
  page retained with 100% text coverage, with a prose-page control.
- All 30 source files matched the SHA-256 hashes in `intake-manifest.json`.
- All first-page covers and the reviewed pure-prose, blank and disclaimer
  pages remain unselected. The three required visual pages are selected.
- All 242 page rows are in the CSV; no input file was rewritten.

```sh
.venv/bin/python -m src.pdfinventory \
  --sources client-runs/runs-23102026-16rows/md-sources-15.csv \
  --out client-runs/runs-23102026-16rows/pdf-inventory
```

Artifacts: [`pdf-inventory.csv`](../../client-runs/runs-23102026-16rows/pdf-inventory/pdf-inventory.csv)
and [`pdf-inventory-summary.md`](../../client-runs/runs-23102026-16rows/pdf-inventory/pdf-inventory-summary.md).
The generated artifacts and render scratchpad are gitignored; this review
records the shortlist and inspection evidence for later steps.
