# Markets Recon / Allocator Pro POC — State

_Last updated: 2026-07-06_

## Current State

POC for Markets Recon / Allocator Pro: ingest fund/asset-manager outlook sources
(PDF/HTML) and produce reviewable sub-asset-class allocation calls (`O`/`N`/`U`/
`UNCERTAIN`) with citations, against the locked taxonomy in `excel-file/Asset
Class List - Locked.csv`. Full spec: `CLAUDE.md`, `POC_CONTEXT.md`,
`WORKBOOK_SCHEMA.md`, `POC_PLAN.md`. Deterministic joins, lookups, and
arithmetic (taxonomy, confidence, assembly) never route through a model;
Haiku/Sonnet/Opus tiers are reserved for judgment-heavy extraction and review.

The deterministic spine is `src/taxonomy.py` (exact 396-leaf validation +
`grouped_block()` for prompt injection), `src/schemas.py` (LLM candidate
contract), `src/confidence.py` (quote/table/visual evidence checks + rubric
scoring, incl. per-source read-quality floors: PDF chars/page, HTML snapshot
length, and a scrambled-page prose fallback that degrades to the key-token
check with a confidence cap + review flag), `src/assemble.py`
(`output.csv`/`failures.csv`/`manifest.md`), `src/ingest.py` (thin
source-CSV loading via the optional `local_file` column, snapshots, chunk
boundaries, visual-heavy detection,
deterministic scrambled-page (column-interleave) detection, Playwright
print-to-PDF capture of visual-heavy HTML),
and `src/llm.py` (swappable headless `claude -p` / `codex exec`, with
`{{name}}` template-var injection). Output rows are the 10 workbook columns
plus `confidence`, `band`, `review_flag`, `basis`, `checker_strength`, and
`call_language` (the effective, post-downgrade grade); one-hot columns are
intentionally omitted. `--sources` takes `pilot`, `target`, or a path to any
source CSV in the pilot column family — canonical firm/date/source/url +
optional `local_file`, with header aliases accepted (`Entity Name`/`Title`/
`External link` etc., see `ingest._COLUMN_ALIASES`), so a real-world export CSV
loads with no editing. Per row: a present-and-existing `local_file` ingests that
local PDF; a `.pdf` URL is downloaded and read as a PDF; any other URL takes the
HTML path. A second test set therefore needs zero code changes.
A `.claude/settings.json` hook blocks git commits
containing Claude/Anthropic self-attribution — commit messages stay plain.

The pipeline runs up to four LLM steps, each with explicit per-step
engine/model/effort flags (codex pinned to `gpt-5.5`; claude requires an
explicit model): analyze (per-chunk extraction via `prompts/analyze_chunk.md`:
injected taxonomy + conventions + brain examples + rolling `memory.md` +
native chunk), a second-reader checker (`prompts/check_candidates.md`, one
call per source, categorical verdicts plus `evidence_strength` feeding the
deterministic rubric — never a self-confidence number; any `fail` verdict
hard-fails the candidate, anything short of all-pass caps confidence at 74,
`thin` caps below High, and `adequate` deducts deterministically, so High means
a second model confirmed the evidence with enough force; default
codex/gpt-5.5/high), a conflict arbiter
(`prompts/arbitrate_conflict.md`, fires only on surviving view conflicts;
default codex/gpt-5.5/medium), and a group-notes resolver
(`prompts/resolve_groups.md`, only when `--group-notes` supplies analyst
free-text pairing notes; default codex/gpt-5.5/low). The normative house
rules live in `prompts/conventions.md`, injected into analyze, checker, and
arbiter alike; `brain.md` carries worked examples + reasoning style,
analyze-only. Both engine CLIs read PDF pages visually (codex renders pages
to PNG itself), so engine routing is unconstrained by source type. All
prompts are indexed in `prompts/REGISTRY.md`. Separate from the run pipeline,
`src/eval.py` is a standalone deterministic (no-LLM) harness that compares a
frozen run's `output.csv` against a held-back ground-truth CSV (firm+leaf join →
phase-1 buckets, recall/view-agreement, missed-call list, and a judgment
worksheet); it reads the run but never influences it. The pilot set
(`prev-excel/pilot.csv`) is now 7 docs / 2 grouped pairs (Schroders
review+outlook, JPM GFICC+GAA). Blind pilots are frozen in `runs/`
(gitignored; frozen runs are force-added when committed): `pilot-01`/`pilot-02`
(analyze only, original 5 sources — pilot-01's 12 false `quote_not_found`
failures drove the `evidence_kind: visual` tagging rule and print-to-PDF ingest
of visual-heavy HTML), `pilot-03` (first with checker + arbiter), `pilot-04`
(first with grouping), and `pilot-05` (first over all 7 docs / 2 groups, and
first with providers swapped: codex/gpt-5.5/high analyze, claude checker/
arbiter/grouper). `POC_PLAN.md` locks the 3-phase build order and LLM-native
ingestion design. A `.venv` holds `pdfplumber`, `pdfminer.six`,
`trafilatura`, `htmldate`, `playwright` (+ chromium). 144 unittests pass.

## Recent Changes

