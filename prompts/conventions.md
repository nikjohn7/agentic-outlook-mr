# House conventions - normative extraction rules

_Version: v1.5_

These rules bind every reader of an outlook source: the extractor when making
a call, and any second reader when judging one. A call that follows these
conventions is correct *by convention* - never penalize it for the convention
itself. They never override the locked taxonomy or the source's own words.

## A call is a position, not an analytical input

A call is the house's own **position or recommendation** on an asset. Settle
this before translating anything into O / N / U: an analytical input stays an
input however precisely it can be read.

- **Visuals and tables that are calls:**
  - an explicit house preference or stance rating: overweight / neutral /
    underweight columns, Most-to-Least Favored tiers, conviction or view scores
    the source defines as its view, positioning dials;
  - the actual current allocation set against a weight the source itself calls
    neutral, benchmark or strategic: above it → `O`, at it → `N`, below it →
    `U`.
- **Not calls on their own:** trend-following, momentum, valuation, volatility,
  risk, sentiment, economic or nowcast indicators, scenario probabilities, and
  historical returns or performance. A colour, sign, arrow, zero or midpoint on
  such an indicator is a reading of the indicator, not O / N / U. **A zero or
  neutral reading is not neutral positioning.** A raw holding weight with no
  stated neutral/benchmark is not an overweight - holding an asset is not
  favoring it.
- **Read what the visual is before reading the cell.** Use the caption or
  title, the legend, the axis labels and the source's own explanation of the
  figure. When a figure (or a set of figures) builds a final allocation from
  several inputs, only the final allocation is a call; the inputs are its
  reasons, and inputs that disagree with each other are not a conflict in the
  house's stance.
- **A model's output is a call when the source adopts it** as its current
  allocation or recommendation ("the fund's tilts are set by this model",
  "these are our recommended weights"). Quantitative recommendations are calls
  like any other; an unadopted signal is not.
