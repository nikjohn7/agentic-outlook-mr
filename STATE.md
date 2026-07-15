# Markets Recon / Allocator Pro POC - State

_Last updated: 2026-07-15_

## Current State

Markets Recon is a local-first POC that ingests fund and asset-manager outlook documents and produces reviewable O/N/U/UNCERTAIN sub-asset-class calls against the locked taxonomy in `excel-file/Asset Class List - Locked.csv`. The pipeline is deterministic for ingestion normalization, taxonomy lookup, evidence validation, confidence scoring, assembly, date handling, and reconciliation; LLM calls are limited to extraction and categorical review. `src/run.py` processes up to 20 sources per run with per-step engine/model/effort defaults, and `src/reconcile.py` produces the final firm-level output from dated batch outputs. Reader summaries use `src/summarize.py`: `digest` and `firmpages` both default to `claude/claude-sonnet-5/high`, then `bind` produces the Word document deterministically. The 98-source production batch is complete and the reconciled cross-batch master deliverable contains 3,630 output rows and 1,386 client failure rows after human review. The second 145-row batch remains in progress; authoritative operational instructions are in `docs/PIPELINE_RUNBOOK.md`, `docs/run-records/`, and the batch handoff files. Run the test suite with `.venv/bin/python -m unittest discover -s tests`.

## Recent Changes

- 2026-07-15: Aligned the firmpages default with digest at `claude/claude-sonnet-5/high`; updated the model-matrix regression test.
- 2026-07-15: Completed human review of the cross-batch master reconcile, accepting 15 supersessions, applying four overrides, and resolving the Triodos/Oil needs-human item.
- 2026-07-13: Evaluated Sonnet versus Codex Luna for digest generation; documented split-parity routing for the in-progress second batch.
- 2026-07-13: Added `docs/PIPELINE_RUNBOOK.md` with the operational sequence from intake through final reconciliation.
- 2026-07-11: Added the opt-in near-leaf reconciliation stage, audit output, taxonomy-coverage review, and regression tests.
- 2026-07-10: Added checker evidence-context routing behind the opt-in `--checker-context` flag; the default remains off pending an isolated replay evaluation.
- 2026-07-10: Established the current per-tool LLM model matrix and regression coverage in `tests/test_model_matrix.py`.
- 2026-07-10: Built date backfill and firm reconciliation stages, including deterministic apply/rebuild paths and audit artifacts.

## Next / Open

- Finish the remaining 145-row batch splits and reader digests, then combine, datefill, reconcile, generate firmpages, and bind the Word deliverable.
- Review whether the opt-in near-leaf reconciliation and checker evidence-context features should be promoted to their default workflows.
- Resolve the open ground-truth discrepancies with the Markets Recon team and obtain a broader analyst-reviewed evaluation set.
- Confirm the set of acceptable model providers for the POC.
