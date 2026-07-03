# Markets Recon / Allocator Pro POC Design

This document is the Phase 1 design skeleton. `POC_PLAN.md` remains the source
of truth for architecture decisions while the implementation catches up.

## Phase 1 Contract

- Deterministic taxonomy validation lives in `src/taxonomy.py`.
- Candidate-call schema lives in `src/schemas.py`.
- Confidence is computed in `src/confidence.py`; the LLM never supplies the
  final score.
- Run output is `Target Output.csv` columns plus `confidence`, `band`, and
  `review_flag`. One-hot columns are intentionally omitted.
- Hard validation failures go to `failures.csv`; they are never converted to
  `UNCERTAIN`.

## LLM Port Notes

- `src/llm.py` keeps Claude and Codex swappable via engine configs.
- Current mode is subscription-backed headless subprocesses.
- API port later should keep the same prompt files and JSON contract, then add
  temperature/seed controls, structured outputs, and batch pricing choices.

## Client Runbook Placeholder

Later deliverable: document how the client runs the pipeline with their own
Claude or Codex subscription login, without API keys.
