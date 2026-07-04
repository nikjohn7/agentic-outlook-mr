# Prompt Registry

Prompt files live in this directory and are versioned here so the headless CLI
workflow can later port to an API with the same contract.

## Active Prompts

### `analyze_chunk.md` — v1 (2026-07-04)

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
