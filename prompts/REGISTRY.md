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

## Planned Prompts

- `brain.md` - Phase 2 few-shot calibration file built in a **separate session**
  from any pilot run, with the 5 pilot sources excluded (blindness protocol).
  `analyze_chunk.md` consumes it via `{{brain_examples}}`; absent is fine.
