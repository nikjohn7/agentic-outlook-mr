# Markets Recon / Allocator Pro POC — State

_Last updated: 2026-07-15_

## Current State

POC for Markets Recon / Allocator Pro: ingest fund/asset-manager outlook sources
(PDF/HTML) and produce reviewable sub-asset-class allocation calls (`O`/`N`/`U`/
`UNCERTAIN`) with citations, against the locked taxonomy in `excel-file/Asset
Class List - Locked.csv`. Full spec: `CLAUDE.md`, `POC_CONTEXT.md`,
`WORKBOOK_SCHEMA.md`, `POC_PLAN.md`; the client's binding decisions and the
v1.2/v2 backlog live in `ROADMAP.md`. Deterministic joins, lookups, and
arithmetic (taxonomy, confidence, assembly) never route through a model; LLM
tiers are reserved for judgment-heavy extraction and review, and always return
categorical judgments, never numbers.

The deterministic spine: `src/taxonomy.py` (exact 396-leaf validation),
`src/schemas.py` (LLM candidate contract; `basis` stated/forecast_delta/
inferred; multi-span `evidence_quote`), `src/confidence.py` (tiered prose
quote gate: exact → normalized (NFKC, dehyphenate, drop glyph-only lines) →
bounded ordered-token subsequence (cap 74/review), recorded as `quote_match`;
table-visual key-token route; Rubric v2 scoring off the checker's categorical
`evidence_strength`, read-quality floors, and degraded-prose paths for
scrambled and OCR pages: key-token fallback, cap 74, forced review),
`src/assemble.py` (`output.csv`/`failures.csv`/`failures-client.csv`/
`manifest.md`; cross-leaf dedup; deterministic stated-beats-implied with
`implied_challenges_stated` logged; O-vs-U sibling tripwire;
`failures-client.csv` is grouped by client label and sorted
most-important-first — `CLIENT_FAILURE_LABELS` dict order is the canonical
importance order, internal `failures.csv` never re-ordered), `src/ingest.py`
(header-alias CSV loading with optional `local_file`, snapshots, chunking,
visual-heavy detection + Playwright print-to-PDF, scrambled-page detection,
retry-hardened fetches with browser fallback for blocked HTML — the browser
path retries too — per-page OCR for image-only PDFs, document-date
extraction; a per-source ingest failure becomes an `ingest_error` failure
row and the run continues), and `src/llm.py` (headless `claude -p` /
`codex exec`, `{{name}}` template vars; codex is no longer pinned to one
model — an allowlist `CODEX_MODELS = gpt-5.5 / gpt-5.6-sol / gpt-5.6-terra /
gpt-5.6-luna` with `DEFAULT_CODEX_MODEL = gpt-5.5`, a codex effort floor of
`low` (`minimal` never emitted; `CODEX_EFFORTS`), threaded through every codex
call site as a flag; defaults byte-identical). Dates are
document-only (client decision 11): the CSV date is discarded; HTML via
htmldate, PDF via a first-page worded-date scan; strict DD/MM/YYYY or blank;
`date_from` ∈ {html, pdf_text, ""}; PDF metadata never used (carries capture
dates). Output rows are the 10 workbook columns plus `confidence`, `band`,
`review_flag`, `basis`, `checker_strength`, `call_language`, `quote_match`;
grouped rows pipe-join Source/URL and non-blank Dates; one-hot columns
intentionally omitted. `output-guide.html` (repo root) explains the output
and failures-client files to the client (plain-language labels for every
internal reason_code, mapping test-enforced complete). A
`.claude/settings.json` hook blocks commits containing Claude/Anthropic
self-attribution — commit messages stay plain. Tests: `.venv/bin/python -m
unittest discover -s tests` (the `-s tests` is required); 429 pass (1 skip).

