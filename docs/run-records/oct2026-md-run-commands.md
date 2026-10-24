# October 2026 Markdown batch commands (quality rerun plan, 2026-10-25)

## Current plan: quality revision v3

The quality-first instruction supersedes the earlier freeze-once plan. All
commands below the historical heading are retained as evidence only: do not
reuse their IDs, move folders, or run their combine/export/summary steps now.
The baseline `oct26md-split1/2/3`, trial and smoke artifacts remain preserved.

Validation evidence: [quality validation record](oct2026-quality-validation.md).
Launch helper: [oct2026-quality-v3-rerun.sh](oct2026-quality-v3-rerun.sh).
Read the acceptance decision in the record before launching. The helper checks
the validated source/prompt fingerprint, unused tmux sessions, output, log,
exit-status and work paths. This task does not launch full splits.

From `poc/`, after reviewing the validation evidence:

```bash
bash -n docs/run-records/oct2026-quality-v3-rerun.sh
bash docs/run-records/oct2026-quality-v3-rerun.sh preflight
# Two runs maximum; launch sequentially from this terminal:
bash docs/run-records/oct2026-quality-v3-rerun.sh launch 2
bash docs/run-records/oct2026-quality-v3-rerun.sh launch 3
bash docs/run-records/oct2026-quality-v3-rerun.sh status
# Wait for BOTH to finish successfully, then launch recommended split 1:
bash docs/run-records/oct2026-quality-v3-rerun.sh wait23 && \
  bash docs/run-records/oct2026-quality-v3-rerun.sh launch 1
bash docs/run-records/oct2026-quality-v3-rerun.sh status
```

Do not run these alongside focused model validations or unrelated batch runs.
Each session is named `oct26md-qv3-splitN`; output is
`client-runs/runs-23102026-16rows/oct26md-qv3-splitN/`, log is the adjacent
`.log`, work is `work/oct26md-qv3-splitN/`, Python exit is the adjacent `.exit`.
Worker commands use `caffeinate -i`, unbuffered Python, `tee`, and
`PIPESTATUS[0]`; a Python exit of zero alone does not prove extraction success.
The helper also requires a completed manifest, count reconciliation, zero chunk
failures, and CSV/XLSX/audit files. Inspect semantic failures and review flags
before acceptance. Empty logs while a model works do not imply failure.

Defaults remain analyze codex/gpt-6.1-sol/high; checker
claude/claude-opus-5-5/high; arbiter claude/claude-haiku-5-5/high; visual
codex/gpt-6-luna/high; shared-evidence claude/claude-opus-5-5/medium.
`--checker-context` is omitted (off), shared-evidence gate remains on, no group
notes or inventory overrides. Exact expanded worker command (N=2, 3, then 1):

The revised adapter disables lifecycle hooks only for its headless Codex
subprocesses (`-c features.hooks=false`), following the demonstrated Stop-hook
answer replacement in the validation record. Global configuration is untouched.

```bash
caffeinate -i .venv/bin/python -u -m src.run \
  --sources client-runs/runs-23102026-16rows/splits/split-N.csv \
  --run-id oct26md-qv3-splitN \
  --out-root client-runs/runs-23102026-16rows
```

URLs remain blank; only dates found in the source may appear. State Street
Sector Market Perspectives retains `06/10/2026`. No datefill. The L&G Q4 2024
edition remains excluded; the Q4 2026 ETF outlook remains included.

A future combine should consume only `oct26md-qv3-split1/output.csv`,
`oct26md-qv3-split2/output.csv`, `oct26md-qv3-split3/output.csv` and their
matching failures, after per-split review. It must not consume focused
validations, old splits, smoke or trial output. Keep any future combined,
reconciled or summary artifacts in new revision-specific destinations.
Retaining old split 1 is a user choice that creates a mixed configuration:
its old eligibility and echoed-source-ID defects remain, and the complete
batch cannot then claim validation under the revised configuration.
No combine, reconcile, final export, digest, firm page or binder is run here.

## Historical commands and results (superseded; do not execute)

