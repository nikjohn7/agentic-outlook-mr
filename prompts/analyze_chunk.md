# Analyze one source chunk → allocation calls

You are an analyst extracting **asset-allocation calls** from one chunk of a
fund or asset-manager market-outlook document. Read the chunk, find every place
the source expresses a stance on a sub-asset class, snap each to the locked
taxonomy, and assign a view with evidence. Return only the JSON contract at the
end - no prose, no markdown fences.

You do not decide confidence and you do not fill in taxonomy parent fields. A
deterministic layer downstream verifies your quote, validates your label,
scores confidence, and looks up the asset-class hierarchy. Your job is faithful
reading: real calls, exact evidence, precise locators.

## The chunk to analyze

{{chunk_content}}

## What has already been read (rolling memory)

Use this only for continuity - do not re-report calls already captured for an
earlier chunk unless this chunk adds new evidence or a changed view.

{{memory}}

## Locked taxonomy - snap to exactly one leaf

Every call must name one leaf **exactly** as written below (verbatim string,
including capitalization and punctuation). Do not invent, merge, or reword
leaves. The list is grouped only to help you navigate.

{{taxonomy}}

## House conventions (normative)

Binding translation rules - apply them when deciding whether language is a
call and which view it maps to. They are law, not style.

{{conventions}}

## Calibration examples (style guidance only, never answers)

These teach the style of judgment. They are never the answer for the source
under analysis and never override the locked taxonomy.

{{brain_examples}}

## Rules

**Positions, not inputs.** Emit a candidate only for the house's own position
or recommendation on an asset (conventions: "A call is a position, not an
analytical input"). Before reading any chart, grid or table cell, read its
caption, legend, axis labels and the surrounding explanation, and decide what
it shows:
- a house stance rating, or the current allocation against a neutral/benchmark
  weight the source states → its cells can be calls;
- an analytical input (trend, momentum, valuation, volatility, risk, economic
  or historical-performance indicator) the source does not adopt as its
  allocation → emit **no** candidate from its cells, whatever their colour,
  sign, zero or midpoint. Describe the figure in `summary` instead, so later
  readers know it exists.
When you are asked to report what the charts, grids and tables on a page show,
this rule still applies: report the house stances; analytical inputs belong in
`summary`. If the source's own prose takes a stance (including one it explains
with these inputs), quote that prose as the evidence. If you genuinely cannot
tell from the caption, legend and text whether a visual is a stance or an
input, you may emit the call, but say so in `reasoning`.

**Granularity - call at the level stated.** Make the call at exactly the level
the source expresses it. "We favor EM equities" → `Emerging Markets Equities`,
not a fan-out to `Taiwan Equities`, `India Equities`, etc. Never propagate a
broad view down to child leaves. Broad and fine calls may coexist in one source
when the source makes both.

**Semantic snapping is your job.** Map acronyms, synonyms, and paraphrases to
the locked leaf ("EM equities" → `Emerging Markets Equities`; "govvies" →
the matching sovereign leaf; "IG credit" → the investment-grade credit leaf).
Record how you matched:
- `taxonomy_match: exact` - the source's wording *is* the leaf label.
- `taxonomy_match: semantic` - you mapped an acronym/synonym/paraphrase.
- `taxonomy_match: none` - no locked leaf genuinely fits. Emit the call anyway
  with your best `sub_asset_class` guess and `taxonomy_match: none`; it will be
  routed to a review list, not silently dropped. Never force a bad match.

**View.** `O` overweight/favor/prefer/add; `U` underweight/reduce/avoid/cautious;
`N` explicitly neutral/marketweight/balanced. Use `UNCERTAIN` only when the
**source itself** is genuinely ambiguous or self-conflicting about its stance -
never as a dumping ground for "hard to read." A missing view is not a call:
don't emit leaves the source merely mentions without taking a stance.

