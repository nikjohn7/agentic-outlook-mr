# Arbitrate a conflicting allocation call

Two or more validated candidates from the SAME source assign different views
to the SAME sub-asset class. Decide which single candidate states the source's
actual current stance. You are applying the house's own publication
conventions, not your market opinion. A deterministic layer downstream keeps
the winner (always flagged for analyst review), records the losers with your
reasoning, and falls back to unresolved-conflict routing if you return null.

Decision rules, in order:

1. **Published level wins.** A printed dial, score, tier, or positioning-grid
   cell is the call; prose tone and change verbs ("upgraded", "increasingly
   cautious") describe the journey, never override the printed destination.
2. **Specific beats general.** Evidence that names the sub-asset class
   directly beats evidence about a broader bucket it merely belongs to.
3. **Current beats conditional.** The stated base-case positioning beats
   scenario, hedge, or "if X then Y" commentary.
4. **Same-direction duplicates are not conflicts** — if the difference is
   intensity, not sign, choose the clearer statement of the same stance.
5. If, after these rules, the source genuinely holds both views at once
   (e.g. explicit different horizons with no primary stance), return
   `winning_index: null`.

## Calibration conventions (style guidance only)

{{brain_examples}}

## Output contract

Return exactly one JSON object and nothing else:

```json
{
  "winning_index": 0,
  "reasoning": "one or two sentences naming the rule applied and the evidence that decided it"
}
```

Use `"winning_index": null` only for rule 5. `reasoning` is always required.
