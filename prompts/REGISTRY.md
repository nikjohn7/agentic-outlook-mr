# Prompt Registry

Prompt files live in this directory and are versioned here so the headless CLI
workflow can later port to an API with the same contract.

## Active Prompts

### `conventions.md` — v1.2 (2026-07-06)

v1.2 (post-test2-01 fix wave, Tasks 2-3): extends the resulting-stance rule
from close/trim language to reduce / neutralize / dial back / scale back / pare
/ moved back to neutral language. The call lands on the stance the firm ends
at, not the direction of travel, unless the final position remains explicitly
over- or underweight. Adds the two-sided rotation/diversification convention:
when a document favors one segment because it is cautious on another, emit both
the beneficiary and the source-of-rotation side where both leaves exist. Mirrored
in `check_candidates.md` v1.6 and illustrated with synthetic examples in
`brain.md` v1.5.

### `conventions.md` — v1.1 (2026-07-06)

v1.1 (pilot-05 fix list, Task 3): two convention tweaks. (1) Closing or trimming
a position lands at its **resulting stance**, not the direction of travel:
closing an overweight → `N` (not `U`); trimming but staying overweight → `O`.
(2) A **hedged risk note with no position taken → `UNCERTAIN`, not `U`**: a
scenario/risk caveat the house flags without adopting a side is not a `U`.
Mirrored in `check_candidates.md` v1.3 (categorical `supports_view: fail` when
the analyzer violates either) and illustrated by two synthetic examples in
`brain.md` v1.3 (neither drawn from the 7 pilot docs).

### `conventions.md` — v1 (2026-07-05)

Not a prompt: the shared normative-rules block, injected as `{{conventions}}`
into `analyze_chunk.md`, `check_candidates.md`, and `arbitrate_conflict.md`
(loaded at runtime by `run.py` `_conventions_text()`). Factored out of
`brain.md` so the house rules — dialect→O/N/U mapping, published-level-wins,
implied-call rules (incl. two-sided-path-nets-to-`N`), not-a-call boundaries,
snapping defaults — have ONE edit point: when Markets Recon feedback changes a
convention, extractor, checker, and arbiter update together, with no drift.
`brain.md` keeps the worked examples and reasoning style (analyze-only).
Rationale: the pilot-03 JPY failure — the extractor applied the netting
convention, the convention-blind checker failed it as a sign mismatch.
Checker independence is preserved: it still never sees the source and judges
only the presented fields; it now judges them under the same law.

### `analyze_chunk.md` — v1.6 (2026-07-06)

v1.6 (post-pilot-06, country-granularity inference): adds a granularity rule to
the `basis: inferred` section — infer at the granularity the prose names. When
inference-grounding prose names a specific country/market and the injected
taxonomy has that country's leaf, the single-step inference lands on the NAMED
country leaf (e.g. `Taiwan Equities`), not the regional aggregate (`Asia
Equities`); the regional leaf is emitted only for genuinely regional prose or
when the named country has no leaf (snap to the nearest containing leaf, as
before). Several countries named in one passage → one inferred candidate per
named country carrying the same spans (the multi-call pattern, kept by the
assemble-side cross-leaf dedup because the leaves are named in the evidence);
this explicitly does not relax the standing prohibition on fanning a broad
*stated* call down to unnamed child leaves. Prose-text only, no schema change;
the checker's `asset_match` question already polices subject identity, so it is
not mirrored there. Illustrated by one synthetic worked example in `brain.md`
v1.4 (invented firm, invented Indonesia/Vietnam prose — not drawn from any pilot
doc). Motivated by the pilot-06 GT comparison
(`runs/pilot-06/gt-comparison.md`): Aberdeen's country-equity claims snapped
into one coarse "Asia Equities O" leaf, which can also mask an opposite
country-level call.

### `analyze_chunk.md` — v1.5 (2026-07-06)

v1.5 (Rubric v2): widens `call_language` from the coarse legacy buckets to
graded categorical judgments: `explicit_dial`, `explicit_stance`,
`directional`, `implied`, `none`. The prompt gives anchored definitions and
one synthetic example for each non-empty tier, plus the strictness rule that if
a sentence would still read naturally with the positioning verb removed, it is
`directional`, not `explicit_stance`. It does not contain point values; the
model supplies the bucket only and `src/confidence.py` owns all arithmetic.
Legacy frozen `explicit` candidates still parse as `explicit_stance`.

### `analyze_chunk.md` — v1.4 (2026-07-06)

