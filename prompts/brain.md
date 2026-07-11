# Brain — worked examples for allocation-call extraction

_Version: v1.6_

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
- Published-level-wins with narrower commentary: a positioning grid places
  `Gold/Precious` in the Overweight column while a nearby note frets about gold
  *miners'* margins. The chart call stands — `Gold/Precious` `O` — because the
  cautious commentary is about a narrower sub-asset (mining equities), not the
  charted leaf; only commentary on gold itself could refine it.
- Bought / "added substantially" → `O` (implied); "largely exited most of
  these" → `U` (implied); "reinforce the case for maintaining exposure" /
  "maintain strategic exposure as a hedge" → `O`.
- "We are closing our overweight in developed-market small caps and moving to a
  neutral stance" → the resulting stance is the call → `N` (not `U`; closing an
  overweight lands at flat, it is not a bearish tilt); "we trimmed our overweight
  in gold but remain overweight" → still `O`. The direction-of-travel verb never
  overrides the stated end state.
- Synthetic resulting-stance contrast: Northstar Allocation writes, "We reduced
  our overweight in emerging-market debt back to benchmark after spreads
  tightened" → `EM Debt - General` `N`, because the final stance is benchmark/
  neutral. By contrast, "we pared our overweight in emerging-market debt but
  remain above benchmark" → `EM Debt - General` `O`; the trim is a smaller
  overweight, not an underweight.

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
- Synthetic rotation example: Harborview Global writes, "We are rotating away
  from expensive Segment A winners whose margins look vulnerable and into
  cheaper Segment B suppliers with improving order books." Emit both sides:
  `Segment A`'s matching leaf `U` (or `basis: inferred` `U` if the leaf bridge
  is one clear analyst step) and `Segment B`'s matching leaf `O`, each carrying
  the rotation evidence. Do not record only the favored destination when the
  source explicitly names the segment it is leaving.
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
- "There is a risk that long-dated government bonds sell off if fiscal deficits
  keep widening, but we are not taking an active duration position here" →
  `UNCERTAIN` (a flagged risk with no stance taken is not a `U`; the house
  explicitly declines to position).

## Snapping

- "Mag 7" / U.S. mega-cap tech → `US Mega-Cap (Tech)`.
- A global multi-asset team's unqualified "high yield" → `Global HY`; "U.S.
  high yield… 7.7% yield-to-worst" → `US HY`.
- "EM equities, e.g. Brazil and Korea" → `Emerging Markets Equities`, no
  fan-out to country leaves.
- **Infer at the granularity the prose names** (contrast the row above). A macro
  house writes: "Indonesia's nickel-processing boom keeps drawing sustained
  foreign investment, and Vietnam is capturing the supply chains relocating out
  of China." The taxonomy has both `Indonesia Equities` and `Vietnam Equities`,
  so the single-step inference lands on the two **named** country leaves —
  `Indonesia Equities` `O` **and** `Vietnam Equities` `O` (`basis: inferred`,
  each carrying that same quoted span) — and the regional aggregate `Asia
  Equities` is **not** emitted. Two named countries → two country candidates is
  the multi-call pattern (each leaf is named in the evidence), not a fan-out;
  the "no fan-out" rule only bars pushing a *broad stated* call ("we favor EM
  equities") down onto countries the source never named.

## The `reasoning` sentence (becomes analyst-facing commentary)

One dense analyst sentence in the house's voice: driver, direction, and the
key counterpoint when the source gives one. Good: "SCFR rates Health Care
Most Favored on demographics and steady defensive demand, with regulatory
pricing pressure the main overhang." Bad: "The source is positive on
healthcare."