_Batch folder: `client-runs/runs-23102026-16rows/`. Inputs: the client
engine's Markdown conversions under `client-inputs/2026-10-review/markdown/`
(PDFs alongside under `pdfs/`, paired through each split CSV's `pdf_file`
column), hashed in `intake-manifest.json`. Source list: `md-sources-15.csv`. That is 15 of the 16
pairs: the L&G "Q4 2024 Active Fixed Income Outlook: America decides" is
excluded as a two-year-old edition (decision 2026-10-23). Date and URL are
intentionally empty for every row (no URLs supplied; quarter-only dates are
not converted). Baseline code commit: `46b79dc` (branch
`markdown-batch-oct2026`)._

## Preflight (deterministic only)

```bash
.venv/bin/python -m src.preflight \
  --sources client-runs/runs-23102026-16rows/md-sources-15.csv \
  --out-dir client-runs/runs-23102026-16rows/preflight-nollm --no-llm
```

15 ok, 0 failed, all 15 typed `md`. The content-sanity LLM pass was skipped:
the client supplied the documents themselves, so there is no link to verify.

## Scout - MODEL OVERRIDE for this batch

**Off-matrix:** scout ran on `claude / claude-haiku-5-5 / high` instead of
the then matrix default `codex / gpt-5.6-luna / medium` (Nikhil's choice, this run
only). `gpt-5.6-luna` was the pre-revamp default when scout ran; after the
2026-10-23 model revamp the code default and `tests/test_model_matrix.py` use
`codex / gpt-6-luna / medium`.

```bash
.venv/bin/python -m src.scout \
  --sources client-runs/runs-23102026-16rows/md-sources-15.csv \
  --out client-runs/runs-23102026-16rows/scout/group-notes.md \
  --engine claude --model claude-haiku-5-5 --effort high
```

Result: 2 multi-source firms, **0 groups proposed**. Neuberger Berman (Equity
Outlook vs Asset Allocation Outlook) and State Street Investment Management
(Sector Market Perspectives vs EM debt outlook) are different series, so they
stay independent. No split takes `--group-notes`.

## Model matrix for this batch (revamped defaults, 2026-10-23)

Set as the code defaults before any split ran, so every command below is bare
(no model flags). Full IDs are pinned for every Claude stage.