v1.4 (pilot-05 fix list, Tasks 1 & 4): adds a required **`basis`** tag to every
candidate — `stated` (explicit dial/prose position, first-class and unchanged),
`forecast_delta` (a house forecast endpoint vs. current level; also requires
`delta_value` + `delta_unit` so the deterministic materiality gate in
`src/confidence.py` can size the move), or `inferred` (a single-step
analyst-style read from macro/thematic prose to a leaf the source never
explicitly positions). Inferred calls are now **encouraged** but strictly
bounded: verbatim prose spans required, single step only, two-sided/weak →
`UNCERTAIN` or nothing, and an inference may never replace or contradict a
stated call on the same leaf (stated wins). The JSON contract gains
`basis`/`delta_value`/`delta_unit` (the delta fields only for `forecast_delta`).
The checker (`check_candidates.md` v1.3) verifies inferred candidates are a
plausible single step; `src/confidence.py` gates immaterial deltas, caps
forecast_delta and inferred calls below High, and forces review.

### `analyze_chunk.md` — v1.3 (2026-07-05)

v1.3: `evidence_quote` for `prose` may now be a JSON **array** of verbatim
spans (in document order) for an honestly elided quote — where the support
lives in two or three separated passages (e.g. a two-sided path that nets to
`N`, up-leg in one sentence and down-leg in a later one). Each span is checked
verbatim on its own downstream, so the previous single-contiguous-quote gate no
longer structurally penalizes calls that inherently need multi-span evidence.
The prompt forbids the old workaround (one string with `...` between passages)
and forbids reordering spans. A single contiguous quote stays a plain string.
Rationale: pilot-04's AllianceBernstein Euro Govt Bonds `N` call failed
`quote_not_found` only because its quote joined two real passages with `...`;
both fragments pass verbatim individually.

### `analyze_chunk.md` — v1.2 (2026-07-05)

v1.2: injects `{{conventions}}` (normative section before the calibration
examples); `{{brain_examples}}` now carries worked examples + reasoning style
only.

### `analyze_chunk.md` — v1.1 (2026-07-04)

The one LLM step. Reads a single native chunk (PDF page range viewed as rendered
pages, or HTML extracted text), finds allocation calls, snaps each to a locked
leaf, and assigns view + evidence + locator. Emits the candidate-call JSON
contract in `src/schemas.py` (`{candidates: [...], summary}`), one paragraph
summary for rolling memory.

Runtime-filled placeholders (`{{name}}`, substituted by `src/llm.py`
`template_vars`; the API port fills the same placeholders):
- `{{chunk_content}}` — the chunk (PDF path + page range to view natively, or
  the HTML text slice), built by `run.py`.
- `{{memory}}` — the source's rolling `memory.md` (continuity across chunks).
- `{{taxonomy}}` — the full 396-leaf locked taxonomy, injected from
  `taxonomy.py` (`grouped_block()`) so it can never drift from the locked CSV.
- `{{brain_examples}}` — `brain.md` few-shots, or a "none available" line.
  Loaded at runtime so a pilot-run session need never open the ground truth.

Machine-readable inputs (appended as JSON by `llm.py`): `source_id`, `chunk_id`
— the model echoes these onto every candidate so downstream keying is exact.

Rationale: extract → snap → call in one prompt (deterministic guardrails catch
errors, not a second round-trip); taxonomy/memory/brain injected rather than
inlined to keep the CSV authoritative and preserve pilot blindness. Rules that
are load-bearing and must survive any edit: call at the level stated (no
parent→child fan-out), semantic snapping with `taxonomy_match` recorded,
`taxonomy_match: none` is emitted (routed to review) not dropped, `UNCERTAIN`
means source ambiguity only, and table/visual evidence needs a specific
table/figure locator.

v1.1 (post-pilot-01): text inside a designed layout artifact (callout box,
sidebar, banner, stat panel, infographic column) must be tagged
`evidence_kind: visual`, not `prose`, even when it is full sentences — the
text snapshot scrambles boxed/multi-column layouts, so the hard verbatim check
rejected 12 correct pilot calls that were misfiled as `prose`. Visual evidence
gets the key-token-on-page check instead.

### `check_candidates.md` — v1.6 (2026-07-06)

v1.6 (post-test2-01 fix wave, Tasks 2-3): mirrors `conventions.md` v1.2 so the
checker polices reduce/neutralize/dial-back/scale-back/pare language as a
resulting-stance question, not direction of travel. Adds a two-sided
rotation/diversification policing line: explicitly two-sided evidence can
support both the favorable and cautionary sides; if the candidate's own evidence
is clearly two-sided but the treatment is incomplete, the checker marks the
relevant dimension `unclear` unless the claimed view is directly contradicted.

### `check_candidates.md` — v1.5 (2026-07-06)

