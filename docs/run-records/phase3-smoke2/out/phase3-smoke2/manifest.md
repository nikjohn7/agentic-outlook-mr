# Run Manifest

## Run configuration
- engine: codex
- model: gpt-5.5
- effort: medium
- checker: claude/sonnet/medium
- arbiter: claude/sonnet/medium
- quote visual verifier: claude/sonnet/medium

## Candidate reconciliation
- candidates: 26
- kept: 14
- failed: 12
- count check: pass
- chunk failures (no candidate): 1

## Call basis (kept rows)
- stated: 14

## Checker strength (kept rows)
- adequate: 6
- decisive: 5
- thin: 3

## Call language (kept rows)
- directional: 12
- explicit_stance: 2

## Quote match tier (kept rows)
- exact: 1
- normalized: 6
- subsequence: 4
- visual: 3

## Quote visual verification
- absent: 1
- attempted: 4
- malformed: 0
- present_paraphrase: 0
- present_verbatim: 3

## Failure reasons
- taxonomy_no_match: 8
- checker_sign_mismatch: 1
- duplicate_cross_leaf: 1
- duplicate_same_view: 1
- ingest_error: 1
- quote_not_found_visual: 1

## Sources processed
- alliancebernstein-multi-asset-midyear-outlook-fortitude-amid-disruption (pdf, 5p / 1 chunks): 14 candidates emitted
- impax-asset-management-mid-year-credit-outlook-2026 (pdf, 6p / 2 chunks): 12 candidates emitted [scrambled pages: p.2, p.3, p.4, p.5]
- smoketest-deliberate-failure (html, 0 chunks): 0 candidates emitted [ingest-failed: Error: Page.goto: net::ERR_NAME_NOT_RESOLVED at https://definitely-not-a-real-domain-phase3-smoke.invalid/outlook.html
Call log:
  - navigating to "https://definitely-not-a-real-domain-phase3-smoke.invalid/outlook.html", waiting until "load"]
