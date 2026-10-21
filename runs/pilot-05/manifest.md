# Run Manifest

## Run configuration
- engine: codex
- model: gpt-5.5
- effort: high
- checker: claude/opus/medium
- arbiter: claude/sonnet/high
- grouper: claude/sonnet/medium
- group notes: prev-excel/group-notes.md

## Grouping
- group notes: prev-excel/group-notes.md
- group-1: schroders-our-multi-asset-investment-views-march-2026, schroders-quarterly-markets-review-q1-2026 — note: Read the Schroders "Our multi-asset investment views – March 2026" along with the Schroders "Quarterly markets review - Q1 2026" report when making calls — treat the two as one combined source.
- group-2: j-p-morgan-asset-management-global-asset-allocation-views-2q-2026, j-p-morgan-asset-management-global-fixed-income-views-2q-2026 — note: Read the J.P. Morgan Asset Management "Global Asset Allocation Views 2Q 2026" along with the J.P. Morgan Asset Management "Global Fixed Income Views 2Q 2026" report when making calls — treat the two as one combined source.

## Candidate reconciliation
- candidates: 131
- kept: 119
- failed: 12
- count check: pass
- chunk failures (no candidate): 0

## Failure reasons
- duplicate_same_view: 10
- checker_sign_mismatch: 1
- quote_not_found: 1

## Sources processed
- aberdeen-investments-emerging-markets-q2-2026-outlook-shifting-sands (html, 10p / 2 chunks): 4 candidates emitted [visual_heavy] [printed-to-pdf] [scrambled pages: p.1, p.2, p.10]
- alliancebernstein-global-macro-outlook-second-quarter-2026 (pdf, 12p / 3 chunks): 30 candidates emitted [scrambled pages: p.3, p.10]
- schroders-quarterly-markets-review-q1-2026 (pdf, 10p / 2 chunks): 0 candidates emitted
- j-p-morgan-asset-management-global-fixed-income-views-2q-2026 (pdf, 4p / 1 chunks): 3 candidates emitted [scrambled pages: p.1, p.2, p.4]
- pimco-layered-uncertainty-conflict-credit-stress-and-ai (pdf, 2p / 1 chunks): 9 candidates emitted [scrambled pages: p.1]
- schroders-our-multi-asset-investment-views-march-2026 (pdf, 8p / 2 chunks): 38 candidates emitted [scrambled pages: p.7]
- j-p-morgan-asset-management-global-asset-allocation-views-2q-2026 (pdf, 6p / 2 chunks): 47 candidates emitted [scrambled pages: p.5, p.6]