- 2026-07-06: Implemented the **post-test2-01 fix wave** from
  `runs/test2-01/gt-comparison.md`. **Task 1 (visual/dial evidence gate):**
  `src.confidence` now routes table/visual token misses on print-captured /
  visual-heavy pages to an explicit `visual_unverified_by_text` state instead
  of hard-failing on snapshot text; the checker input marks those candidates
  and includes the native PDF path; kept rows visibly note that the page image
  was checked rather than snapshot text; checker failures on this route carry a
  distinct message from the old token-miss failure. Clean-text visual/table
  token misses still hard-fail. **Task 2:** `conventions.md` and
  `check_candidates.md` now treat reduce / neutralize / dial back / scale back /
  pare language as a resulting-stance rule, not direction of travel; `brain.md`
  has synthetic examples for reduce-to-benchmark (`N`) vs trim-but-still-OW
  (`O`). Added an O-vs-U sibling-consistency tripwire in `src.assemble` for
  same source/page/evidence and same top-level asset class; it review-flags
  both rows and never fails or corrects them, and deliberately does not trigger
  on N-vs-U. **Task 3:** conventions/checker/brain now require two-sided
  rotation/diversification evidence to emit both the source-of-rotation caution
  and the beneficiary where supported. Prompt versions updated:
  `conventions.md` v1.2, `check_candidates.md` v1.6, `brain.md` v1.5, registry
  updated. **Task 4:** generated client `tmp/client-update/questions.html`
  gained dial-grid breadth and source-scope questions. **Task 5:**
  `tmp/gt-reconciliation-test2.md` drafted the TRP UK IG Credit GT correction
  and the six BlackRock not-grounded/source-scope rows; GT CSV untouched.
  **Task 1F artifact:** `runs/test2-01-rescored/` reconstructs the 23 frozen
  `evidence_check_failed` rows and preserves frozen output rows verbatim, with
  22 rescued / 1 duplicate-same-view assembly failure. Wellington `Japan
  Equities N` and the frozen `UK Duration N` UK-rates/gilts row are rescued.
  The artifact was first generated with an explicit
  `local_visual_review_fallback` (Claude CLI not logged in at the time), then
  regenerated the same day with the real `claude/opus/medium` checker once the
  CLI was authenticated: all 23 verdicts real, 0 hard fails, strength 22
  decisive / 1 adequate, identical outcome (22 rescued / 1 duplicate-same-view
  assembly failure) — opus visually confirmed every dial call the fallback had
  assumed. `checker-verdicts.json` now records `claude/opus/medium` on every
  entry. Tests:
  `.venv/bin/python -m unittest discover -s tests` → 201 pass. Note:
  `.venv/bin/python -m unittest` and discover without `-s tests` still report
  0 tests in this checkout, so use the explicit discovery command.
- 2026-07-06: **Materiality gate CLOSED** by user decision after going
  unexercised in three consecutive runs (pilot-06, test2-01 first attempt, and
  test2-01 final attempt: 0 `forecast_delta` candidates). The code and unit
  coverage remain live (`MATERIALITY_FLOOR_BP = 25`,
  `MATERIALITY_FLOOR_PCT = 2.0`, sub-floor hard fail, at/above-floor cap), but
  it is no longer a pending validation item. Revisit only if a forecast-delta
  source appears in the 37-source batch.