The run pipeline (`src/run.py`, ≤20 sources per run, `--out-root` to redirect
artifacts) runs up to five LLM steps with per-step engine/model/effort flags:
analyze (per-chunk, rolling `memory.md`, `prompts/analyze_chunk.md` +
`conventions.md` + `brain.md`), checker (`check_candidates.md`, one call per
source, sees the whole-file memory; any `fail` hard-fails, `thin` caps below
High), conflict arbiter (`arbitrate_conflict.md`), a group-notes resolver
(`resolve_groups.md`), and a tier-3 visual quote verifier
(`verify_quote_visual.md`) that fires only for
quotes failing the deterministic tiers: categorical present_verbatim /
present_paraphrase / absent, fail-closed, verbatim kept capped+review as
`quote_match: visual`, paraphrase dropped, absent dropped as
`quote_not_found_visual`; tier counts and invocations logged in manifest.md.
Both engine CLIs read PDF pages visually, so image content reaches the model
even when snapshot text is thin. The CODE DEFAULTS are the single source of
truth for which model runs which step — a bare invocation of any tool
resolves to the model matrix wired 2026-07-10 (see Recent Changes; regression
net `tests/test_model_matrix.py`). This SUPERSEDES the earlier "proven
production config" (pilot-05 onward: analyze codex/gpt-5.5/high, checker
claude/opus/medium, arbiter claude/sonnet/high, grouper claude/sonnet/medium;
validated live by `cost-slice-01` at ~8 min/source) — kept here as history.
Prompts are versioned in `prompts/REGISTRY.md`.

Standalone tools around the pipeline: `src/eval.py` (deterministic GT
comparison harness + judgment worksheet; never influences a run),
`src/scout.py` (layer 1 of grouping: pre-run metadata-only companion
proposals emitting a `--group-notes` file; conservative — same firm alone
never groups), `src/crosscheck.py` (layer 3 report tool, now SUPERSEDED as the
acting stage by `src/reconcile.py`; still runs as a bare same-firm (firm, leaf)
overlap report), `src/reconcile.py` (the v1.2 firm-reconcile stage: scope gate
→ merge same-view / precedence-ladder conflicting → reconciled `output.csv` +
`reconcile-audit.csv` + summary; `merged_by_reconcile`/`superseded_by_reconcile`
failure rows; consumed by the combine step),
`src/preflight.py` (fetch-only sweep of a source CSV, no run cap, one batched
content-sanity call, `preflight.csv` + report), `src/datefill.py` (post-run
document-date backfill: never edits a run in place — report/patch +
separate `--apply`; one date-hunt agent per undated source
(`prompts/find_date.md`) in a codex-low→claude-sonnet-low cascade; every
claim deterministically verified fail-closed (stated quote must reappear;
landing page must reference the doc; date parsed here and year-windowed
2025–2026); precedence stated-full → metadata → landing-full →
month-year `01/MM`, quarter/season never fills; PDF-metadata tier excludes
browser print-to-PDF captures by producer/creator signature; `--apply`
rebuilds grouped `Date` cells and dedupes the `15/06/2026 | ×4` cosmetic),
and `src/summarize.py`
(reader summaries: per-source `digest` (claude/claude-sonnet-5/high) →
deterministic reconcile → per-firm `firmpages` (claude/claude-sonnet-4-6/high,
no em dashes, v1.1) → `bind` to a
python-docx Word binder; sample approved by Nikhil, opus/medium is the
escalation if a page reads flat).

