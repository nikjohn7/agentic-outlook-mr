# House conventions — normative extraction rules

_Version: v1.2_

These rules bind every reader of an outlook source: the extractor when making
a call, and any second reader when judging one. A call that follows these
conventions is correct *by convention* — never penalize it for the convention
itself. They never override the locked taxonomy or the source's own words.

## Translating the house's dialect into O / N / U

- Map by **sign, not intensity**: "slightly bullish" is still `O`.
- `O` — overweight; favor/prefer/like; bullish; constructive; Most/More
  Favored; attractive entry point; upgraded to positive. `U` — underweight;
  bearish; cautious; Less/Least Favored; avoid/reduce/trim/exited. `N` —
  neutral; marketweight; balanced; a "0" score; the middle tier of any ranked
  scale.
- **The published level wins over prose tone.** A printed dial, score, grid
  cell, or tier label *is* the call; surrounding prose adds color but never
  overrides it. Change verbs describe the journey, not the destination: an
  upgrade that lands at neutral is `N`; "caution has increased" beside a
  printed Neutral dial is `N`.
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
  bottom → `U`.
- When the house's own rating and its hedging prose diverge, the rating wins.

## Implied calls (no "overweight" label required)

- **Portfolio actions are calls**: bought/added → `O`; exited/trimmed → `U`;
  deliberately maintained as an active positive case → `O`. A bare "we hold
  X" with no case attached is not a call.
- **A directional forecast revision on a priced asset is a call on its leaf**;
  a sovereign **yield** forecast revised up is `U` on that bond leaf.
- **Explicit cross-market rankings cut both ways** when both leaves exist
  ("prefer US over Europe" → US `O` and Europe `U`).
- **Recommended hedge/ballast allocations are calls** (`O`).
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
  across its horizon (hikes then cuts; a currency weak near-term but
  appreciating medium-term), the net call is `N` with both sides quoted —
  the evidence for such an `N` legitimately shows both directions, not
  explicit "neutral" language.
- **Mixed views inside one region** with no finer leaf → the region leaf at
  `N` with both sides in the evidence; sub-markets with their own leaf and a
  clear view are also called at their level.

## What is NOT a call

- Reporting a market move with no forward expectation attached.
- Absence of mention — never infer `U` from silence.
- **Explicit non-conviction is `N`, not `UNCERTAIN`** ("no strong directional
  conviction" → `N`). `UNCERTAIN` is reserved for a source that contradicts
  itself or raises a stance it does not take.
- **A hedged risk note with no position taken is `UNCERTAIN`, not `U`.**
  Scenario/risk language the house flags without committing to a side — "there
  is a risk that X sells off if scenario Y plays out", stated as a caveat rather
  than a stance — is `UNCERTAIN`, never a `U`. A flagged downside risk is not a
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

Different firms taking opposite views on the same leaf is normal — judge each
source only on its own words.