- 2026-07-06: Ran the **test2-01 GT comparison** (branch phase-3): deterministic
  `src.eval` join against `ground-truth/test2-ground-truth.csv` (89 rows / 5
  firms), then a judgment pass (five parallel per-firm agents verifying every
  non-exact worksheet row against the ingested `work/test2-01/` snapshots +
  source PDFs). Artifacts: `runs/test2-01/eval/` (`eval-report.md`,
  `eval-buckets.json`, filled `judgment-worksheet.csv`),
  `runs/test2-01/gt-judgments/*.judgment.json` (5), synthesis
  `runs/test2-01/gt-comparison.md`. **Raw recall 68/89 (76.4%)** — best raw of
  any run (pilot-06 65.9%); view-agreement 63/68 (92.6%); **quote spot check
  142/142 pass**. **Grounded-adjusted recall ≈ 75/83 (90.4%)** (add 7
  near_leaf_covered, drop 6 not_grounded GT-provenance rows). Misses (21):
  recall_gap 4 / near_leaf_covered 7 / not_grounded 6 / defensible_omission 4 —
  **only 4 genuine misses**, of which **2 are fixable** (Wellington Japan
  Equities N + UK Gilts N were emitted-but-evidence-gated) and **2 are one costly
  reading gap** (BlackRock IT/Tech U + US Mega-Cap U — the doc's central "diversify
  away from expensive mega-cap AI" caution, which the model took the opposite
  side of by emitting only bullish Asian-semi tech, unflagged). **Precision
  excellent: 1 overreach / 74 model_only** (66 sound_breadth + 7 near_leaf_of_gt
  + 1 overreach). Across all 142 kept rows the pass found **exactly 2 real
  defects, and they are the SAME class**: the 1 view reading error (Franklin EM
  Debt-Local Currency U vs N) and the 1 overreach (TRP EM Debt-Local Currency U)
  both map a *reduce/neutralize* dial to `U` instead of the resulting stance `N`
  — the pilot-05 trim→resulting-stance convention **not firing on
  "neutralize/reduce" verbs** (Franklin even read the same dial correctly as N
  for EM Debt-General but U for local-currency; unflagged at conf 75). Of the 5
  view disagreements: 1 genuine model error (that EM-Debt row), **1 GT error**
  (TRP UK IG Credit — both dials show Neutral, model's N right), 2 convention
  disputes, 1 model_correct (BlackRock Healthcare O) → model view calls
  defensible on 4/5 disagreements and 67/68 matched. **Both post-pilot-06 changes
  validated positively**: Change-2 country-granularity is recall-POSITIVE
  (BlackRock inferred Taiwan/South Korea Equities O + GSAM China Equities N all
  grounded in named-country prose, landing on named leaves alongside stated Asia
  Equities O — no snapping; 0/6 inferred rows hallucinated); Change-1
  call_language persisted on all rows, downgrade guard held. Per-firm true
  recall: Franklin 95.8%, TRP 91.7%, GSAM 88.9% (31/31 model_only sound — faithful
  p.12 grid enumeration, not overreach), Wellington 75% raw but 0 reading
  errors/0 overreach (all 4 misses near-leaf or evidence-gated), **BlackRock
  18.8% raw but true recall strong** — 10/16 GT rows reference bonds/credit/MyMap
  multi-asset material absent from the ingested equity-only PDF (GT authored from
  a fuller corpus, like pilot-05 PIMCO), all 13 model calls grounded.
  **Systemic fix list**: (1) evidence gate vs print-captured HTML dial grids —
  all 23 `evidence_check_failed` on the two print-to-PDF sources; cost 2
  Wellington misses; TRP only escaped because the grouped UK-view PDF carried the
  same dials in clean text (grouping doubling as ingest-robustness backstop) —
  **highest-value recall fix**; (2) EM-Debt reduce→U convention gap; (3)
  two-sided-prose caution scope (BlackRock mega-cap); (4) BlackRock GT source
  scope (analyst decision); (5) **materiality gate UNEXERCISED a 3rd time** (0
  forecast_delta); (6) GSAM horizon/conditionality flattening (marks risk-column
  hedges O — caveat, not defect). Verdict: strongest run yet on both recall and
  precision; defects are narrow and specific, none blind-protocol or
  extraction-integrity. `runs/` gitignored; not committed. GT itself has ≥1 error
  + 6 not-grounded rows to reconcile with the analyst.
- 2026-07-06: Ran the **test2 blind test set** (`prev-excel/test2/test2.csv`, 7
  new sources: BlackRock, GSAM, T. Rowe Price ×2, Wellington ×2, Franklin
  Templeton) through the pipeline as `test2-01` on `phase-3` — the behavioral
  validation of the two post-pilot-06 prompt/output changes (Change-1
  `call_language` output column, Change-2 country-granularity inference,
  `analyze_chunk.md` v1.6). Engines held at the pilot-06 config: analyze
  codex/gpt-5.5/high, checker claude/opus/medium, arbiter claude/sonnet/high,
  grouper claude/sonnet/medium, `--group-notes prev-excel/test2/group-notes.md`.
  Pre-flight clean: 191 tests pass; `--ingest-only` smoke confirms all 7 ingest
  with zero code edits (4 `.pdf` URLs downloaded — BlackRock 13p / GSAM 18p /
  TRP-UK 4p / Franklin 14p; 3 HTML URLs print-captured as visual-heavy —
  TRP-Monthly 6p / Wellington-Monthly 8p / Wellington-Quarterly 10p). **Grouping
  resolved both pairs with zero warnings** (group-1 TRP Monthly+UK, group-2
  Wellington Quarterly+Monthly) on both the killed and the surviving attempt.
  253 candidates → **142 kept / 111 failed**; count check pass; 0 chunk
  failures. Views O 88 / N 32 / U 22 (no kept UNCERTAIN this run). Bands High
  114 / Medium 28. Basis stated 136 / inferred 6 / **forecast_delta 0**
  (materiality gate UNEXERCISED a third time — no forecast_delta candidates
  emitted). Checker strength decisive 76 / adequate 58 / thin 8. Call language
  explicit_dial 83 / directional 39 / explicit_stance 14 / implied 6.
  Review-flagged 39. Failures: `duplicate_same_view` 62 (grouped-pair cross-doc
  dedups), `evidence_check_failed` 23, `arbitrated_out` 13 (sonnet arbiter on
  grouped-source view conflicts — GBP/Europe Equities/credit dial ties resolved
  by published-level/specific-beats-general/current-beats-conditional),
  `quote_not_found` 10 (prose on scrambled/visual pages), `duplicate_cross_leaf`
  3 (cross-leaf dedup LIVE). **Change-2 validated behaviorally**: BlackRock
  emitted `Taiwan Equities O` + `South Korea Equities O` as named-country
  `inferred` leaves (Medium + review) *alongside* the stated `Asia Equities O` —
  the intended multi-call pattern, no snapping to the regional aggregate; GSAM
  `China Equities N` (inferred) likewise. This is the direct fix for the
  pilot-06 Aberdeen "Asia Equities O" snapping. **Change-1 validated**:
  `call_language` persisted on all 142 rows, explicit_dial dominant (the four
  dial-grid sources), `implied` on exactly the 6 inferred rows; the
  explicit_dial→explicit_stance prose downgrade guard held (0 explicit_dial rows
  on prose). Inference-tier caps intact (all 6 inferred rows Medium + review).
  **New observation / recall risk**: all 23 `evidence_check_failed` land on the
  two print-to-PDF HTML sources (TRP-Monthly 16, Wellington-Quarterly 7) with
  "table/visual evidence tokens were not found in snapshot text" — the
  visual/table key-token gate rejects legitimate dial-grid calls (US Equities,
  UK Gilts, currencies, duration) when the print-captured snapshot text lacks
  the rendered tokens; a print-captured-HTML-grid recall gap worth a fix pass,
  flagged for human review. Operational note: the **first attempt was killed
  mid-analyze** (4/7 sources done) when the background-task wrapper was torn down
  and took its child python with it (`setsid` is absent on macOS); relaunched
  under `nohup` and it survived to completion. Frozen on disk at `runs/test2-01/`
  (`output.csv`/`failures.csv`/`manifest.md`); **not committed** (runs/
  gitignored). Blind protocol held — `ground-truth/test2-ground-truth.csv` never
  opened; GT comparison is the separate downstream `src.eval` + judgment step.
- 2026-07-06: Extended generic source intake to load a real-world export CSV
  as-is (`prev-excel/test2/test2.csv`, a 7-source second test set). Two additions
  to `src/ingest.py`. (1) **Header aliases**: `load_pilot_sources` maps a CSV's
  headers to canonical fields (firm/date/source/url/local_file) via
  `_COLUMN_ALIASES` — so `Entity Name`/`Title`/`External link` load with no
  editing; `firm`/`source`/`url` are required (a missing one raises, naming the
  headers seen), `date`/`local_file` optional. `load_target_sources` unchanged.
  (2) **Remote PDF download**: a `.pdf` URL with no `local_file` is now fetched
  (`_download_pdf`, injectable via `create_snapshot(downloader=...)`, filename
  from the URL path, `%PDF`-magic guard so an HTML error page returned for a
  `.pdf` URL fails loudly) and flows through the existing PDF path; the old
  "remote PDF fetch is not implemented" error is gone. Non-`.pdf` URLs still take
  the HTML path (and print-to-PDF if visual-heavy). Shared browser UA constant.
  Live ingest smoke on test2.csv: all 7 sources succeed — 4 PDF URLs downloaded
  and parsed (14/18/4/14p), 3 HTML URLs fetched (all visual-heavy →
  print-captured 6/10/8p). 6 new tests (aliases, missing-column error, remote
  download + filename + non-PDF-body guard); 191 pass. No LLM calls.
- 2026-07-06: Three post-pilot-06 changes on `phase-3`, gated on the pilot-06
  judgment pass (`runs/pilot-06/gt-comparison.md`). (1) **`call_language`
  persisted to output artifacts**: `output.csv`/`failures.csv` gain a
  `call_language` column carrying the EFFECTIVE grade actually scored (after the
  explicit_dial→explicit_stance downgrade on prose), and the manifest gains a
  call-language distribution. The downgrade logic is now a shared
  `confidence.effective_call_language(candidate)` helper used by both
  `score_candidate` (persisted on `ConfidenceResult.call_language`) and the
  assemble failure path; the existing `call_language_note` was already carried
  into commentary. (2) **Country-granularity inference (prompt-only)**:
  `analyze_chunk.md` v1.6 + `brain.md` v1.4 tell the inference tier to infer at
  the granularity the prose names — a named country with a taxonomy leaf lands
  on that country leaf (Taiwan Equities), not the regional aggregate (Asia
  Equities); regional only for genuinely regional prose or a country with no
  leaf; several named countries → one candidate each carrying the same spans
  (multi-call, kept by the existing cross-leaf dedup because the leaves are
  named). One synthetic Indonesia/Vietnam worked example (invented firm/prose,
  blind-safe). Not mirrored in the checker (its `asset_match` already polices
  subject identity); no schema change — behavioral validation is the upcoming
  blind test-set run. Fixes the pilot-06 Aberdeen "Asia Equities O" snapping.
  (3) **Generic source intake**: source CSVs gain an optional `local_file`
  column (repo-relative local PDF; present+exists → ingest with URL as
  metadata, present+missing → hard error naming the row, absent/empty → fetch
  URL). `_pilot_local_pdf_for`'s hardcoded firm/title→PDF mapping is DELETED and
  migrated into `prev-excel/pilot.csv`'s 7 rows (loads byte-identically — pinned
  by a regression test). `--sources` now accepts `pilot`, `target`, or a path to
  any pilot-format CSV (new `run.load_sources` helper; `enforce_source_limit`
  applies to all); `Target Ingestion List.csv` (no `local_file` column) loads
  unchanged. **This unblocks the second blind test set — a new CSV with
  `local_file` paths is now all that's needed, zero code edits.** 14 new tests;
  185 pass. No pilot re-run, no LLM calls.