Run history (all blind; GT never opened during a run): pilots 01–06 over the
7-doc pilot set and `test2-01` over 7 new firms drove the hardening arc —
multi-span quotes, scrambled-page rescue, the visual/dial evidence route for
print-captured grids, Rubric v2, the inference tier, cross-leaf dedup, the
reduce/neutralize→resulting-stance convention, and country-granularity
inference. The materiality gate is live and first fired in production
98b-split1 (1 forecast_delta kept, 1 delta_below_materiality dropped).
Quality at close of phase 2: pilot-06 true
recall 85.4% (view agreement 92.5%, overreach 1/106); test2-01 raw recall
76.4% / grounded-adjusted ~90.4%, view agreement 92.6%, quote check 142/142,
precision 1 overreach / 74 model_only. GT sets: `ground-truth/pilot-*.csv`
(82 rows), `test2-ground-truth.csv` (89 rows; carries ≥1 known GT error + 6
not-grounded rows pending analyst reconciliation). Frozen in git:
`runs/test2-01`, `runs/test2-01-rescored`, `runs/pilot-05`, pilot-06
judgment artifacts; disk-only: `runs/pilot-04`(+`-rescored`); `work/` keeps
only `test2-01` (pilot work dirs deleted in the 194M→22M cleanup; the
pilot-05 eval spot-check skips without its snapshots). `runs/` and
`client-runs/` are gitignored; frozen artifacts are force-added.

Production batch state: the client's FINAL list arrived 2026-07-07
(`excel-file/Target Ingestion List AI.csv`, 98 rows; supersedes the earlier
37-row plan under `client-runs/runs-07072026-37rows/`) and was reduced to 97
rows (Vanguard "midyear market outlook" removed, wrong-year content). It
runs as TEN firm-whole splits (9×10 + 1×7) from the wired master
`client-runs/runs-07072026-98rows/Target Ingestion List AI (with
local_file).csv` (44 local files under `manual-sources/`), command sheet
`docs/run-records/98run-commands.md`; everything combines into ONE deliverable. Execution
COMPLETE: all ten splits ran (split 1 deliverable is `98b-split1-rescored/`;
split 8 needed an HSBC rerun, already merged into its current files — the
`.pre-hsbc-rerun` backups and `98b-split8-hsbc/` are superseded), and the
cross-run crosscheck is done (`crosscheck/`). The combined deliverable is
`98b-combined/` (built by `scripts/combine-98b.py`): 1729 kept calls across 55
firms + 758 failure rows; its `failures-client.csv` is importance-sorted.
Firm pages (56, firmpages stage codex/medium) + the deterministic python-docx
Word binder (`98b-combined/firm-summaries.docx`) were built off the
pre-reconcile combined output and rebuild once the reconcile promotion lands.
Referenced run records live under `docs/run-records/`; the 98-row combine
helper is `scripts/combine-98b.py`. Runs launch under `nohup` (a wrapper teardown killed a run once
on macOS), ≤2 parallel, staggered. `.venv` holds
pdfplumber, pdfminer.six, trafilatura, htmldate, playwright (+ chromium),
python-docx; Tesseract 5.5.2 + Poppler for OCR.

## Recent Changes