| Stage | Engine / model / effort |
|---|---|
| analyze | codex / gpt-6.1-sol / high |
| tier-3 visual quote verifier | codex / gpt-6-luna / high |
| checker | claude / claude-opus-5-5 / high |
| conflict arbiter | claude / claude-haiku-5-5 / high |
| group-notes resolver (not used: no groups) | codex / gpt-6-luna / high |
| shared-evidence gate | claude / claude-opus-5-5 / medium |
| reconcile scope gate (+ near-leaf) | claude / claude-opus-5-5 / medium |
| datefill primary (dates stay empty this batch) | codex / gpt-6-luna / high |
| crosscheck | claude / claude-sonnet-5-5 / medium |
| summarize digest | claude / claude-sonnet-5-5 / medium |
| summarize firmpages | claude / claude-sonnet-5-5 / high |
| datefill cascade | claude / claude-sonnet-5-5 / medium |
| scout (code default; this batch's scout already ran on claude-haiku-5-5) | codex / gpt-6-luna / medium |
| preflight content check (skipped this batch: `--no-llm`) | claude / claude-sonnet-5-5 / low |

## Splits (3 x 5, firm-whole)

| Split | Sources | Chunks |
|---|---|---|
| `splits/split-1.csv` (first checkpoint) | Neuberger Berman x2, Allianz, Franklin Templeton, MetLife IM | 25 |
| `splits/split-2.csv` | NYL Investment Management, Amova, L&G, State Street Investment Management x2 | 25 |
| `splits/split-3.csv` | Lombard Odier, Columbia Threadneedle Investments, Robeco, Tikehau Capital, BlackRock | 37 |

One command per split, bare, in its own tmux session (`caffeinate -i` keeps
the Mac awake; `tee` keeps the log):

```bash
tmux new-session -d -s oct26md-splitN -c "$PWD"
tmux send-keys -t oct26md-splitN "caffeinate -i .venv/bin/python -m src.run \
  --sources client-runs/runs-23102026-16rows/splits/split-N.csv \
  --run-id oct26md-splitN --out-root client-runs/runs-23102026-16rows \
  2>&1 | tee client-runs/runs-23102026-16rows/oct26md-splitN.log" Enter
```

The page check now runs inside each run (code from `c9fb437`), so there is no
separate page-check step. The standalone `src.pagecheck` CLI (runbook 2.4) is
only for the split 1 trial and other runs made before that.

## Split 1 TRIAL (ran 2026-10-23 18:17-19:04, code `bc731ff`)

**This was a trial, not the deliverable.** It read the Markdown only (no PDF
pages); its output in `oct26md-split1/` and the page-checked copy in
`oct26md-split1/pagecheck/` are kept for comparison and are not combined. The
final run reruns split 1 with the other two splits (see Final run plan).

5 sources, 25 chunks, no chunk failures. 248 candidates -> 132 kept, 116
failed; the client file has 123 rows (9 same-view pairs across the two
Neuberger documents merged). Views O 59 / U 37 / N 35 / UNCERTAIN 1; bands
High 92 / Medium 40. Shared-evidence gate (first live run): 5 calls dropped,
each for a sound reason; every other shared passage kept as separate calls.

Review found, and fixed before the final run (code after `bc731ff`):
- Review flags were 48% (64 of 132 internal rows; 68 of 123 client rows).
  44 of the 64 flagged internal rows carried a shared-evidence multi-keep
  note (the gate kept several calls named in one passage) or a cross-document
  same-view merge note; many, but not all, had no other flag reason. Neither
  note is a judgment; both are now noted but not flagged (only a failed gate
  verdict, or a conflict picked by rule or left to a human, flags). Split 1
  after the change: 41 of 123 client rows (33%; 68 -> 41), mostly
  substantive checker objections and the sibling tripwire. The drop is
  smaller than 44 because some of those rows stay flagged for another reason.
- Merged rows did not split back into their passages when re-read (the CSV
  writer collapses the separator's double spaces); fixed in
  `src/commentary.py`.
- Re-exporting a run listed its merge rows twice in the failures file, and
  the same candidate proposed in several chunks appeared as identical rows;
  exact repeats are now listed once (67 -> 56 rows for split 1).
- The arbiter's failure label said "Two documents disagreed"; it settles a
  conflict within one document. Reworded.

Page check (`oct26md-split1/pagecheck/`): 188 quotes; 62 pages confirmed,
52 added where the model gave none, 0 corrected, 0 ambiguous, 74 not found
word for word (table and chart quotes; page removed, label kept). Client
rows with at least one verified page: 90 of 123. Every model-given page that
could be checked was right.

Open for later: the sibling tripwire flags complementary pairs (intermediate
O / long duration U; high quality O / low quality U).

## Final run plan (decided 2026-10-24)

The three splits are the FINAL run, made once, on the Markdown + PDF mode
(runbook 1.7): each document is read as its Markdown plus only the PDF pages
the inventory flags, PDF-read calls carry `(read from PDF)` in the Locator,
and the page check runs inside the run. URL stays empty. Date is filled only
where the document states a full date: today that is State Street's Sector
Market Perspectives (06/10/2026), which is kept. Firm summaries and the Word
binder are part of the deliverable.

**Release gate.** Step 1 (the smoke test) is the release gate. It passes when:

- the grid calls are recovered: the allocation-dot grids and change arrows on
  PDF p.4-7 produce calls, each checked against the rendered page;
- the PDF markers are correct: every `(read from PDF)` passage cites a
  flagged page (4, 5, 6, 7 or 9), no Markdown-read passage carries it, and
  `evidence_origin` agrees with the Locator;
- Markdown validation is intact: the Markdown-read calls of the trial's
  Asset Allocation Outlook rows (`oct26md-split1/output.csv`) still appear,
  and no Markdown quote fails because of the PDF chunks;
- merges look sensible: each `both` row's Markdown and PDF passages support
  the same view, and each view conflict between a PDF-read and a
  Markdown-read candidate (manifest count) was resolved the right way.

After it passes, the configuration (code commit, model matrix, inventory
thresholds) is frozen and steps 2 to 9 run once. If it fails, fix and repeat
step 1 only. The outputs become benchmark truth only after the client reviews
and accepts them.

All commands run from the repo root with:

```bash
B=client-runs/runs-23102026-16rows
```

### 1. Smoke test: Neuberger Asset Allocation Outlook

```bash
{ head -1 $B/splits/split-1.csv; grep '^Neuberger Berman,"Asset Allocation Outlook' $B/splits/split-1.csv; } \
  > $B/smoke-neuberger-aao.csv
.venv/bin/python -m src.pdfinventory --sources $B/smoke-neuberger-aao.csv --out $B/oct26md-smoke-aao-inventory
tmux new-session -d -s oct26md-smoke-aao -c "$PWD"
tmux send-keys -t oct26md-smoke-aao "caffeinate -i .venv/bin/python -m src.run \
  --sources $B/smoke-neuberger-aao.csv \
  --run-id oct26md-smoke-aao --out-root $B \
  2>&1 | tee $B/oct26md-smoke-aao.log" Enter
```

The inventory should list pages 4, 5, 6, 7, 9 (as in `$B/pdf-inventory/`).
Review `$B/oct26md-smoke-aao/`: `manifest.md` (PDF chunks and flagged pages,
`## Evidence origin (kept rows)`, `## Page check (paired PDFs)`),
`output.csv` (`Locator`, `evidence_origin`), `pagecheck-audit.csv`,
`failures.csv` and `output-client.csv`, against the gate above.

**Result (ran 2026-10-24 12:24-12:36, code `3e49267`): PASSED.** 109 candidates
-> 54 kept, 55 failed (46 duplicate_same_view, 5 checker_sign_mismatch,
2 arbitrated_out, 2 taxonomy_no_match); 54 client rows; High 47 / Medium 7;
6 rows flagged. Evidence origin: pdf 18, both 24, markdown 12.

- Grid calls recovered: the positioning grids on p.5, p.6 and p.7 gave 42
  PDF-read passages, 18 of them leaves the Markdown never yielded (the
  currency block, TIPS, munis, ABS/CMBS, IG corporates, India, LatAm, the EMD
  sub-leaves). Every PDF-read dot was checked against the rendered page: all
  agree, including the change arrows. p.4 is the summary grid (its leaves were
  cited from the detailed pages) and p.9 is a chart page with no allocation
  content; neither needs its own call.
- PDF markers: every `(read from PDF)` passage cites a flagged page, no
  Markdown passage carries it, `evidence_origin` agrees with the Locator on
  all 54 rows.
- Markdown validation: no quote failure of any kind. 32 of the trial's 35 calls
  on this document reappear with the same view. Duration - Long now lands on
  Long-Dated US Treasuries (same quote); Oil (a thin inferred call) was not
  proposed; TIPS changed U -> N (below).
- Merges and conflicts: the one PDF-vs-Markdown view conflict is TIPS. The
  grid reads At Target; the arbiter ruled the published level wins over the
  prose preference for five-year nominals (N, flagged for review). Correct.

Page check: 100 quotes; 42 pdf_read, 31 added, 27 not found word for word
(Markdown prose quotes; page removed, label kept).

After the gate: one export-only change (commit after `3e49267`): a merged
row's Locator cell no longer prints `Title: -` for a passage without a page;
it lists only the passages that have one (alignment on re-read comes from the
Full Commentary labels). It does not touch extraction, validation or calls, so
the gate's call-level checks stand and the final run uses that commit. The gate
did not exercise this Locator export: it sits outside the smoke-tested
configuration, is covered by unit tests only, and the final run's merged
Locator cells must be spot-checked at review.

### 2. The three splits

The tmux commands under "Splits" above, with N = 1, 2, 3 (run ids
`oct26md-split1` to `oct26md-split3`). At most two at a time. A run writes
into an existing run folder without warning, so move the trial first (done
2026-10-24: the trial is at `oct26md-split1-trial/`):

```bash
mv $B/oct26md-split1 $B/oct26md-split1-trial
mv $B/oct26md-split1.log $B/oct26md-split1-trial.log
mv $B/work/oct26md-split1 $B/work/oct26md-split1-trial
```

### 3. Combine

Concatenates the three splits (in split order) into `$B/oct26md-combined/`
(no model calls):

```bash
.venv/bin/python - "$B" <<'PY'
import sys
from pathlib import Path
from src.assemble import CLIENT_FAILURE_COLUMNS, FAILURE_COLUMNS, OUTPUT_COLUMNS
from src.csvio import read_csv, write_csv
batch = Path(sys.argv[1])
out = batch / "oct26md-combined"
for name, columns, target in (
    ("output.csv", OUTPUT_COLUMNS, "output.pre-reconcile.csv"),
    ("failures.csv", FAILURE_COLUMNS, "failures.csv"),
    ("failures-client.csv", CLIENT_FAILURE_COLUMNS, "failures-client.splits.csv"),
):
    rows = []
    for n in (1, 2, 3):
        path = batch / f"oct26md-split{n}" / name
        header, split_rows = read_csv(path)
        if tuple(header) != columns:
            raise SystemExit(f"{path}: unexpected columns")
        rows += split_rows
    write_csv(out / target, columns, rows)
    print(f"{target}: {len(rows)} rows")
PY
```

### 4. Skip datefill

No datefill this batch. Dates come only from the documents' own text at
ingest; the one full date (State Street's Sector Market Perspectives,
06/10/2026) is already in the split outputs, and quarter-only dates stay
blank. Reconcile reads `output.pre-reconcile.csv` directly.

### 5. Reconcile

```bash
.venv/bin/python -m src.reconcile \
  --outputs $B/oct26md-combined/output.pre-reconcile.csv \
  --near-leaf \
  --out-dir $B/oct26md-combined
```

Writes the reconciled `output.csv`, the audits, `reconcile-summary.md` and
`reconcile-failures-client.csv`. Human gate: review the audits (superseded
rows, cross-view near-leaf merges, `needs_human` keys) before step 6.

Fold the reconcile failure rows into the client failures file (label-block
order, exact repeats once):

```bash
.venv/bin/python - "$B" <<'PY'
import sys
from pathlib import Path
from src.assemble import CLIENT_FAILURE_COLUMNS
from src.csvio import read_csv, write_csv
from src.export import merge_failure_rows
out = Path(sys.argv[1]) / "oct26md-combined"
rows = merge_failure_rows(
    read_csv(out / "failures-client.splits.csv")[1],
    read_csv(out / "reconcile-failures-client.csv")[1],
)
write_csv(out / "failures-client.csv", CLIENT_FAILURE_COLUMNS, rows)
print(f"failures-client.csv: {len(rows)} rows")
PY
```

### 6. Final export

```bash
.venv/bin/python -m src.export \
  --output $B/oct26md-combined/output.csv \
  --out-dir $B/oct26md-combined \
  --failures $B/oct26md-combined/failures-client.csv
```

Rewrites `output-client.csv/.xlsx` and `failures-client.csv/.xlsx` (shared-key
losers folded in) and writes `export-report.md`.

### 7. Digest

One call per source, into one flat folder (firmpages reads it):

```bash
for N in 1 2 3; do
  .venv/bin/python -m src.summarize digest --run $B/oct26md-split$N --out-dir $B/digests
done
```

### 8. Firm pages

```bash
printf 'Firm,Sub-Asset Class,bucket,agent_verdict,note,needs_human\n' \
  > $B/oct26md-combined/firmpages-crosscheck-empty.csv
.venv/bin/python -m src.summarize firmpages \
  --digests $B/digests \
  --outputs $B/oct26md-combined/output.csv \
  --crosscheck $B/oct26md-combined/firmpages-crosscheck-empty.csv \
  --out-dir $B/firmpages
```

The header-only crosscheck is needed because Neuberger Berman and State Street
have two digests each; reconcile has already settled every firm and asset.

### 9. Bind

```bash
.venv/bin/python -m src.summarize bind \
  --pages $B/firmpages \
  --out $B/oct26md-combined/firm-summaries.docx
```

### Final check: URL and Date

URL must be blank on every client row, and Date blank except State Street's
Sector Market Perspectives (06/10/2026; a merged row may list it beside
another title). Prints PASS or FAIL:

```bash
.venv/bin/python - "$B/oct26md-combined/output-client.csv" <<'PY'
import sys
from collections import Counter
from src.csvio import read_csv
_, rows = read_csv(sys.argv[1])
with_url = [r for r in rows if r["URL"].strip()]
dated = Counter((r["Source"], r["Date"]) for r in rows if r["Date"].strip())
print(f"rows: {len(rows)}; with a URL: {len(with_url)}")
for (source, date), count in sorted(dated.items()):
    print(f"dated: {date} | {source} ({count} rows)")
expected = any(
    date == "06/10/2026" and "Sector Market Perspectives: Q4 2026" in source
    for source, date in dated
)
ok = not with_url and expected and all(
    date == "06/10/2026" and "Sector Market Perspectives: Q4 2026" in source
    for source, date in dated
)
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
PY
```

`failures-client.csv` has no URL or Date column.