- 2026-07-06: Ran the pilot-06 GT judgment pass (branch phase-3; five parallel
  per-firm judgment agents verifying every non-exact worksheet row against the
  ingested `work/pilot-06/` snapshots + `prev-excel/` PDFs; report
  `runs/pilot-06/gt-comparison.md`, row-level JSONs in
  `runs/pilot-06/gt-judgments/`, worksheet judgment/notes columns filled). **True
  recall 70/82 (85.4%)** = 54 exact + 16 near_leaf_covered, up from pilot-05's
  53/82 (65%); and because **0 GT rows are not-grounded this run** (down from 6),
  grounded-subset recall equals raw recall (pilot-05 needed a not-grounded
  discount to reach 70%). View agreement 49/53 decided exacts (92.5%), 64/69
  incl. near-leaf (92.8%). **Overreach collapsed 10→1** (JPM Gold/Precious O,
  0.9% of 106 rows, **review-flagged — flag hit-rate 1/1**); pilot-05's AB
  forecast-table micro-delta tail is entirely gone (AB emitted 10 rows, none a
  table micro-delta, and its country-macro inferences flipped from pilot-05
  misses to exact matches — the intended fix-wave outcome). All 4 exact view
  disagreements are convention disputes (0 reading errors, 4/4 flagged); the
  first kept UNCERTAIN (Aberdeen Oil) correctly abstained on GT's structurally
  disputable Oil U. Per-firm true recall: Schroders 28/28 (100%), PIMCO 13/15
  (86.7%, full 11p source resolves every pilot-05 grounding gap), AB 10/12
  (83.3%), JPM 10/13 (76.9%, all 25 GAA dial signs re-verified), **Aberdeen
  9/14 (64.3%)** the weak spot — country-granularity snapping into one "Asia
  Equities O" leaf (swallows Taiwan/Korea/Malaysia + AI-sector) + an EM-sovereign
  synonym split account for ~5 of its 10 "misses" (raw exact recall 28.6% badly
  understates true recall). Residual 12 true misses: 9 inference_depth + 3
  not_emitted (incl. Aberdeen Data Centers regression) + 0 not_grounded.
  Inferred-tier audit (17 rows, first live): all grounded, 0 hallucinated; 7 AB
  inferences are sound + recall-positive; the 2 genuine over-reads (JPM Gold,
  PIMCO Equities) were caught (capped ≤74 + flagged). Thin-tier audit (13 rows,
  first live): correct for hedged/soft rows but conflates hedged evidence with
  scrambled-page verbatim degradation on 3 stated PIMCO/JPM rows (rubric note).
  **Materiality gate UNEXERCISED again** (no forecast_delta candidates emitted).
  Quote spot check 106/106 pass. Verdict: run **supports closing Phase 2** on
  quality grounds; residual gaps are client convention/scope decisions (leaf
  granularity/synonymy now the biggest recall lever, inference scope), not
  extraction defects. `runs/` gitignored; the judgment-pass artifacts
  (`gt-judgments/`, filled `judgment-worksheet.csv`, `gt-comparison.md`) were
  force-added and committed (`04c9c43`), but the rest of the pilot-06 run
  (`output.csv`, `manifest.md`, `failures.csv`, `work/`, deterministic
  `eval-report.md`/`eval-buckets.json`) remains unfrozen.
