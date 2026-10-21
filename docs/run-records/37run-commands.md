# 37-batch commands — run these yourself, in order

_Prepared 2026-07-07. Prerequisite for everything below: instruction set 6 has
landed (it adds `--out-root` to run.py and creates the preflight tool). The
split CSVs live at `client-runs/runs-07072026-37rows/splits/split-{1..4}.csv`
(generated from the workbook copy; REGENERATE from Kyle's final CSV when it
arrives — ask Claude, it's one command)._

## 1. Preflight (DONE for the workbook copy — `preflight-3/` is the current
## result: 37/37 OK after ingest hardening + JPM local_file wiring. Re-run
## only on Kyle's final CSV, pointing --sources at a local_file-wired copy)

```bash
.venv/bin/python -m src.preflight \
  --sources "excel-file/Target Ingestion List.csv" \
  --out-dir client-runs/runs-07072026-37rows/preflight
```

Review `client-runs/runs-07072026-37rows/preflight/preflight-report.md`:
FAILED links and `suspect` content checks must be resolved (or consciously
accepted) before the split runs.

## 2. Scout (re-run on Kyle's final CSV; ~1 cheap LLM call)

```bash
.venv/bin/python -m src.scout \
  --sources "excel-file/Target Ingestion List.csv" \
  --out client-runs/runs-07072026-37rows/scout/group-notes.md \
  --report client-runs/runs-07072026-37rows/scout/scout-report.md
```

Review the report. If it proposes 0 groups (expected), run the splits WITHOUT
`--group-notes`. If it proposes groups, add to the affected split's command:
`--group-notes client-runs/runs-07072026-37rows/scout/group-notes.md \
--grouper-engine claude --grouper-model sonnet --grouper-effort medium`

## 3. Cost slice — launch the agent with `docs/run-records/instructions-8-cost-slice.md` and review before any split runs

## 4. The four split runs

Engine config = the proven test2 config (analyze codex/gpt-5.5/high, checker
claude/opus/medium, arbiter claude/sonnet/high). Each writes to
`client-runs/runs-07072026-37rows/<run-id>/` with work (snapshots + PDFs)
under `client-runs/runs-07072026-37rows/work/<run-id>/`.

Before launching: keep the Mac awake (`caffeinate -i` in another terminal or
Amphetamine). Launch at most 2 at a time, a few minutes apart; watch the
first minutes of each log for rate-limit errors. Monitor with
`tail -f client-runs/runs-07072026-37rows/<run-id>.log`.

### Split 1 — Aberdeen ×7 + AEW, AllianceBernstein, CI Global AM (10 rows)

```bash
nohup .venv/bin/python -m src.run \
  --sources client-runs/runs-07072026-37rows/splits/split-1.csv \
  --run-id 37b-split1 \
  --out-root client-runs/runs-07072026-37rows \
  --engine codex --effort high \
  --checker-engine claude --checker-model opus --checker-effort medium \
  --arbiter-engine claude --arbiter-model sonnet --arbiter-effort high \
  > client-runs/runs-07072026-37rows/37b-split1.log 2>&1 &
```

### Split 2 — Invesco ×6 + Eastspring, Federated Hermes, Guggenheim (9 rows)

```bash
nohup .venv/bin/python -m src.run \
  --sources client-runs/runs-07072026-37rows/splits/split-2.csv \
  --run-id 37b-split2 \
  --out-root client-runs/runs-07072026-37rows \
  --engine codex --effort high \
  --checker-engine claude --checker-model opus --checker-effort medium \
  --arbiter-engine claude --arbiter-model sonnet --arbiter-effort high \
  > client-runs/runs-07072026-37rows/37b-split2.log 2>&1 &
```

### Split 3 — State Street ×5 + Columbia Threadneedle ×3 + JPM AM (9 rows)

```bash
nohup .venv/bin/python -m src.run \
  --sources client-runs/runs-07072026-37rows/splits/split-3.csv \
  --run-id 37b-split3 \
  --out-root client-runs/runs-07072026-37rows \
  --engine codex --effort high \
  --checker-engine claude --checker-model opus --checker-effort medium \
  --arbiter-engine claude --arbiter-model sonnet --arbiter-effort high \
  > client-runs/runs-07072026-37rows/37b-split3.log 2>&1 &
```

### Split 4 — Impax ×2 + PGIM ×2 + Janus Henderson, KKR, Lord Abbett, Manulife, T. Rowe Price (9 rows)

```bash
nohup .venv/bin/python -m src.run \
  --sources client-runs/runs-07072026-37rows/splits/split-4.csv \
  --run-id 37b-split4 \
  --out-root client-runs/runs-07072026-37rows \
  --engine codex --effort high \
  --checker-engine claude --checker-model opus --checker-effort medium \
  --arbiter-engine claude --arbiter-model sonnet --arbiter-effort high \
  > client-runs/runs-07072026-37rows/37b-split4.log 2>&1 &
```

## 5. After each split finishes — digests (1 LLM call per source)

```bash
.venv/bin/python -m src.summarize digest \
  --run client-runs/runs-07072026-37rows/37b-split1 \
  --out-dir client-runs/runs-07072026-37rows/digests/37b-split1
```

(repeat per split — swap `37b-split1` for the other run ids)

## 6. After all four — crosscheck across every split output

```bash
.venv/bin/python -m src.crosscheck \
  --outputs client-runs/runs-07072026-37rows/37b-split1/output.csv \
            client-runs/runs-07072026-37rows/37b-split2/output.csv \
            client-runs/runs-07072026-37rows/37b-split3/output.csv \
            client-runs/runs-07072026-37rows/37b-split4/output.csv \
  --out-dir client-runs/runs-07072026-37rows/crosscheck
```

Firm pages + the Word binder wait for the ~70-row batch (one combined
deliverable) unless Kyle wants an interim batch-1 doc — same commands
(`firmpages`, `bind`) work either way.