v1.5 (post-test2-01 fix wave, Task 1): adds the text-unverifiable visual route.
Candidates marked `text_unverifiable_visual: true` come from print-captured /
visual-heavy pages where snapshot text could not verify the dial/grid tokens;
the checker must open the supplied `native_source_path`, inspect the cited page
image, and verify the graphic directly. Clear visual confirmation can be
`decisive`; ambiguous graphics pass only as `adequate`/`thin`; absent or
contradictory graphics fail with a note. `src/confidence.py` still owns all
numeric scoring.

### `check_candidates.md` — v1.4 (2026-07-06)

v1.4 (Rubric v2): adds required `evidence_strength` on every checker verdict,
with categorical buckets `decisive`, `adequate`, and `thin`. The checker still
never opens the source and still emits no confidence number; it judges only the
presented candidate fields. `src/confidence.py` maps those categories to the
deterministic adjustment/cap, and missing strength on legacy all-pass verdicts
is treated as `decisive` to preserve old frozen-run semantics.

### `check_candidates.md` — v1.3 (2026-07-06)

v1.3 (pilot-05 fix list, Tasks 3 & 4): mirrors the two new conventions so the
checker can issue a categorical verdict when the analyzer violates them —
closing/trimming an overweight to a flat end state called `U` → `supports_view:
fail`; a hedged risk note with no position taken called `U`/`O` → `fail`. Adds
`inferred`-basis verification: for `basis: inferred` candidates the checker
judges whether the read is a plausible **single step** from the quoted prose to
the leaf (implausible leap or multi-step chain → `fail`). Reads the new `basis`
field now passed in the checker inputs (`run.py`).

### `check_candidates.md` — v1.2 (2026-07-05)

v1.2: teaches the checker to read an **elided** `evidence_quote` (spans joined
with ` ... ` because the support is split across the document — each span
already verified verbatim) as one body of evidence, and to judge whether the
stitched spans together support the `view` (e.g. up-leg + down-leg netting to
`N`) — never failing a call merely because the evidence arrives in spans. Pairs
with `analyze_chunk.md` v1.3, which emits the span list. The checker still sees
the spans joined into one string (`run.py` passes `candidate.evidence_quote`).

### `check_candidates.md` — v1.1 (2026-07-05)

v1.1: injects `{{conventions}}` with the framing "never fail a candidate for
following a convention; reserve `fail` for evidence that contradicts the view
even after the conventions are applied", and `supports_view`'s `N` definition
now includes a two-sided view netting to neutral (the quote legitimately
shows both directions). Live smoke: the pilot-03 JPY candidate
(`checker_sign_mismatch` under v1) passes 3-for-3 under v1.1.

### `check_candidates.md` — v1 (2026-07-04)

The second-reader (checker) step: one call per source over all of that
source's candidates. Judges three categorical questions per candidate —
`supports_view` (evidence supports the view sign), `forward_looking` (stance,
not market recap), `asset_match` (evidence is about the named leaf) — each
`pass | unclear | fail`, plus a `note`. No source access, no re-extraction,
and **no self-confidence number**: verdicts are facts consumed by the
deterministic rubric (`src/confidence.py`) — any `fail` hard-fails the
candidate to review (`checker_sign_mismatch` / `checker_not_forward_looking`
/ `checker_asset_mismatch`); anything short of all-pass caps confidence at 74
so the call cannot reach High. Default engine codex @ high effort
(`--checker-engine/--checker-model/--checker-effort`). Inputs (appended JSON):
source_id, firm, source_title, candidates[] with echoed `index`.

### `resolve_groups.md` — v1 (2026-07-05)

The group-notes resolver: runs once at run start, only when `--group-notes
<file>` supplies free-text analyst notes naming which sources to combine
(e.g. a firm's review + outlook pair, which analysts output as ONE
pipe-joined source). Translates note lines into `source_id` groups drawn
strictly from the run's source list; anything it cannot confidently map goes
to `unmatched_notes` — flagged in the manifest, never guessed. Deterministic
guards in `run.py` `_resolve_groups` drop unknown/overlapping ids with
warnings; the plan is frozen to `work/<run-id>/groups.json` and echoed in the
manifest, and all downstream grouping (cross-doc rolling memory, group-keyed
dedup/conflict, pipe-joined output rows) consumes the plan, not the notes.
Resolver failure degrades to an ungrouped run. Default engine codex @ low
effort (`--grouper-*` flags). Inputs (appended JSON): sources[] with
source_id/firm/title/date; `{{group_notes}}` injected.

### `arbitrate_conflict.md` — v1.1 (2026-07-05)