- 2026-07-06: Ran the deterministic GT eval for pilot-06 (`src.eval`, no LLM
  calls) against `ground-truth/pilot-ground-truth.csv` (82 rows / 5 pilot firms);
  artifacts in `runs/pilot-06/eval/` (`eval-report.md`, `eval-buckets.json`,
  `judgment-worksheet.csv`). Raw leaf-match recall 54/82 (65.9%, flat vs
  pilot-05's 53/82); view-agreement among decided matches 49/53 (92.5%); 52
  model_only, 28 gt_only. Only 4 view disagreements and **all 4 sit on
  review-flagged rows** (AB China Equities U/N, AB EM Equities O/N, AB LatAm FI
  N/O, PIMCO Equities-General U/N). Quote-verbatim spot check **106/106 pass, 0
  fail**. Per-firm recall: Schroders 92.9% (26/28, all agree), AB 75%, PIMCO 60%
  (now properly grounded on the full 11p source — 9 matched/8 agree), JPM 46.2%
  (34 model_only from the grouped GFICC+GAA breadth), **Aberdeen 28.6%** the weak
  spot (10 misses, mostly country-equity leaves snapping to broad "Asia
  Equities"). eval is bookkeeping only — the judgment pass over
  `judgment-worksheet.csv` (defensible miss vs recall gap, overreach vs breadth,
  near-leaf snapping) is the separate downstream step, not yet done. Not
  committed.
- 2026-07-06: Ran pilot-06 blind — the validation run for the pilot-05 fix wave
  (basis field, materiality gate, cross-leaf dedup, convention tweaks, inference
  tier, Rubric v2). Engines held constant vs pilot-05: analyze codex/gpt-5.5/
  high, checker claude/opus/medium, arbiter claude/sonnet/high, grouper
  claude/sonnet/medium, `--group-notes`. Pre-flight clean (144 tests pass;
  PIMCO.pdf confirmed 11p). Single-source smoke first (JPM GFICC, codex/high): 5
  candidates, no repair retries, new schema round-trips — `call_language` carried
  `directional`/`implied` and `basis` carried `stated`/`inferred` (inference tier
  behaviorally live at smoke). Full run: both groups resolved with zero warnings;
  124 candidates → 106 kept, 18 failed; count check pass; 0 chunk failures.
  Failures: 14 `duplicate_same_view` (cross-doc dedups within the two groups), 1
  `arbitrated_out` (JPM Intermediate US Treasuries=N; sonnet arbiter applied
  current-beats-conditional), 1 `duplicate_cross_leaf` (JPM Global HY=N vs kept
  EM Debt-General — cross-leaf dedup LIVE), 1 `quote_not_found` (JPM Short-Dated
  Bonds=O, same scrambled-page family unrescued as pilot-05), 1
  `taxonomy_no_match` (PIMCO analyzer emitted non-leaf label "Currencies",
  deterministically rejected). Views O 44 / N 31 / U 30 / UNCERTAIN 1 (first
  kept UNCERTAIN — the hedged-risk→UNCERTAIN convention fix firing, Aberdeen Oil).
  Bands High 76 / Medium 30. Basis stated 89 / inferred 17 / forecast_delta 0.
  Checker strength decisive 68 / adequate 25 / thin 13. Review-flagged 31.
  Checklist outcomes: (2/3) **materiality gate UNEXERCISED** — zero
  `delta_below_materiality` failures AND zero `forecast_delta` kept rows: the
  analyzer emitted no forecast_delta candidates at all this run (codex classified
  AB's calls as broad `stated`/`inferred` country views; AB candidate count fell
  30→10 and the pilot-05 4–14bp micro-delta table rows the gate was built for did
  not recur), so gate + caps are code-live/unit-tested but not demonstrated
  behaviorally here — flagged for human. (4) inference tier LIVE: 17 inferred
  kept rows, all ≤74 + review, none violating. (5) cross-leaf dedup LIVE (1
  row). (6) Rubric v2 LIVE: `checker_strength` populated with a
  decisive/adequate/thin spread, all 13 thin rows ≤74 + review; call-language
  vocabulary confirmed live at smoke, but per-row `call_language` (incl.
  `explicit_dial` on JPM GAA) is NOT persisted to output.csv so cannot be
  verified from frozen artifacts — JPM GAA's band/checker profile (36 High,
  mostly decisive) is consistent with dial extraction. (7) PIMCO now ingests the
  full 11p Cyclical Outlook (was 2p): 23 candidates / 16 kept, up from
  pilot-05's 9 / 7. Shape vs pilot-05 (119 kept / O59-N31-U29-UNC0 / High114-
  Med5 / 5 review-flagged): the shift to more Medium and far more review flags
  is the expected consequence of the inference tier + Rubric v2 caps, not a
  regression. Frozen on disk at `runs/pilot-06/`; not committed. No fixes made
  and no ground-truth opened (blind). GT comparison happens separately via eval.