- **Prose can carry its own call.** When the source's text takes a stance -
  "our valuation work points to an opportunity in X, which we are building" -
  that is a call: quote the prose, with the basis it deserves, instead of the
  signal cell. A sentence that only reports what an indicator reads ("the
  trend signal on X turned positive") is still an indicator reading.
- **A house forecast is the house's view, not an input** - a published
  yield/FX/price forecast follows the forecast rules below (and their
  materiality gate); a third-party or model reading the house does not
  endorse does not.
- **Eligibility and basis are separate.** Owning a forecast does not make its
  allocation consequence `stated`. Prose predicting price support, weaker
  prices, currency moves or yield pressure without explicitly positioning or
  preferring the asset requires an allocation bridge: use `inferred`. This
  includes a forecast that an index stays flat and a two-sided yield path.
  Use `forecast_delta` only for a numerical endpoint versus an identified
  current level, with its magnitude and unit. Use `stated` for actual house
  positions/ratings/allocations or explicit preference language. Keep an
  eligible inference; do not suppress it to avoid tagging its basis.

## Translating the house's dialect into O / N / U

- Map by **sign, not intensity**: "slightly bullish" is still `O`.
- `O` - overweight; favor/prefer/like; bullish; constructive; Most/More
  Favored; attractive entry point; upgraded to positive. `U` - underweight;
  bearish; cautious; Less/Least Favored; avoid/reduce/trim/exited. `N` -
  neutral; marketweight; balanced; a "0" on the house's own rating scale; the
  middle tier of a ranked house-preference scale.
- **The published house level wins over prose tone.** A printed house rating -
  a dial, score, grid cell or tier label that shows the house's position, per
  the section above - *is* the call; surrounding prose adds color but never
  overrides it. This authority belongs to house stances only: an indicator
  chart that is clearer or more precise than the prose gains no authority over
  the prose. Change verbs describe the journey, not the destination: an
  upgrade that lands at neutral is `N`; "caution has increased" beside a
  printed Neutral dial is `N`.
- **A clear house-stance dial/table/chart call stands regardless of
  surrounding commentary tone - unless the commentary explicitly addresses the
  same leaf.** Commentary about a *narrower* sub-asset does not override a
  chart's call on the broader leaf: cautious prose about gold *mining* does not
  turn a positioning grid's `Gold/Precious` overweight into a `U`. Only
  commentary on the *same* leaf the visual charts can override or refine that
  visual call.
- **Closing, reducing, neutralizing, or trimming a position lands at its
  resulting stance, not the direction of travel.** "We are closing our
  overweight in X" → `N` (the end state is flat/neutral), **not** `U`.
  "Reducing", "neutralizing", "dialing back", "scaling back", "paring", or
  "moving back to neutral" all follow the same rule: the call is the stance the
  firm ends at, unless the text says the final position is still over- or
  underweight. Trimming but staying overweight → still `O`; reducing an
  underweight toward neutral → `N`. The direction-of-travel verb never
  overrides the stated end state.
- Ranked house scales collapse by tier: top tiers → `O`, middle → `N`,
  bottom → `U`. This applies to a ranking of the house's preferences, not to
  a ranking of indicator readings (cheapest to dearest, strongest to weakest
  trend).
- When the house's own rating and its hedging prose diverge, the rating wins.

## Implied calls (no "overweight" label required)

- **Portfolio actions are calls**: bought/added → `O`; exited/trimmed → `U`;
  deliberately maintained as an active positive case → `O`. A bare "we hold
  X" with no case attached is not a call.
- **A directional forecast revision on a priced asset is a call on its leaf**;
  a sovereign **yield** forecast revised up is `U` on that bond leaf.
- **Explicit cross-market rankings cut both ways** when both leaves exist
  ("prefer US over Europe" → US `O` and Europe `U`).
- **Recommended or adopted hedge/ballast allocations are calls** (`O`).
- **Stated risk posture is a two-sided call** ("high cash, low risk" → cash
  `O`, equities `U`).
- **Rotation and diversification stances cut both ways.** When the source
  favors one segment because it is cautious on another ("rotate away from X
  into Y", "diversify out of expensive X toward cheaper Y"), emit both sides
  where both leaves exist: the beneficiary gets its positive call and the source
  of the rotation gets its cautionary/negative call. The cautionary side is a
  legitimate call when the caution is explicit; use `basis: inferred` only when
  it requires one clear analyst step.
- **A two-sided path nets to `N`**: when the source commits to both directions
  across its horizon for the asset itself or the driver it prices (hikes then
  cuts; a currency weak near-term but appreciating medium-term), the net call
  is `N` with both sides quoted - the evidence for such an `N` legitimately
  shows both directions, not explicit "neutral" language. This needs a
  supported path or view on the relevant asset; a list of economic pros and
  cons is not a two-sided path.
- **Mixed views inside one region** with no finer leaf → the region leaf at
  `N` with both sides in the evidence; sub-markets with their own leaf and a
  clear view are also called at their level. The mixed views must be stances
  on the region's markets, not mixed macro conditions.
- **Broad and narrower calls are judged separately.** A broad leaf and a
  country or sub-market leaf inside it may legitimately carry different views;
  each stands on its own evidence.
- **An inference needs a clear bridge from the source's own argument.**
  Source-specific reasoning about an asset - an opportunity the house names
  and explains, a risk it says it is acting on - can support a single-step
  inference. A cheap valuation, a favorable signal or one supportive input on
  its own is not enough. An inference stays `inferred` even when a numerical
  chart sits beside it: the number does not make the position stated.

## What is NOT a call

- Reporting a market move with no forward expectation attached.
- An analytical input reading (trend, momentum, valuation, volatility, risk,
  economic or historical-performance indicator) the source does not adopt as
  its allocation or recommendation - see the first section.
- **Mixed macro conditions alone** ("growth is resilient, but inflation is
  sticky and policy uncertain") do not establish `N` equities or any other
  position; they are context until the source draws a stance from them.
- **Claims about a product's virtues or past performance** (a fund, a
  strategy, the house's own offering) are not a recommendation on the broad
  asset class. An actual recommendation to allocate - to the asset class, or
  to a product that is a clear instance of it - can be a call.
- Absence of mention - never infer `U` from silence.
- **Explicit non-conviction is `N`, not `UNCERTAIN`** ("no strong directional
  conviction" → `N`). `UNCERTAIN` is reserved for a source that contradicts
  itself or raises a stance it does not take.
- **A hedged risk note with no position taken is `UNCERTAIN`, not `U`.**
  Scenario/risk language the house flags without committing to a side - "there
  is a risk that X sells off if scenario Y plays out", stated as a caveat rather
  than a stance - is `UNCERTAIN`, never a `U`. A flagged downside risk is not a
  taken position; only map to `U` when the house actually adopts the cautious
  stance.
- Backward performance attribution counts only when the exposure is retained
  or reaffirmed.

## Snapping defaults

- GICS sector language maps to the matching sector leaf (Health Care →
  `Healthcare/Pharma`; Information Technology → `IT/Tech/Telecomms (inc. AI)`;
  Energy → `Energy Sector`).
- A generic instrument takes the **house's stated scope**: a global team's
  unqualified "high yield" → `Global HY`; "U.S. high yield" → `US HY`.
- A whole-asset-class "fixed income / duration" stance with no geography →
  `Duration`.
- A country with no country leaf snaps up to the nearest regional leaf
  (`semantic`); countries named *inside* a broader stance stay at the stated
  level (no fan-out).

Different firms taking opposite views on the same leaf is normal - judge each
source only on its own words.
