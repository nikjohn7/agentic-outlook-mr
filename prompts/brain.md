# Brain — analyst calibration for allocation-call extraction

Distilled from five analyst-reviewed outlook sources (a sector strategist, a
real-assets macro house, two multi-asset managers, a wealth manager). Style
guidance only: it never overrides the locked taxonomy, the source under
analysis, or the output contract.

## 1. Translate the house's dialect into O / N / U

Every firm publishes on its own scale. Map by **sign, not intensity** —
"slightly bullish" is still `O`. When the house's own rating and its hedging
prose diverge ("slightly bullish, but a broad overweight is not warranted"),
the rating wins.

**The published level wins over prose tone.** When a source prints its
positioning — a dial, score, grid, or tier label — that level *is* the call;
surrounding prose adds color but never overrides it. Change verbs describe
the journey, not the destination: "cash has been upgraded to neutral (0)" →
`N` (an upgrade that lands at neutral is neutral); "moved from neutral to
slightly bearish" → `U`; "caution has increased" beside a printed Neutral
dial → `N`.

- `O` — overweight; favor / prefer / like; bullish; Most/More Favored;
  attractive entry point; upgraded to positive.
- `N` — neutral; marketweight; balanced; a "0" score; the middle tier of any
  ranked scale.
- `U` — underweight; bearish; cautious; Less/Least Favored; avoid; reduce.

Ranked house scales collapse by tier: top tiers → `O`, middle → `N`, bottom →
`U` (a five-tier "Most Favored … Least Favored" spectrum maps O/O/N/U/U).
Record the tier name inside the evidence.

Portfolio **actions are calls**: bought / added / "added substantially" →
`O` (implied); exited / trimmed / "largely exited most of these" → `U`
(implied); deliberately maintained as an active positive case ("reinforce the
case for maintaining exposure", "maintain strategic exposure as a hedge") →
`O`. A bare "we hold X" with no case attached is not a call.

## 2. Implied calls: forecasts, rankings, posture

Outlook documents — especially macro house views — rarely say "overweight".
Extract the stance the source commits to:

- **Directional forecast revision on a priced asset is a call on its leaf.**
  "We now expect Brent to peak around $110 per barrel — far higher than our
  prior forecast of approximately $60" → `Oil` `O`. A sovereign **yield**
  forecast revised up is `U` on that bond leaf ("Australian 10-year to run 15
  bps higher, at 4.8%" → `Australia Govt Bonds` `U`).
- **Explicit cross-market rankings cut both ways** when leaves exist:
  "elevated risk premia for French and U.K. government bonds relative to
  German Bunds" → `German Bunds` `O`; "we prefer U.S. risky assets over
  Europe" → `US Equities` `O` **and** `Europe Equities` `U`.
- **A house's macro view implies calls on its own investable universe.** A
  real-assets manager ranking economies is calling its property markets:
  "Australia… tightest monetary policy globally… to dampen housing market
  speculation" → `RE - AsiaPac` `U` (no Australia leaf; snap up, `semantic`).
- **Recommended hedge/ballast allocations are calls**: "we prefer a mix of USD
  and real rates as a ballast" → `USD` `O`; gold held as "portfolio insurance…
  maintain strategic exposure" → `Gold/Precious` `O`.
- **Stated risk posture is a two-sided call**: "we enter Q2 with a high level
  of cash and a low level of risk" → `Cash/Money Markets` `O` and
  `Equities - General` `U`.
- **A two-sided rate path nets to `N`**: "the ECB to raise rates twice in 2026
  before reversing in 2027" → `Euro Govt Bonds` `N`, quoting both sides.
- **Mixed views inside one region** with no finer leaf → the region leaf at
  `N` with both sides in the evidence (Germany/Spain/Sweden strong vs
  France/Italy/U.K. weak → `RE - Europe` `N`). Where a sub-market has its own
  leaf and a clear view, also emit it (`RE - UK` `U`).

## 3. What is NOT a call

- **Reporting a market move** ("the index is down 7.3% since the war began",
  "the currency has weakened") — unless a forward expectation is attached
  ("…keeping it under pressure in the near term" → `U`).
- **Absence of mention.** Never infer `U` because a leaf isn't discussed or
  isn't "a focus area".
- **Explicit non-conviction is `N`, not `UNCERTAIN`**: "repricing has opened
  pockets of value across LatAm… though we express no strong directional
  conviction" → `LatAm Fixed Income` `N`; "valuations are compelling, but
  patience is warranted" → `N`. `UNCERTAIN` is reserved for a source that
  contradicts itself.
- **Backward performance attribution** counts only when the exposure is
  retained or reaffirmed ("materially underweight the Mag 7, a position
  carried from 2025… waiting for cheaper valuations" → `US Mega-Cap (Tech)`
  `U`).

## 4. Snapping defaults

- GICS sector language: Health Care → `Healthcare/Pharma`; Information
  Technology → `IT/Tech/Telecomms (inc. AI)`; Energy → `Energy Sector`; the
  rest match leaves near-verbatim (`Industrials`, `Utilities`, `Materials`,
  `Financials`, `Consumer Staples`, `Consumer Discretionary`,
  `Communication Services`, `Real Estate`).
- "Mag 7" / U.S. mega-cap tech → `US Mega-Cap (Tech)`.
- A generic instrument takes the **house's stated scope**: a global multi-asset
  team's unqualified "high yield" → `Global HY`; "U.S. high yield… 7.7%
  yield-to-worst" → `US HY`.
- A whole-asset-class "fixed income / duration" stance with no geography →
  `Duration`.
- Country with no country leaf → nearest regional leaf (`semantic`):
  Australia property → `RE - AsiaPac`. Countries named *inside* a broader
  stance stay at the stated level ("EM equities, e.g. Brazil and Korea" →
  `Emerging Markets Equities`, no fan-out).

Different firms taking opposite views on the same leaf (one house `O` USD,
another `N`) is normal — judge each source only on its own words.

## 5. The `reasoning` sentence (becomes analyst-facing commentary)

One dense analyst sentence in the house's voice: driver, direction, and the
key counterpoint when the source gives one. Good: "SCFR rates Health Care
Most Favored on demographics and steady defensive demand, with regulatory
pricing pressure the main overhang." Bad: "The source is positive on
healthcare."
