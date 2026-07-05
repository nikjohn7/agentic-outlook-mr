# Prompt Registry

Prompt files live in this directory and are versioned here so the headless CLI
workflow can later port to an API with the same contract.

## Active Prompts

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