- 2026-07-15: Human review of the master-combined reconcile
  (`client-runs/master-combined-15072026/`, the cross-batch 98b+145b
  `--near-leaf` pass) — all 7 exact-pass and 15 near-leaf cross-view
  supersessions plus the 1 needs-human key reviewed against member-row
  commentary; PGIM Euro Govt Bonds and State Street Japan Duration verified
  against the live sources (PGIM p.13 score legend: "Modest tightening" is the
  4/5 POSITIVE excess-return rating — the O stands). 15 supersessions accepted;
  4 overridden: Carmignac Europe Equities → UNCERTAIN (both rows the same doc
  twice, evidence conditional, checker-unconfirmed O), AllianceBernstein
  Duration → U (explicit "Underweight duration but tap income" beats FI-team
  "holding duration"), Lion Global → Duration U (headline stance, conf 89;
  canonical flipped from Duration - Short O), State Street Japan Duration → U
  (source names Japan explicitly; the O inference is walked back in-document).
  Triodos/Oil needs-human resolved to U (named reference scenario = base-case
  declining path). Final: 3,630 output rows / 1,386 client failure rows.
  Rationale: `review-decisions.md` in the run dir; pre-review backups
  `*.pre-human-review.csv`; audit CSVs left as the unmodified machine record
  (summary carries a human-review addendum). Overrides applied with reconcile
  merge semantics preserved (max confidence, member-order Source/URL/Date
  joins, primary-first commentary, new primary's per-row fields, review kept).
- 2026-07-13: Digest-model A/B run over 2 split-7 docs (claude-sonnet-4-6 vs
  codex/gpt-5.6-luna/high; both verified against the source PDFs including
  chart-only data): both fully grounded, zero hallucinations; one minor
  deviation each (sonnet asserted 2 stances beyond the kept calls, luna
  softened one kept U to uncertain). Decision: digest models ALTERNATE by
  split parity from split 7 — odd splits claude/claude-sonnet-5/high (code
  default, bare command), even splits codex/gpt-5.6-luna/high — recorded in
  `AGENT-HANDOFF.md` and `docs/run-records/145run-commands.md`. Split-7
  digests were found incomplete (3/10 sources, not "done" as previously
  recorded here); the sonnet-4.6 originals are backed up at
  `client-runs/runs-13072026-145rows/digests-split7-sonnet46-backup/`, A/B
  artifacts at `digests-luna-ab/145b-split7/`.
- 2026-07-13: Reader-summary model pins were split by stage: `digest` now
  defaults to `claude/claude-sonnet-5/high`, while `firmpages` remains
  `claude/claude-sonnet-4-6/high`. The model-matrix regression test and prompt
  registry document the independent defaults; the full suite passes 429 tests
  with one skip.
- 2026-07-13: `docs/PIPELINE_RUNBOOK.md` written — the exact before/during/
  after pipeline process (setup + model matrix, CSV intake → preflight →
  scout → firm-whole splits, launch/monitor rules, per-source internals,
  combine → datefill → reconcile order, multi-batch combine, human review
  gates). Internal now; the client-facing "how to reproduce" doc derives
  from it. Multi-batch rule recorded there: cross-batch final = ONE
  `src.reconcile --near-leaf` pass over each batch's pre-reconcile DATED
  concatenation (multiple `--outputs`), never over already-reconciled files.
- 2026-07-11: Phase 3 near-leaf reconciliation built (ROADMAP v1.2 item 6 +
  the advisory portion of item 3; uncommitted on `phase-3`). Opt-in
  `src/reconcile.py --near-leaf` pass that runs AFTER the exact-leaf pass over
  its reconciled rows (61-key exact baseline preserved; off by default so the
  exact-only path is byte-identical). Deterministic candidate generation pairs a
  firm's related locked leaves by two bounded lanes — structural (same top-level
  asset class, Jaccard ≥0.50, plus token-subset or shared category) and
  short-label containment (a ≤2-token leaf whose tokens all appear in the other,
  e.g. `AI` ↔ `IT/Tech/Telecomms (inc. AI)`, Jaccard 0.2) — then builds per-firm
  connected-component clusters. A batched LLM (claude/opus/medium, INHERITED from
  the scope gate — no independent default; ≤8 clusters / ≤40 rows per call,
  `prompts/reconcile_nearleaf.md`) partitions each cluster's rows into collective
  `same_claim` calls (merged onto a canonical leaf chosen from the cluster's own
  locked labels) vs `distinct` calls (kept). Cross-view merges name the
  most-relevant surviving row (user decision: agent picks, not keep-separate);
  every near-leaf survivor is force-flagged review. All four taxonomy fields are
  rebuilt via `src/taxonomy.py`. Any contract violation (non-partition, canonical
  not in cluster, missing primary, malformed/failed batch) fails CLOSED to
  needs_human for the whole cluster. New reason codes `near_leaf_merged` /
  `near_leaf_superseded` (+ client labels), `reconcile-nearleaf-audit.csv`, and a
  standalone cross-firm `taxonomy-coverage-review.csv` (broad/specific volume as
  CONTEXT ONLY, never an auto-tiebreaker). Suite 408→429 (+20 test_reconcile, +1
  test_model_matrix). Real sibling run over `98b-combined/output.dated.csv` →
  `client-runs/runs-07072026-98rows/reconcile-near-leaf/` (NOT promoted): 1729 →
  1654 rows; exact pass 46 merged / 7 superseded (LLM-nondeterministic drift from
  the frozen 47/5), near-leaf 308 candidate pairs / 117 clusters → 16 same-view
  merged + 6 cross-view superseded across 20 acted clusters, 97 kept, 0
  needs_human; 0/1654 rows have mismatched taxonomy fields. The 6 cross-view
  supersessions (AllianceBernstein Duration + US Equities, Citizens US Duration,
  RBC GAM EM Equities) are the borderline broad/specific review items, all
  flagged. KKR US Treasuries↔Intermediate, RBC US Credit↔US IG, Amundi Euro Govt
  clusters were judged `distinct` (kept). `scripts/combine-98b.py` untouched.
