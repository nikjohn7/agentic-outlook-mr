# Markets Recon / Allocator Pro POC — State

_Last updated: 2026-07-08_

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
`implied_challenges_stated` logged; O-vs-U sibling tripwire), `src/ingest.py`
(header-alias CSV loading with optional `local_file`, snapshots, chunking,
visual-heavy detection + Playwright print-to-PDF, scrambled-page detection,
retry-hardened fetches with browser fallback for blocked HTML — the browser
path retries too — per-page OCR for image-only PDFs, document-date
extraction; a per-source ingest failure becomes an `ingest_error` failure
row and the run continues), and `src/llm.py` (headless `claude -p` /
`codex exec`, `{{name}}` template vars). Dates are
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
unittest discover -s tests` (the `-s tests` is required); 311 pass (1 skip).

The run pipeline (`src/run.py`, ≤20 sources per run, `--out-root` to redirect
artifacts) runs up to five LLM steps with per-step engine/model/effort flags:
analyze (per-chunk, rolling `memory.md`, `prompts/analyze_chunk.md` +
`conventions.md` + `brain.md`), checker (`check_candidates.md`, one call per
source, sees the whole-file memory; any `fail` hard-fails, `thin` caps below
High), conflict arbiter (`arbitrate_conflict.md`), a group-notes resolver
(`resolve_groups.md`), and a tier-3 visual quote verifier
(`verify_quote_visual.md`, default claude/sonnet/medium) that fires only for
quotes failing the deterministic tiers: categorical present_verbatim /
present_paraphrase / absent, fail-closed, verbatim kept capped+review as
`quote_match: visual`, paraphrase dropped, absent dropped as
`quote_not_found_visual`; tier counts and invocations logged in manifest.md.
Both engine CLIs read PDF pages visually, so image content reaches the model
even when snapshot text is thin. The proven production config (pilot-05
onward): analyze codex/gpt-5.5/high, checker claude/opus/medium, arbiter
claude/sonnet/high, grouper claude/sonnet/medium; the cost slice
(`cost-slice-01`, 3 sources incl. Manulife) validated it live at ~8
min/source. Prompts are versioned in `prompts/REGISTRY.md`.

Standalone tools around the pipeline: `src/eval.py` (deterministic GT
comparison harness + judgment worksheet; never influences a run),
`src/scout.py` (layer 1 of grouping: pre-run metadata-only companion
proposals emitting a `--group-notes` file; conservative — same firm alone
never groups), `src/crosscheck.py` (layer 3: post-run same-firm (firm, leaf)
overlap report across any number of run outputs; same-view auto-marked, view
conflicts get one batched categorical pass, degrade to `needs_human`),
`src/preflight.py` (fetch-only sweep of a source CSV, no run cap, one batched
content-sanity call, `preflight.csv` + report), and `src/summarize.py`
(reader summaries: per-source `digest` → deterministic reconcile →
per-firm `firmpages` (claude/sonnet/high, no em dashes, v1.1) → `bind` to a
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
`tmp/98run-commands.md`; everything combines into ONE deliverable (combined
output CSV + crosscheck across all outputs + firm pages/binder at the
combine step). Execution: split 1 DONE (104 kept / 32 failed, ~4.2 h, RBC
Global Insight 4-way group applied; deliverable is `98b-split1-rescored/`,
110 rows); split 2 relaunch pending after its first attempt died on a
transient AB DNS error (fixed by phase-3 fault tolerance; 6 AB PDFs now
local); splits 3–10 queued. Runs launch under `nohup` (a wrapper teardown
killed a run once on macOS), ≤2 parallel, staggered. `.venv` holds
pdfplumber, pdfminer.six, trafilatura, htmldate, playwright (+ chromium),
python-docx; Tesseract 5.5.2 + Poppler for OCR.

## Recent Changes

- 2026-07-08: Splits 1–2 first launch + phase-3 validation. Split 1 completed
  (104 kept / 32 failed; materiality gate fired for the first time); split 2
  died mid-run on a transient AB DNS error — the defect phase-3 fixed. Review
  of split 1 found 8 Janus Henderson quote_not_found drops all with real
  evidence (glyph-artifact lines + two-column interleaving), driving the
  tiered quote gate; deliverable for split 1 is `98b-split1-rescored/` (110
  rows). A fresh smoke (`tmp/phase3-smoke2/`) then validated everything
  end-to-end live: ingest_error continuation on an unreachable source,
  quote-gate tier counts in manifest, subsequence + visual tiers fired
  (visual: 3 kept present_verbatim, 1 dropped fail-closed), stale-work-dir
  rerun re-ingests fresh with no stale reuse. The earlier "CLIs unusable"
  smoke blocker was an agent-sandbox artifact (Codex sandbox blocks the
  macOS Keychain that holds claude CLI auth) — both CLIs work from normal
  sessions. Verdict: GO for relaunching split 2 and running splits 3–10.
- 2026-07-08: Phase 3 hardening for the 98-row batch defects: AB split-2
  local PDFs wired; per-source ingest errors now continue the run with
  `ingest_error` failures/client labels/manifest entries; browser fallback
  fetches retry before failing; prose quote verification now records
  `quote_match` and uses exact -> normalized -> bounded subsequence tiers
  (subsequence cap 74/review); tier-3 visual quote verification added
  (claude/sonnet/medium default, categorical only, fail-closed, paraphrase
  dropped); `98b-split1-rescored` verifies all 8 Janus quote drops, appends 6
  new rows, and suppresses 2 duplicate same-source/same-leaf rows. Full suite:
  311 pass / 1 skip. (The build agent's own smoke stalled on CLI auth inside
  its sandbox; superseded by the passing smoke-2 above.)
- 2026-07-08: Aegon row updated: Kyle's replacement link is a direct PDF —
  downloaded through the ingest path (24p, real text layer, "July 2026
  Global fixed income mid-year outlook"), saved to `manual-sources/` and
  wired local (38 local files); splits regenerated, packing unchanged
  (Aegon in split-10), groups intact.
- 2026-07-08: Batch reduced to 97 rows: the Vanguard "midyear market
  outlook" row was REMOVED (its link serves pre-2026 content; Nikhil
  informing Kyle) — one Vanguard row remains on the local 2026 update PDF,
  so no Vanguard group. Splits repacked (9×10 + 1×7, firm-whole); scout
  re-run on the 97 list proposed 2 groups (Wellington Bond Credit+Rates,
  RBC Wealth Global Insight regionals ×4) — wired as removable flags on
  splits 1 and 5 in `tmp/98run-commands.md` (v3), Nikhil accepts/rejects at
  launch.
- 2026-07-08: 98-batch finalized for launch. BofA txt replaced with real
  Private Bank content (no longer the Merrill duplicate); the unresolved
  Vanguard "midyear market outlook" row wired to the same local update PDF
  per Nikhil (the split-1 group collapses the two Vanguard rows into one
  combined source — group-notes now REQUIRED on split-1). Splits repacked
  per Nikhil's preference: TEN splits of ≤10 rows (9×10 + 1×8), firm-whole;
  command sheet `tmp/98run-commands.md` rewritten (v2). All 98 rows
  fetch-safe; 38 local files.
- 2026-07-08: 98-batch manual round 2 wired: Allspring, Morgan Stanley,
  Nuveen, Carmignac, Schwab, RBC Wealth ×5 (PDFs), BofA Private Bank (txt —
  byte-identical to the Merrill transcript, both BofA CIO brands; flagged
  for Kyle), Vanguard update replaced by the 3-Jul-2026 PDF. Master now
  wires 37 local files; splits regenerated (20/20/20/19/19). Confirmation
  sweep (`preflight-confirm/`): 15/15 ok, 0 suspect. Sole unresolved row:
  Vanguard "midyear market outlook" URL serves wrong-year content (htmldate
  09/05/2023) — must be fixed or consciously dropped before split-3.
- 2026-07-08: 98-batch prep. **(1)** `.txt` transcript local_file support in
  ingest (video sources; text path, char-range locators, `date_from:
  txt_text`; unsupported extensions hard-error) — commit ab4598d, suite 291 →
  297. **(2)** `summarize._work_dir_for` now resolves an out-root run's
  sibling `work/` dir (cost-slice finding fixed; 85663ad, suite 298).
  **(3)** Wired master `client-runs/runs-07072026-98rows/Target Ingestion
  List AI (with local_file).csv` (26 local files: 12 carried + 11 Nikhil
  manual incl. 4 transcripts + Amundi/Seviora extension-less PDFs
  auto-downloaded + JPM cdn PDF), splits 20/20/20/19/19 firm-whole, command
  sheet `tmp/98run-commands.md`. **(4)** Scout over 98: 1 group proposed
  (Vanguard transcript + midyear outlook) — moot until the wrong-year
  content is fixed. **(5)** Preflight over 98: 88 ok / 10 failed; retry
  sweep proved AB ×6 + JPM transient-DNS (now ok). Kyle's CSV had the four
  Wellington URLs shuffled against titles — remapped in the wired master
  (original untouched). Confirmed-broken/blocked, awaiting Nikhil manual
  download: Allspring, Morgan Stanley, Nuveen, Carmignac, BofA Private
  Bank, Schwab, RBC Wealth ×5; both Vanguard pieces carry wrong-year
  content (URL serves July 2024; transcript reads as the 2025 video).

## Next / Open

- **97-row batch execution (in progress)**: relaunch split 2 (delete stale
  `work/98b-split2/` + `98b-split2.log` first for a clean slate), then splits
  3–10 from `tmp/98run-commands.md` (≤2 parallel, nohup, ~2–4 h each).
  Group-note flags: RBC ×4 applied on split 1; Wellington pair pending on
  split 5.
- One combined final deliverable across all batch outputs: combined CSV,
  crosscheck across every output.csv, firm pages + Word binder at the
  combine step. Use `98b-split1-rescored/output.csv` for split 1, not the
  original.
- Split-1 client cosmetics to decide before the combine: 30 undated Janus
  Henderson rows (docs carry no parseable worded date) and grouped rows
  pipe-joining identical dates (`15/06/2026 | ×4`) — dedupe would read
  better.
- `runs/pilot-04` + `runs/pilot-04-rescored` remain disk-only (freeze
  pending user decision).
- GT reconciliation with the Markets Recon team: test2 GT has ≥1 error (TRP
  UK IG Credit) + 6 not-grounded rows (`tmp/gt-reconciliation-test2.md`);
  earlier pilot GT disputes sent 2026-07-04, awaiting response.
- Still open: fuller analyst-reviewed ground-truth set; acceptable model
  providers. Deferred work is tracked in `ROADMAP.md` (v1.2/v2 backlog).
