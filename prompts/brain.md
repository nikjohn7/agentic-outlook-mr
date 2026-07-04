# Brain — worked examples for allocation-call extraction

Distilled from five analyst-reviewed outlook sources (a sector strategist, a
real-assets macro house, two multi-asset managers, a wealth manager). The
normative rules these examples illustrate live in `conventions.md` and are
injected separately; these examples teach the style of applying them. They
never override the locked taxonomy, the source under analysis, or the output
contract.

## Dialect translation

- "Slightly bullish, but a broad overweight is not warranted" → the rating
  wins → `O`.
- "Cash has been upgraded to neutral (0)" → `N` (an upgrade that lands at
  neutral is neutral); "moved from neutral to slightly bearish" → `U`;
  "caution has increased" beside a printed Neutral dial → `N`.
- A five-tier "Most Favored … Least Favored" spectrum maps `O`/`O`/`N`/`U`/`U`.
  Record the tier name inside the evidence.
- Bought / "added substantially" → `O` (implied); "largely exited most of
  these" → `U` (implied); "reinforce the case for maintaining exposure" /
  "maintain strategic exposure as a hedge" → `O`.

## Implied calls

- "We now expect Brent to peak around $110 per barrel — far higher than our
  prior forecast of approximately $60" → `Oil` `O`.
- "Australian 10-year to run 15 bps higher, at 4.8%" → `Australia Govt Bonds`
  `U` (yield up = price call down).
- "Elevated risk premia for French and U.K. government bonds relative to
  German Bunds" → `German Bunds` `O`; "we prefer U.S. risky assets over
  Europe" → `US Equities` `O` **and** `Europe Equities` `U`.
- A real-assets manager ranking economies is calling its property markets:
  "Australia… tightest monetary policy globally… to dampen housing market
  speculation" → `RE - AsiaPac` `U` (no Australia leaf; snap up, `semantic`).
- "We prefer a mix of USD and real rates as a ballast" → `USD` `O`; gold held
  as "portfolio insurance… maintain strategic exposure" → `Gold/Precious` `O`.
- "We enter Q2 with a high level of cash and a low level of risk" →
  `Cash/Money Markets` `O` and `Equities - General` `U`.
- "The ECB to raise rates twice in 2026 before reversing in 2027" →
  `Euro Govt Bonds` `N`, quoting both sides.
- Germany/Spain/Sweden strong vs France/Italy/U.K. weak → `RE - Europe` `N`
  (both sides in the evidence), plus `RE - UK` `U` where the sub-market has
  its own leaf and a clear view.

## Not-a-call boundaries

- "The index is down 7.3% since the war began" → not a call — unless a
  forward expectation is attached ("…keeping it under pressure in the near
  term" → `U`).
- "Repricing has opened pockets of value across LatAm… though we express no
  strong directional conviction" → `LatAm Fixed Income` `N`; "valuations are
  compelling, but patience is warranted" → `N`.
- "Materially underweight the Mag 7, a position carried from 2025… waiting
  for cheaper valuations" → `US Mega-Cap (Tech)` `U` (backward attribution
  counts because the exposure is retained).

## Snapping

- "Mag 7" / U.S. mega-cap tech → `US Mega-Cap (Tech)`.
- A global multi-asset team's unqualified "high yield" → `Global HY`; "U.S.
  high yield… 7.7% yield-to-worst" → `US HY`.
- "EM equities, e.g. Brazil and Korea" → `Emerging Markets Equities`, no
  fan-out to country leaves.

## The `reasoning` sentence (becomes analyst-facing commentary)

One dense analyst sentence in the house's voice: driver, direction, and the
key counterpoint when the source gives one. Good: "SCFR rates Health Care
Most Favored on demographics and steady defensive demand, with regulatory
pricing pressure the main overhang." Bad: "The source is positive on
healthcare."