- 2026-07-10: Checker evidence-context windows built (deterministic routing +
  visual fallback), behind opt-in `--checker-context` (OFF by default;
  uncommitted on `phase-3`). `src/confidence.evidence_context` routes each PROSE
  candidate from facts already recorded, no LLM routing: CLEAN (`quote_match`
  exact/normalized AND cited page not scrambled/OCR) → a text window of the
  quote's containing paragraph +/-1, hard-capped at
  `EVIDENCE_CONTEXT_CHAR_CAP=1200` around the primary span, attached as
  `evidence_context`; DEGRADED (subsequence match, or page scrambled/OCR) → NO
  text, `context_unreliable: true` + the cited page for the checker's existing
  visual route; fail-safe (non-prose, empty snapshot, quote not locatable, or no
  PDF page image) attaches nothing and the candidate is judged as today. Reuses
  the quote gate's own matching machinery (import, no reimplementation).
  `check_candidates.md` v1.8 adds the normative context section (context is
  CONTEXT NEVER EVIDENCE; may only push toward unclear/fail when the immediate
  surroundings hedge/condition/negate/re-attribute THIS quote, never over a
  different view — that stays the arbiter's/memory's territory;
  `context_unreliable` → open the page image). Wired in `run.py`
  (`_context_fields`/`_native_pdf_path`, recorded in run_config/manifest). Suite
  394→408 (routing unit tests: clean-exact/normalized, subsequence, scrambled,
  OCR, unlocatable, multi-span first-span window, non-prose, char-cap,
  grouped-member; + 3 run-wiring tests). A/B (blind, artifacts under gitignored
  `client-runs/checker-context/`, nothing promoted): two fresh 7-doc pilot runs
  on the current matrix, context OFF vs ON. OFF: recall 65.9% (54/82),
  view-agreement 94.2% (49/52), overreach 76 model_only, 130 kept. ON: recall
  67.1% (55/82), view-agreement 92.5% (49/53), overreach 72, 127 kept. Deltas sit
  within analyze-step nondeterminism (the two baselines here vs the frozen
  pilot-matrix-02 already differ ~1-2pt) and the checker HARD-FAILED nothing in
  EITHER run, so the full-pipeline A/B cannot isolate the feature (analyze runs
  first → candidate sets differ; candidates aren't serialized for a clean
  checker-only replay). A CONTROLLED micro-demo (identical candidate, only
  `checker_context` varies, real opus/medium) DOES isolate it: a quote "We would
  be overweight European equities" conditioned in-sentence on a scenario the
  house does not expect flips `supports_view` pass(decisive) → fail, note citing
  the neutral base case — the exact residual gap the feature targets, fired
  within its normative rules. Janus regression: the known-real Multi-Sector
  Credit score-grid calls are `explicit_dial` table/visual, which the prose-only
  feature never touches (proven no-op); the context run had 0 checker hard-fails,
  and the 2 loans calls absent under context dropped on the deterministic
  `evidence_check_failed` (empty visual evidence from analyze nondeterminism),
  not the checker. RECOMMENDATION: keep OPT-IN (off by default) — the default-on
  bar (view agreement/overreach improve without material recall cost) is not met
  (agreement dipped within noise); the feature is proven correct and safe (no
  recall cost, fail-safe, no-op on visual) but shows no net aggregate benefit
  under the current lenient checker (opus/medium hard-fails almost nothing),
  pending a cleaner isolated eval (freeze candidates, vary only the checker)
  before any promotion.