v1.1: `{{conventions}}` replaces `{{brain_examples}}` (the rules ARE the
conventions; worked examples stay analyze-only); scope extended to
analyst-grouped source sets, with rule 3 gaining "within a grouped set, the
forward-looking outlook document beats the retrospective review document"
(pending client confirmation); per-candidate `source_id` added to inputs.

### `arbitrate_conflict.md` — v1 (2026-07-04)

The conflict-arbiter step: called only when validated candidates give the same
(source, leaf) different views. Applies house publication conventions —
published-level-wins, specific-beats-general, current-beats-conditional — and
names a `winning_index` or `null` (→ falls back to `unresolved_conflict`
routing). Winner is kept but always flagged `review` with the arbiter's
reasoning appended to commentary; losers land in `failures.csv` as
`arbitrated_out`. The arbiter is deliberately NOT shown the deterministic
confidence scores (anchoring). `{{brain_examples}}` injected for calibration.
Default engine codex @ medium effort (`--arbiter-*` flags).

### `brain.md` — v1.5 (2026-07-06)

v1.5 (post-test2-01 fix wave, Tasks 2-3): adds two synthetic worked examples.
One contrasts reducing an overweight back to benchmark (`N`) with paring an
overweight but remaining above benchmark (`O`). The other shows a rotation away
from one segment into another as two calls: the source segment cautionary side
and the destination beneficiary side. Synthetic throughout, preserving the
blindness protocol.

### `brain.md` — v1.4 (2026-07-06)

v1.4 (post-pilot-06, pairs with `analyze_chunk.md` v1.6): one synthetic worked
example in the Snapping section teaching "infer at the granularity the prose
names" — an invented macro house naming Indonesia and Vietnam yields two
country-leaf inferred candidates (`Indonesia Equities` O, `Vietnam Equities` O),
with the regional aggregate `Asia Equities` explicitly NOT emitted, framed as
the contrast to the adjacent broad-stated "no fan-out" example. Synthetic
throughout (invented firm, invented country prose), preserving the pilot
blindness protocol.

### `brain.md` — v1.3 (2026-07-06)

v1.3 (pilot-05 fix list, Task 3): two synthetic worked examples for the new
conventions — closing an overweight → `N` (small-cap example), trim-but-stay →
`O` (gold example); and a hedged duration risk note with no position taken →
`UNCERTAIN`. Both are synthetic patterns, deliberately NOT drawn from the 7
pilot docs, preserving the blindness protocol.

### `brain.md` — v1.2 (2026-07-05)

v1.2: the normative rules moved to `conventions.md` (single edit point,
shared with checker/arbiter); `brain.md` now carries only the worked examples
(dialect translation, implied calls, not-a-call boundaries, snapping) and the
`reasoning`-sentence style, still injected into `analyze_chunk.md` only via
`{{brain_examples}}`. Content and blindness provenance unchanged from v1.1.

### `brain.md` — v1.1 (2026-07-04)

Analyst-calibration few-shots consumed by `analyze_chunk.md` via
`{{brain_examples}}` (loaded at runtime by `run.py` `_brain_text()`; absent is
still fine). Built in a session separate from any pilot run, from the user's
five-source ground truth in `ground-truth/ground-truth.csv` (Charles Schwab,
CBRE IM, Cantor Fitzgerald, BMO GAM, Barings) — **none of the 5 pilot sources
appear in it**, preserving the blindness protocol. Every ground-truth row was
validated against the locked taxonomy (69/69 exact leaves + parent lookups) and
verified against all five sources (Schwab, Cantor, Barings live pages; CBRE
PDF read page-by-page; BMO via a user-supplied print-to-PDF after the site
proved unreachable — its positioning dials confirmed 11 rows and exposed 4
prose-vs-dial ground-truth discrepancies, flagged to the user).

v1.1 added the published-level rule after BMO verification: a printed dial/
score/tier is the call; prose tone and change verbs ("upgraded to neutral",
"caution has increased") never override the printed level.

Content (~1.4k tokens, kept deliberately small): house-scale → O/N/U
translation (sign-not-intensity, tier collapse, rating-beats-hedging-prose,
published-level-wins),
portfolio actions as implied calls, forecast/ranking/posture implication rules
(price forecast revision → commodity call; yield forecast up → sovereign `U`;
cross-market ranking cuts both ways; macro house view → its investable
universe; two-sided rate path nets `N`; regional netting with per-leaf
carve-outs), not-a-call boundaries (market-move reporting, absence of mention,
non-conviction → `N` not `UNCERTAIN`), snapping defaults (GICS names, house
scope for generic instruments, snap-up to regional leaves), and `reasoning`
sentence style (drives `Full Commentary`).