- 2026-07-06: Opened the **phase-3** branch and added `src/eval.py` — a
  standalone, fully deterministic (no-LLM) ground-truth comparison harness, run
  only after a run is frozen (`python -m src.eval --run runs/<id>
  --ground-truth <csv>`). It joins `output.csv` against the GT CSV on
  (normalized firm, sub-asset leaf) into the pilot-05 phase-1 buckets
  (`exact_match` split view-agree/disagree, `model_only`, `gt_only`), emits
  near-leaf suggestions for gt_only rows (same-firm agreeing-view rows on a
  different leaf, token-overlap ranked, >0 overlap only — never auto-matched),
  and reports raw recall, view-agreement (UNCERTAIN counted separately as
  abstain), per-firm math, the missed-call list, a review-flag hit analysis,
  and band/basis/checker_strength distributions (the last two shown only when
  the run's output carries them; pilot-05 predates them). A best-effort
  quote-verbatim spot check reconstructs each output row's evidence from its
  Full Commentary and re-verifies it via `confidence.evidence_passes` against
  the `work/<run-id>/` snapshots (evidence_kind inferred from commentary/locator
  text; skips cleanly when snapshots are absent). Writes
  `runs/<id>/eval/{eval-report.md, eval-buckets.json, judgment-worksheet.csv}`.
  **Pinned regression** reproduces the pilot-05 phase-1 counts exactly (82 GT,
  119 model, 44 exact = 40 agree/4 disagree, 75 model_only, 38 gt_only) and
  reconciles per-firm against the frozen `runs/pilot-05/gt-judgments/*.phase1.json`;
  spot check reports 119/119 pass. 27 new tests; 171 pass. Adds files only —
  no pipeline module touched.
- 2026-07-06: Shipped Rubric v2: graded categorical judgments now feed the
  deterministic confidence arithmetic while band semantics stay unchanged.
  Analyzer `call_language` widened to `explicit_dial` / `explicit_stance` /
  `directional` / `implied` / `none`; legacy `explicit` and `implied` frozen
  candidates still parse deterministically (`explicit` -> `explicit_stance`).
  `explicit_dial` is guarded to table/visual evidence and downgrades to
  `explicit_stance` on prose with a recorded note. Checker verdicts now carry
  `evidence_strength` (`decisive` / `adequate` / `thin`): `decisive` preserves
  all-pass behavior, `adequate` applies the tunable
  `CHECKER_ADEQUATE_DEDUCTION = 4`, and `thin` applies the tunable
  `CHECKER_THIN_CAP = 74` with review. Tunable call-language constants live in
  `CALL_LANGUAGE_POINTS`; band thresholds remain 75/50. Output/failure rows add
  `checker_strength` after `basis`, and manifests include a checker-strength
  distribution. Prompt versions bumped (`analyze_chunk.md` v1.5,
  `check_candidates.md` v1.4, `REGISTRY.md` updated). 17 new tests; 144 pass.
- 2026-07-06: Shipped the pilot-05 fix list (Tasks 1–4) — deterministic gates
  and a tagged inference tier, all behind a new required `basis` field on the
  candidate schema (`stated` | `forecast_delta` | `inferred`; old frozen
  candidates load as `stated`; `forecast_delta` also requires
  `delta_value`/`delta_unit`, rejected at parse time if missing). (1)
  **Materiality gate** (`src/confidence.py`): a `forecast_delta` move below the
  floor (`MATERIALITY_FLOOR_BP = 25`, `MATERIALITY_FLOOR_PCT = 2.0`, both
  provisional pending client answer) hard-fails to `delta_below_materiality`
  (reviewable, reversible — never converted to `N`); at/above the floor it is
  capped at 74 (below High) with a forced review flag, because delta-as-view is
  unconfirmed. (2) **Cross-leaf dedup** (`src/assemble.py`): same source doc +
  same view + identical normalized evidence spans clusters; within a cluster a
  leaf survives if it is *named* in the evidence (leaf-name/evidence token
  overlap, prefix-tolerant), so genuine multi-leaf sentences ("long NOK and
  AUD", "IT and communication services") all survive while unnamed fan-out
  leaves collapse to `duplicate_cross_leaf`; if no leaf is named, keep the
  highest-overlap leaf (tie-break: locked-taxonomy order). Known limitation:
  the trigger is *identical* evidence, so the AB global-duration triple
  (different table row per leaf) is out of scope — partially mitigated by the
  materiality gate. (3) **Convention tweaks** (`conventions.md` v1.1, mirrored
  in `check_candidates.md` v1.3, examples in `brain.md` v1.3): closing/trimming
  an overweight lands at the resulting stance (→ `N`/`O`, not `U`); a hedged
  risk note with no position taken → `UNCERTAIN`, not `U`. (4) **Inference
  tier** (`analyze_chunk.md` v1.4): the analyzer now SHOULD emit single-step
  analyst inferences (`basis: inferred`, verbatim spans, never overriding a
  stated call on the same leaf); `src/confidence.py` caps them at 74 (one band
  below stated) with a forced review flag; the checker verifies each is a
  plausible single step. Output/`failures.csv` gain a `basis` column after the
  review fields and the manifest gains a kept-row basis breakdown. 32 new tests
  (schema, materiality gate, caps, four frozen cross-leaf clusters +
  no-named-leaf fallback, deterministic pilot-05 re-score of the AB overreach
  rows and JPM GAA dials); 127 pass. No LLM pilot re-run (separate blind step).
- 2026-07-06: User replaced `prev-excel/PIMCO.pdf` with the correct/full
  Cyclical Outlook source (the previous file was the 2-page infographic only).
  This makes the 5 PIMCO GT rows judged "not grounded in the ingested source"
  in the pilot-05 comparison in-scope for the next run, and largely resolves
  client question 5 (PIMCO source scope) in
  `runs/pilot-05/gt-comparison.md`. No re-run yet.
- 2026-07-06: Ran the pilot-05 GT comparison (deterministic firm+leaf join +
  five parallel per-firm judgment agents verifying against the ingested
  sources; report `runs/pilot-05/gt-comparison.md`, row-level JSONs in
  `runs/pilot-05/gt-judgments/`). Recall 53/82 (65%, from 51% in pilot-04;
  70% excluding 6 PIMCO GT rows not grounded in the ingested 2-pager —
  GT authored from the full Cyclical Outlook article). All 6 view
  disagreements judged convention disputes (mostly table-vs-prose), none a
  reading error; all 24 JPM GAA dial signs verified correct; Schroders
  28/28 again. Of 66 unmatched model rows: 38 defensible GT omissions, 18
  convention disputes, 10 overreaches (8 pass at 75/High because the
  forecast-delta convention has no materiality floor — AB 4-14bp deltas;
  the rubric's 5 review flags correctly caught the prose-soft rows).
  Dominant remaining miss cause is analyst-style inference depth (20/29),
  a scope question, not a bug. New client questions: delta materiality
  floor + prose-over-table precedence, inference-depth scope, dial level
  policy, PIMCO source scope.
- 2026-07-06: Ran pilot-05 blind — the provider-swap run over all 7 docs
  (analyze codex/gpt-5.5/high, checker claude/opus/medium, arbiter
  claude/sonnet/high, grouper claude/sonnet/medium, `--group-notes`). Single-
  chunk smoke first (JPM GFICC, codex/high): 5 candidates, first attempt, no
  repair — codex feeds the span-list schema natively. Grouper resolved both
  pairs with zero warnings. 131 candidates → 119 kept, 12 failed (10
  `duplicate_same_view` cross-doc dedups within the two groups, 1
  `checker_sign_mismatch`, 1 `quote_not_found`); count check pass, 0 chunk
  failures. Breadth up sharply vs pilot-04's 48 kept: Aberdeen now emits 4
  (was 0 — priority-1 recall gap closed), AB 30, Schroders multi-asset 38, JPM
  GAA 47 (the previously-uningested combined source), Schroders review-alone
  still 0 (correct). Views O 59 / N 31 / U 29; bands High 114 / Medium 5;
  5 review-flagged (all conf 74). Two non-dedup failures for analyst review:
  AB JPY=N killed by opus checker reading USD/JPY 155→145 as monotonic yen
  appreciation (→O, not two-sided-nets-to-N); JPM Short-Dated Bonds=O
  `quote_not_found` (scrambled-page family, not rescued this run). Frozen on
  disk at `runs/pilot-05/`; not yet committed.
- 2026-07-05: Closed the JPM combined-source scope gap from the GT evaluation:
  added a 7th pilot row ("Global Asset Allocation Views 2Q 2026", 4/30/2026,
  local PDF in `prev-excel/`), a group note pairing it with the GFICC doc
  (mirrors the Schroders pair), and title-disambiguated JPM entries in
  `_pilot_local_pdf_for` (firm-only mapping would have sent both JPM rows to
  `jp-morgan.pdf`). Pilot is now 7 docs / 2 groups. Tests updated (source
  limit 7, JPM pair mapping); 95 pass. The recorded MR URL ends in "/c"
  (likely truncated) — metadata only, the local PDF is ingested.
- 2026-07-05: First ground-truth evaluation completed. User authored
  `ground-truth/pilot-ground-truth.csv` (82 rows, all 5 pilot firms); a codex
  agent ran the hybrid comparison against `runs/pilot-04-rescored/` (results
  condensed in `runs/pilot-04-rescored/gt-comparison.md`). Direction accuracy
  is excellent: 42/50 model rows align with GT, zero opposite-sign errors,
  and the rubric's review flags caught the one overreach. Recall is the gap
  (42/82 raw): 14 misses from Aberdeen emitting zero candidates, 16 from
  table/infographic breadth (AB forecast table, PIMCO implication rows), 5
  from JPM extraction breadth, and 5 because GT pairs JPM's GFICC doc with
  "Global Asset Allocation Views 2Q 2026", which the pilot never ingested
  (Schroders-style combined source). The grouped Schroders pair scored
  28/28. Seven near-leaf disputes cluster on broad-vs-specific leaf snapping
  (needs a convention decision).
- 2026-07-05: Built `runs/pilot-04-rescored/` — the GT-comparison artifact for
  the pilot-04 review. Both pilot-04 `quote_not_found` failures were re-scored
  deterministically (no LLM calls; script + provenance README in the artifact)
  under the new gates and pass: AB Euro Govt Bonds `N` via multi-span (2 spans,
  clean p.7, strict verbatim path) and JPM Short-Dated US Treasuries `O` via
  the scrambled-page fallback (p.2), both at 74/Medium/review (checker verdicts
  were not persisted by the run, so the checker-unconfirmed cap applies; the
  README documents all reconstruction assumptions). The 48 frozen rows are
  preserved verbatim; rescued rows are inserted in their firm blocks;
  `failures.csv` is header-only.
- 2026-07-05: Ran the grouping live test blind (`pilot-04`, claude/opus/medium
  + checker/arbiter/grouper on codex/gpt-5.5) with `--group-notes
  prev-excel/group-notes.md`. The Schroders pair grouped with no warnings
  (review doc alone: 0 candidates; outlook doc: 32). 50 candidates → 48 kept,
  2 `quote_not_found`, 0 chunk failures, count check pass. Aberdeen emitted 0
  candidates. Frozen at `runs/pilot-04/`. The two failures were diagnosed as
  an honest elided quote (AB) and a column-interleaved text layer (JPM) —
  drove the multi-span and scrambled-page fixes below.
- 2026-07-05: Added deterministic scrambled-page detection to rescue prose
  calls on column-interleaved PDF pages (the JPM pilot-04 JPM
  `quote_not_found`: pdfplumber merges the two columns line-by-line, so no
  contiguous quote of the rendered page survives the verbatim check).
  `src/ingest.detect_scrambled_page` flags a page (1-indexed) when it has a
  near-empty full-body-height vertical gutter separating two populated
  columns — line length alone fails to separate them (AB's wide single-column
  pages run longer than JPM's two-column page). Flags land in `IngestedSource`,
  `ingest_meta.json`, and the run manifest source line. In `src/confidence.py`
  a prose call citing a flagged page falls back to the key-token overlap check
  (like table/visual), capping confidence at 74 (below High), forcing
  `review`, and recording the degradation in the output/failure row; a clean
  page still enforces verbatim, and a non-cited scrambled page is unaffected.
  Threaded `scrambled_pages` through `assemble_candidates`/`score_candidate`.
  Validated: flags JPM p.2 (+p.1/p.4), does NOT flag AB clean single-column
  pages (incl. p.7, whose failure is a genuine stitched quote that stays
  failed); only AB's real 3-col grid (p.3) and forecast table (p.10) flag.
  Deterministically re-scored the frozen JPM pilot-04 `quote_not_found`: now
  passes at confidence 74 / Medium / review / degraded. 11 new tests (real
  word-box fixtures from JPM p2 + AB p7); 94 pass. No pilot re-run.
- 2026-07-05: `evidence_quote` now accepts a list of verbatim spans (a lone
  string is still one span), so an honest elision — two real passages joined
  with "..." — no longer fails the prose quote gate. Schema
  (`src/schemas.py`) parses string-or-list into `evidence_spans`;
  `evidence_quote` became a property joining spans with " ... " (commentary,
  failures, checker/arbiter inputs unchanged). The prose gate
  (`src/confidence.py`) verifies each span verbatim individually and,
  deterministically for multi-span only, enforces max 3 spans, ≥4 meaningful
  tokens per span, and document order (blocks reversed stitching); ellipses
  are never parsed out of free text — only an explicit list splits. Prompts:
  `analyze_chunk.md` v1.3 emits span lists for elided prose (no more inline
  "..."), `check_candidates.md` v1.2 reads the ` ... `-joined spans as one
  body of evidence. Deterministically re-scored the frozen pilot-04 AB Euro
  Govt Bonds `quote_not_found` (split its recorded quote on the ellipsis into
  2 spans): now passes against the frozen snapshot; the JPM
  `quote_not_found` (no ellipsis, a separate ingestion issue) still fails,
  untouched. 6 new tests; 83 pass. No pilot re-run.
- 2026-07-05: Applied both pilot-03 diagnosability fixes (delegated to an
  Opus 4.8 subagent). (1) `failures.csv` now records `evidence_quote` (column
  after `evidence_kind`; empty for chunk-level failures) so `quote_not_found`
  rows are diagnosable at a glance. (2) `normalize_quote_text` canonicalizes
  typographic/extraction seams symmetrically on quote and snapshot: soft
  hyphen removed, all dash variants folded to `-`, and intra-word hyphens
  removed after whitespace collapse — so a hyphenated word consumed by a PDF
  line break ("AI-related" → "AIrelated") can no longer sink a correct quote,
  in either direction. Word content/order still must match exactly
  (paraphrase/stitch/reorder tests still fail). 6 new tests; 76 pass.

## Next / Open

- Freeze pending: `runs/pilot-05/` (and still `runs/pilot-04/` +
  `runs/pilot-04-rescored/`) are on disk but not yet committed — `runs/` is
  gitignored; freeze by force-add when the analyst review confirms.
- Pilot-05 fix list from the GT comparison (`runs/pilot-05/gt-comparison.md`):
  items (1)–(3) are **done** (2026-07-06, see Recent Changes). (1)
  materiality gate for forecast-delta evidence — DONE and CLOSED as
  unit-test-validated by 2026-07-06 decision after three unexercised runs; keep
  code-live and revisit only if a forecast-delta source appears in the
  37-source batch. (2) cross-leaf dedup — DONE, scoped to identical-evidence
  clusters with a named-leaf guard; the AB global-duration triple (different
  table row per leaf) is a documented out-of-scope limitation of the identical-
  evidence trigger. (3) convention fixes (close-an-overweight → N, hedged risk
  → UNCERTAIN) — DONE. Still **flagged pending client answers**:
  inference-depth scope — the `inferred` tier is built and segregated below
  stated calls, but whether analyst-style macro→allocation inference is in
  scope (client question 2) still bounds recall; dial main+sub level policy
  (question 3); leaf-snapping for the 9 near-leaf pairs (question 4). (PIMCO
  source scope resolved 2026-07-06.)
- Grouping client questions from pilot-04: which Date/URL a combined
  pipe-joined row should carry, and confirm the outlook-beats-review arbiter
  rule.
- Disputed ground-truth calls (7: BMO Cash/Quality/EM Debt/CAD prose-vs-dial,
  Barings Hedge Funds/EM Equities/TIPs) and possibly-missing rows (3: BMO
  Growth and Materials, Barings US Small Cap) were sent to the Markets Recon
  team as review notes (drafted 2026-07-04, kept outside the repo); update
  the GT CSV and, if conventions change, `prompts/brain.md` when they
  respond.
- Reconcile source count with client (user says 38, workbook CSV has 37) and
  pick an output date-format policy (see `POC_PLAN.md` open items).
- Open questions tracked in `CLAUDE.md` (View legend confirmation, ground-truth
  availability, page-number requirements, HTML source locators, confidence
  threshold).
