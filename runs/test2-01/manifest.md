# Run Manifest

## Run configuration
- engine: codex
- model: gpt-5.5
- effort: high
- checker: claude/opus/medium
- arbiter: claude/sonnet/high
- grouper: claude/sonnet/medium
- group notes: prev-excel/test2/group-notes.md

## Grouping
- group notes: prev-excel/test2/group-notes.md
- group-1: t-rowe-price-monthly-asset-allocation-update-april-2026, t-rowe-price-global-asset-allocation-the-view-from-the-uk — note: Read the T. Rowe Price "Monthly Asset Allocation Update - April 2026" along with the T. Rowe Price "Global Asset Allocation: The View From the UK" report when making calls — treat the two as one combined source.
- group-2: wellington-management-quarterly-asset-allocation-outlook-q2-2026, wellington-management-monthly-asset-allocation-outlook — note: Read the Wellington Management "QUARTERLY ASSET ALLOCATION OUTLOOK: Q2 2026" along with the Wellington Management "Monthly Asset Allocation Outlook" report when making calls — treat the two as one combined source.

## Candidate reconciliation
- candidates: 253
- kept: 142
- failed: 111
- count check: pass
- chunk failures (no candidate): 0

## Call basis (kept rows)
- inferred: 6
- stated: 136

## Checker strength (kept rows)
- adequate: 58
- decisive: 76
- thin: 8

## Call language (kept rows)
- directional: 39
- explicit_dial: 83
- explicit_stance: 14
- implied: 6

## Failure reasons
- duplicate_same_view: 62
- evidence_check_failed: 23
- arbitrated_out: 13
- quote_not_found: 10
- duplicate_cross_leaf: 3

## Sources processed
- blackrock-equity-market-outlook-q2-2026 (pdf, 13p / 3 chunks): 29 candidates emitted [scrambled pages: p.2, p.3, p.4, p.5, p.6, p.7, p.8, p.9, p.10, p.11]
- goldman-sachs-asset-management-market-know-how-1q-2026 (pdf, 18p / 4 chunks): 67 candidates emitted [scrambled pages: p.11, p.12, p.13]
- t-rowe-price-monthly-asset-allocation-update-april-2026 (html, 6p / 2 chunks): 31 candidates emitted [visual_heavy] [printed-to-pdf] [scrambled pages: p.2, p.5]
- t-rowe-price-global-asset-allocation-the-view-from-the-uk (pdf, 4p / 1 chunks): 34 candidates emitted [scrambled pages: p.3]
- wellington-management-quarterly-asset-allocation-outlook-q2-2026 (html, 10p / 2 chunks): 31 candidates emitted [visual_heavy] [printed-to-pdf] [scrambled pages: p.1, p.9, p.10]
- wellington-management-monthly-asset-allocation-outlook (html, 8p / 2 chunks): 18 candidates emitted [visual_heavy] [printed-to-pdf] [scrambled pages: p.1, p.7, p.8]
- franklin-templeton-allocation-views-from-goldilocks-to-geopolitics-repositioning-for-an-energy-shock (pdf, 14p / 3 chunks): 43 candidates emitted [scrambled pages: p.3, p.4, p.5, p.6, p.7, p.8, p.9]