- 2026-07-10: Model revamp — the code defaults at every LLM call site became a
  new matrix (supersedes the pilot-05 "proven production config"; uncommitted
  on `phase-3`). Analyze codex/gpt-5.6-sol/high; checker claude/opus/medium;
  arbiter codex/gpt-5.6-luna/high; grouper claude/haiku/high (haiku+high
  intentional, CLI-verified); quote-visual codex/gpt-5.6-luna/high; scout
  codex/gpt-5.6-luna/medium (claude/haiku removed — never approved); preflight
  codex/gpt-5.6-luna/high; crosscheck claude/sonnet/medium; reconcile scope
  gate claude/opus/medium (and `scripts/combine-98b.py`; effort raised
  low→medium same day); datefill primary
  codex/gpt-5.6-luna/high + cascade claude/sonnet/medium; summarize digest
  claude/`claude-sonnet-5`/high and firmpages
  claude/`claude-sonnet-4-6`/high (separate pinned ids so client voice can't drift
  when the `sonnet` alias re-points; ids CLI-verified).
  `run.py` argparse extracted to `build_parser()`; all step defaults filled so
  `python -m src.run --run-id X --sources ...` is fully specified. Suite
  385→394 (`tests/test_model_matrix.py`: per-tool no-flags→matrix regression
  net). Validation (blind, artifacts under gitignored
  `client-runs/model-revamp/`, nothing promoted): (1) visual-verifier A/B on 18
  labeled cases — codex/gpt-5.6-luna/high 17/18 vs claude/sonnet/medium 9/18, 0
  vs 1 worst-class false-verbatim, 0 vs 5 malformed → default set to luna. (2)
  7-doc pilot (`pilot-matrix-02`) through `src.eval` vs the frozen pilot-06
  baseline on the same harness: raw recall 65.9%→67.1%, view-agreement
  92.5%→94.3%, misses 28→27, view-disagreements 4→3; model rows 106→128 (the
  +22 dominated by grounded stated JPM calls, not overreach — 39/44 stated
  basis). No material regression. (3) datefill throwaway rerun over
  `98b-combined/output.csv`: 43/50 filled vs the frozen 44/50 — the 37 metadata
  + 6 stated fills identical, only Morgan Stanley's one landing-page fill went
  fail-closed blank. A full judgment pass (like pilot-06's) would be needed to
  certify STATE-style "true recall / overreach" numbers.
- 2026-07-10: Phase 2 built (ROADMAP v1.2 item 1, marked built). Part A:
  `src/assemble.py` same-view dedup is now citation-preserving — the losing
  candidate's commentary folds into the kept row via the shared `"  ||||  "`
  labeled-segment convention (`<Source Title> (<locator>): <commentary>`,
  defined once, imported) instead of vanishing / a bare "Corroborated by"
  note; `duplicate_same_view` stays in internal `failures.csv` (note reworded)
  but is excluded from `failures-client.csv` (explicit exclusion, label kept).
  Part B: `src/reconcile.py` — standalone firm-reconcile stage that supersedes
  `crosscheck.py` as the acting tool (crosscheck untouched as a report). Groups
  on imported `src.eval` (firm, leaf); a batched categorical scope gate
  (`prompts/reconcile_scope.md`, claude/sonnet/medium) splits each multi-row
  key into `same_claim`/`distinct_claims` (fail-closed → needs_human); then
  deterministic code merges same-view claims (max confidence, pipe-joined
  Source/URL/Date, `||||` commentary) and resolves conflicting views by a
  precedence ladder (recency → basis stated>forecast_delta>inferred → band/
  confidence → needs_human keeps all rows flagged; never forced, never
  majority-vote). Outputs reconciled `output.csv` + `reconcile-audit.csv`
  (dual-confidence trail) + `reconcile-summary.md`; emits `merged_by_reconcile`
  / `superseded_by_reconcile` failure rows. `scripts/combine-98b.py` rewired:
  combine splits → date patch (`datefill.apply_patch`) → reconcile → final
  files, reconcile failures folded into the combined failure files. New
  `tests/test_reconcile.py` (19: scope gate, each precedence rule incl. undated
  recency-skip, tie→needs_human, distinct, merge) + assemble merge/label/sort
  tests; suite 358→378. `output-guide.html` gains the two new labels + a
  `||||` note. Real run over `98b-combined/output.dated.csv`
  (`reconcile/`, sibling pending review, NOT promoted into 98b-combined):
  1729→1677 rows; 61 keys matched the crosscheck anchor exactly (39 same-view/
  22 conflicting by view; scope gate 41 same_claim/20 distinct_claims); 47
  merged, 5 superseded (recency×2, confidence×1 across 3 keys — PGIM/RBC GAM/
  Wellington), 44 kept_distinct, 0 needs_human.
- 2026-07-10: Phase 1 built and run for real — `src/datefill.py` +
  `prompts/find_date.md` + `tests/test_datefill.py` (45 tests). Over
  `98b-combined/output.csv`: 50 undated sources, 44 filled (6 stated, 37 PDF
  metadata, 1 landing page), 6 fail-closed blank, 1 unmatched firm variant
  (Aon/"Aon's"). The print-capture guard mattered: 16 manual PDFs are browser
  Save-as-PDF whose CreationDate is a capture time — excluded by
  producer/creator signature. `--apply` wrote the sibling
  `98b-combined/output.dated.csv` (blank rows 1280→85). Client decision
  recorded in ROADMAP (decision-11 note): metadata publish dates allowed as
  post-run fallback, capture dates still excluded. Uncommitted on `phase-3`.

## Next / Open

- **Second client batch (145 rows) EXECUTING**: received as
  `excel-file/additional-data.csv` (Firm+URL only); wired master
  `client-runs/runs-13072026-145rows/additional-data (with local_file).csv` =
  140 rows (2 GlobalX exact dupes + 3 drops: Victory Capital wrong-year-2025,
  Angel Oak listing page, BNP Wealth cookie-wall HTML), slug/document-derived
  titles, 38 files in `manual-sources/` (Wells Fargo PDF extracted from a
  multipart capture). Preflight full/retry/confirm all clean. Four accepted
  groups in `scout/group-notes.md` (Insight monthly+quarterly, Apollo
  two-site, Coutts two-domain, Western Asset webcast summary+transcript;
  Apollo/Coutts twins retitled to avoid source_id collisions). 14 firm-whole
  10-row splits; command sheet `docs/run-records/145run-commands.md`. Splits
  1–8 COMPLETE on the bare model matrix (937 kept / 395 failure rows; groups
  resolved; no dead runs), digests 1–6 done (claude-sonnet-4-6); split-7
  (claude-sonnet-5) and split-8 (gpt-5.6-luna) digests launched 2026-07-13
  under the new parity alternation, in flight. Splits 9–14 pending via
  `AGENT-HANDOFF.md` (user runs:
  `claude "Follow the instructions in client-runs/runs-13072026-145rows/
  AGENT-HANDOFF.md exactly."`). Then per `docs/PIPELINE_RUNBOOK.md` §3–4:
  combine → datefill → reconcile, and the cross-batch final = ONE
  `reconcile --near-leaf` over `98b-combined/output.dated.csv` + batch 2's
  dated combined → ONE final output.csv + ONE failures-client.csv.
- **Phase 3 near-leaf output pending review** (uncommitted on `phase-3`,
  sibling not promoted): `client-runs/runs-07072026-98rows/reconcile-near-leaf/`
  holds the near-leaf reconciled `output.csv` + `reconcile-nearleaf-audit.csv` +
  `taxonomy-coverage-review.csv` + summary over `output.dated.csv`. Review the 6
  cross-view supersessions (AllianceBernstein Duration + US Equities, Citizens US
  Duration, RBC GAM EM Equities) and the 16 same-view merges (esp. the
  Equities-General→US Equities collapses and the AI↔IT/Tech normalizations), plus
  a sample of the 97 kept-separate clusters (incl. the KKR/RBC/Amundi named
  examples judged distinct). If accepted, wire `--near-leaf` into
  `scripts/combine-98b.py` and rebuild `98b-combined/` after the Phase 1/2
  promotion. Open policy Q: whether near-leaf survivors should stay force-flagged
  review beyond this first run.
- **Phase 1 date patch applied, pending review:**
  `98b-combined/output.dated.csv` is the date-filled sibling (frozen
  `output.csv` untouched). Review `client-runs/runs-07072026-98rows/datefill/`
  (`datefill.csv` + `datefill-summary.md`) — especially the 6 fail-closed
  blanks and Carmignac's ambiguous numeric — then promote `output.dated.csv`
  to `output.csv` if accepted, and re-run `scripts/combine-98b.py` /
  firm-pages off the dated file.
- **Phase 2 reconcile output pending review** (like the date patch, written as
  a sibling): `reconcile/` holds the reconciled `output.csv` + audit + summary
  over `98b-combined/output.dated.csv`. Review it (esp. the 5 superseded rows
  and the 20 distinct_claims keys), then PROMOTE by running
  `scripts/combine-98b.py` (combine → date patch → reconcile) to rebuild the
  final `98b-combined/` from the frozen splits. Not yet promoted — the frozen
  `98b-combined/output.csv` is still the pre-reconcile concatenation.
- **Deliverable remainder**: firm-page digests still running; when done,
  reconcile → firmpages → bind the Word binder, then review digests against
  `98b-combined/`. Re-run `scripts/combine-98b.py` if any split output changes.
- Combine-step code changes (assemble sort, tests, guide, combine script)
  are uncommitted on `phase-3`.
- **Model revamp pending review** (uncommitted on `phase-3`): the wired matrix,
  tests, REGISTRY, and validation artifacts (`client-runs/model-revamp/`:
  A/B results, `pilot-matrix-02`, `pilot-eval`, datefill-throwaway, 3
  firmpage samples). One judgment call flagged: grouper is `claude/haiku/high`
  per the matrix table + Note 3, though the build's item-1 text also said
  `sonnet` — flip if sonnet was intended. Nothing promoted; no client batch
  re-run yet.
- **Checker evidence-context pending review** (uncommitted on `phase-3`):
  `--checker-context` feature + `check_candidates.md` v1.8 + tests. Recommended
  OPT-IN (off by default); see the 2026-07-10 Recent Changes entry for the A/B
  numbers, the controlled micro-demo, and the Janus no-op result. Nikhil reviews
  before commit. If a definitive default-on/off call is wanted, the clean next
  step is an isolated eval: freeze one analyze output's candidates and run ONLY
  the checker twice (context off/on) so nondeterminism can't confound it (would
  need the pipeline to serialize candidates + a checker-replay mode). A/B
  artifacts under gitignored `client-runs/checker-context/` (both pilot runs +
  evals, the Janus regression pair, the controlled-demo script), nothing
  promoted.
- `runs/pilot-04` + `runs/pilot-04-rescored` remain disk-only (freeze
  pending user decision).
- GT reconciliation with the Markets Recon team: test2 GT has ≥1 error (TRP
  UK IG Credit) + 6 not-grounded rows (`docs/run-records/gt-reconciliation-test2.md`);
  earlier pilot GT disputes sent 2026-07-04, awaiting response.
- Still open: fuller analyst-reviewed ground-truth set; acceptable model
  providers. Deferred work is tracked in `ROADMAP.md` (v1.2/v2 backlog).
