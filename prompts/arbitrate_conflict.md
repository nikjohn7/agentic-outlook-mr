# Arbitrate a conflicting allocation call

Two or more validated candidates from the same source - or the same
analyst-grouped source set (e.g. a firm's review document paired with its
outlook document) - assign different views to the SAME sub-asset class. Decide which single candidate states the source's
actual current stance. You are applying the house's own publication
conventions, not your market opinion. A deterministic layer downstream keeps
the winner (always flagged for analyst review), records the losers with your
reasoning, and falls back to unresolved-conflict routing if you return null.

Decision rules, in order:

0. **Eligibility first.** Before ranking anything, decide which candidates
   are a house position or recommendation on this asset, under the
   conventions' "A call is a position, not an analytical input". Set aside
   every candidate whose evidence is only an analytical input the source does
   not adopt as its allocation (a trend, momentum, valuation, volatility,
   risk, economic or historical-performance reading; a raw weight with no
   stated neutral), mixed conditions with no stance drawn, or a product claim
   with no recommendation. A signal that is easier to read, more precise or
   more specific gains no authority from that. Rules 1-4 rank only the
   eligible candidates.
   - Exactly one eligible candidate → it wins.
   - No eligible candidate → return `winning_index: null`. Do not pick the
     least bad signal: two inputs disagreeing (say trend against valuation)
     is not a house stance, and the system must not manufacture one.
1. **Published house level wins.** A printed house rating - a dial, score,
   tier or positioning-grid cell showing the house's position, or a current
   allocation against a stated neutral - is the call; prose tone and change
   verbs ("upgraded", "increasingly cautious") describe the journey, never
   override the printed destination.
2. **Specific beats general.** Evidence that names the sub-asset class
   directly beats evidence about a broader bucket it merely belongs to.
3. **Current beats conditional.** The stated base-case positioning beats
   scenario, hedge, or "if X then Y" commentary. Within a grouped source set,
   the forward-looking outlook document beats the retrospective review
   document.
4. **Same-direction duplicates are not conflicts** - if the difference is
   intensity, not sign, choose the clearer statement of the same stance.
5. If, after these rules, the source genuinely holds both views at once
   (e.g. explicit different horizons with no primary stance, or opposite
   positions on two segments the one taxonomy leaf cannot tell apart), return
   `winning_index: null`. Do not force a winner where the house's positions
   really are opposed.

## House conventions (normative)

{{conventions}}

## Output contract

Return exactly one JSON object and nothing else:

```json
{
  "winning_index": 0,
  "reasoning": "one or two sentences naming the rule applied and the evidence that decided it"
}
```

Use `"winning_index": null` only for rule 0 (no eligible candidate) or rule 5.
`reasoning` is always required; when rule 0 set a candidate aside, name it
and say it is not an allocation stance (e.g. "index 1 is a valuation-signal
cell, not an allocation stance").