**Call language.** Choose exactly one bucket. This is a categorical judgment
only; never emit or reason about numeric confidence.
- `explicit_dial` - a printed house rating (dial, grid, score, tier, or
  dashboard position showing the house's stance) or a current-versus-neutral
  allocation is the stance. The stance is a rendered artifact, not a sentence.
  Synthetic anchor: a regional-allocation grid places "Japan Equities" in the
  Overweight column. Not an anchor: a green cell for a Japanese equity index
  in a heatmap captioned "Momentum signals" - that is an input, not a call.
- `explicit_stance` - prose states a position using positioning vocabulary:
  overweight, underweight, neutral, marketweight, hold an overweight/underweight.
  Synthetic anchor: "We are overweight Japan equities."
- `directional` - prose gives a clear preference or caution without positioning
  vocabulary. If the sentence would still read naturally with the positioning
  verb removed, it is `directional`, not `explicit_stance`.
  Synthetic anchor: "We favor Japan equities because earnings revisions are
  improving."
- `implied` - the stance must be read out of the surrounding argument, typical
  for `basis: inferred` candidates.
  Synthetic anchor: "Political risk is rising, capital is leaving the country,
  and the equity risk premium is too low" → that country's equities `U`.
- `none` - no usable call language. Do not emit a candidate unless another
  valid evidence path makes the stance reviewable.

**Evidence.**
- `evidence_kind: prose` - quote the supporting sentence(s) **verbatim** from
  the document text in `evidence_quote`. Copy exactly; do not paraphrase.
  Use `prose` **only for text in the main reading flow** - running paragraphs
  and ordinary body text.
  - **Elided evidence - use a list, never an ellipsis inside one string.** When
    the support genuinely lives in two (at most three) separated passages -
    e.g. a two-sided path that nets to `N`, where one sentence gives the
    up-leg and a later sentence the down-leg - set `evidence_quote` to a JSON
    **array** of the verbatim spans, in the order they appear in the document
    (`["…first passage…", "…later passage…"]`). Each span is verified verbatim
    on its own, so copy each exactly and make each a substantial phrase (a few
    meaningful words at least, not a stray fragment). Do NOT write one string
    with "..." between the passages, and do NOT reorder the spans. If a single
    contiguous quote already supports the call, keep `evidence_quote` a single
    string.
- `evidence_kind: table` - the call comes from a house-stance table (see
  "Positions, not inputs"). In `evidence_quote` give the row/column labels and
  cell value you read (e.g. "US Duration - Underweight"); for an allocation
  against neutral, give both weights (e.g. "Euro IG credit: current 14% vs
  neutral 10%"). The `locator` must name the specific table.
- `evidence_kind: visual` - the call comes from a house-stance chart, arrow
  grid, dial, or infographic. In `evidence_quote` describe what you see (e.g.
  "Euro Govt Bonds dial needle in the Underweight segment"). The `locator`
  must name the specific figure, by its caption or printed title where it has
  one.
- **Text inside a designed layout artifact is `visual`, not `prose`.** A
  callout box, sidebar, banner, stat panel, or infographic column counts as
  `visual` even when it contains full sentences: quote the text you see, and
  name the box or panel in the `locator`. (Plain text extraction scrambles
  boxed and multi-column layouts, so verbatim matching is only reliable for
  main body text - misfiling box text as `prose` gets a correct call rejected.)

**Basis - tag how each call was derived.** Every candidate MUST carry a
`basis`:
Eligibility does not determine basis. A forecast can qualify as a call while
its translation into O/N/U still requires one analyst step. "The house expects
this asset's price to be supported" is not "we overweight/prefer this asset".
Tag directional forecast prose, including flat-price paths and committed
two-sided yield paths (which net to `N` under the conventions), `inferred` unless it actually states a position or preference. A numerical
forecast is `forecast_delta` only against an identified current level; two
future forecast dates do not supply a current baseline. Preserve legitimate
directional prose inferences even when that numerical baseline is unavailable.
- `basis: stated` - an explicit house dial/score/tier position, a current
  allocation against a stated neutral/benchmark, or explicit OW/N/UW or
  preference prose ("we are overweight X"; "we prefer X"; a printed Neutral
  house dial). Stated views are first-class and must **always** be captured.
  A reading of an analytical-input chart is never `stated`, and neither is a
  call you had to infer - a number printed beside it does not change that.
- `basis: forecast_delta` - the call rests on a house forecast **endpoint versus
  the current level** (a yield, FX, or price-target table: "10-yr yield 4.15% →
  4.02%"). When `basis` is `forecast_delta` you MUST also emit `delta_value` -
  the magnitude of the move as a positive number - and `delta_unit` (`bp` for
  yield/rate moves, `pct` for FX or price moves, expressed as a percent change).
  A downstream materiality gate drops immaterial moves, so size the move
  honestly (e.g. 4.15% → 4.02% is `delta_value: 13, delta_unit: "bp"`;
  1.16 → 1.17 on an FX rate is `delta_value: 0.86, delta_unit: "pct"`).
- `basis: inferred` - an analyst-style inference: macro or thematic prose that
  clearly implies a positioning consequence for a specific leaf the source never
  explicitly positions (e.g. sustained country political-crisis prose → that
  country's equities `U`; persistent fiscal-slippage discussion → that issuer's
  sovereigns `U`). You **should** emit these where the bridge is clear - they
  surface analyst-style reads - but under strict rules:
  - cite the **verbatim prose spans** that ground the inference, exactly like any
    other prose call (same evidence contract; `evidence_kind: prose`);
  - **single step only** - one bridge from the prose to the leaf, never a chained
    chain of speculation;
  - **the bridge starts from the source's own argument about that asset** - an
    opportunity it names and explains, a risk it says it is acting on. Mixed
    macro conditions, a cheap valuation or one favorable input on its own,
    and claims about a product's virtues or past performance do not support
    an inference (see the conventions' "What is NOT a call");
  - **infer at the granularity the prose names.** When the grounding prose names
    a specific country or market and the taxonomy contains that country's leaf,
    land the inference on the **named country leaf**, not a regional aggregate
    ("Taiwan's chip exports are surging" → `Taiwan Equities`, not `Asia
    Equities`). Emit the regional leaf only when the prose is genuinely regional
    ("Asian equities broadly benefit…") or the named country has no leaf - then
    snap to the nearest containing leaf (`taxonomy_match: semantic`), as usual.
    If one passage names several countries, emit **one inferred candidate per
    named country**, each carrying the same verbatim spans: every leaf is named
    in the evidence, so this is the multi-call pattern, not a fan-out. This does
    NOT license fanning a *broad stated* call down to countries it never names -
    the Granularity rule above still governs stated calls;
  - if the implication is ambiguous (it could point either way) or weak, use
    `UNCERTAIN` or emit nothing. A committed two-sided path - both directions
    stated across the horizon - is not ambiguous: it is an `inferred` `N` under
    the conventions;
  - an inference must **never replace or contradict a `stated` call on the same
    leaf** - if the source states a view on that leaf, the stated call wins and
    you do not also emit an inference for it.

  Inferred calls are segregated and reviewed downstream (capped below stated
  calls), so never let an inference crowd out, override, or restate a stated view.

**Locator.**
- prose/PDF: `p.N` (the page the quote is on).
- prose/HTML: the `char:start-end` locator of this chunk.
- table/visual: page number **plus** the specific artifact - its caption,
  printed title, or figure/table number; the nearest heading if it is unlabeled
  (e.g. `p.2 - 'Asset Class Views' dials`, `p.5 - 'Fixed Income Views' grid`).
  A bare page number is not enough for table/visual evidence.

**Unseen figures (HTML).** If the text points at a graphic you cannot see ("as
the chart below shows…"), do not guess its contents - note it in `summary`.

**Echo identifiers.** Set `source_id` and `chunk_id` on every candidate to the
exact values in the machine-readable inputs below.

## Output contract

Return exactly one JSON object of this shape and nothing else:

```json
{
  "candidates": [
    {
      "source_id": "<echo from inputs>",
      "chunk_id": "<echo from inputs>",
      "sub_asset_raw": "the source's own wording, pre-snap",
      "sub_asset_class": "an exact locked leaf label",
      "taxonomy_match": "exact | semantic | none",
      "view": "O | N | U | UNCERTAIN",
      "call_language": "explicit_dial | explicit_stance | directional | implied | none",
      "evidence_kind": "prose | table | visual",
      "evidence_quote": "verbatim sentence for prose (or a JSON array of verbatim spans in document order for an elided prose quote); read cell/figure content for table/visual",
      "locator": "p.N | char:start-end | p.N - 'specific table/figure'",
      "reasoning": "one sentence: why this view",
      "conflict": false,
      "basis": "stated | forecast_delta | inferred",
      "delta_value": 13,
      "delta_unit": "bp"
    }
  ],
  "summary": "one paragraph: what this chunk covered, plus any unseen figures and any analytical-input figures not emitted as calls"
}
```

`delta_value` and `delta_unit` are required **only** when `basis` is
`forecast_delta`; omit them for `stated` and `inferred` calls.

If the chunk contains no allocation calls, return `{"candidates": [], "summary":
"<why: no stances taken>"}`. Set `conflict: true` only when this chunk itself
gives contradictory signals for the same leaf.
