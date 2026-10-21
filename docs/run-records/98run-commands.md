# 98-batch commands — run these yourself, in order

_Prepared 2026-07-08 (v3: 97 rows — the wrong-year Vanguard "midyear market
outlook" row was REMOVED per Nikhil; one Vanguard row remains, on the local
2026 PDF). Source of truth: `client-runs/runs-07072026-98rows/Target
Ingestion List AI (with local_file).csv` (97 rows, 37 local files incl. 4
transcripts). Splits: `client-runs/runs-07072026-98rows/splits/
split-{1..10}.csv`, firm-whole, 9×10 + 1×7 rows._

## 0. Before launching

- Preflight state: full sweep `preflight/`, failed-link re-sweep
  `preflight-retry/`, round-2 confirmation `preflight-confirm/` — every
  remaining row is fetch-safe and content-verified as of 2026-07-08.
- Scout proposed 2 groups (fresh run on the 97-row list): Wellington "Bond
  Market Outlook" Credit + Rates (same series, subtitled parts) and RBC
  Wealth's four "Global Insight 2026 Midyear Outlook" regionals (one
  outlook, regional parts). If you accept them, use the split-1 and split-5
  commands as written below; to reject one, delete the two `--group-notes`/
  `--grouper-*` lines from that split's command.
- Keep the Mac awake (`caffeinate -i` in another terminal, or Amphetamine).
- Run AT MOST 2 splits at the same time, launched a few minutes apart.
  Watch each log's first minutes for rate-limit errors:
  `tail -f client-runs/runs-07072026-98rows/<run-id>.log`
- A 10-row split takes roughly 1–1.5 h. When one finishes, start the next —
  any order is fine, they are fully independent.

## 1. The split runs

All ten use the same command shape — only the split number changes (three
places: `--sources`, `--run-id`, the log name). Splits 1 and 5 additionally
carry the group flags (RBC Wealth regionals; Wellington Credit+Rates).

### Split 1 — RBC Wealth ×7 (4 grouped regionals), Janus Henderson ×2, State Street (10 rows)

```bash
nohup .venv/bin/python -m src.run \
  --sources client-runs/runs-07072026-98rows/splits/split-1.csv \
  --run-id 98b-split1 \
  --out-root client-runs/runs-07072026-98rows \
  --engine codex --effort high \
  --checker-engine claude --checker-model opus --checker-effort medium \
  --arbiter-engine claude --arbiter-model sonnet --arbiter-effort high \
  --group-notes client-runs/runs-07072026-98rows/scout/group-notes.md \
  --grouper-engine claude --grouper-model sonnet --grouper-effort medium \
  > client-runs/runs-07072026-98rows/98b-split1.log 2>&1 &
```

### Split 5 — State Street IM ×5, Wellington ×5 (Credit+Rates grouped) (10 rows)

```bash
nohup .venv/bin/python -m src.run \
  --sources client-runs/runs-07072026-98rows/splits/split-5.csv \
  --run-id 98b-split5 \
  --out-root client-runs/runs-07072026-98rows \
  --engine codex --effort high \
  --checker-engine claude --checker-model opus --checker-effort medium \
  --arbiter-engine claude --arbiter-model sonnet --arbiter-effort high \
  --group-notes client-runs/runs-07072026-98rows/scout/group-notes.md \
  --grouper-engine claude --grouper-model sonnet --grouper-effort medium \
  > client-runs/runs-07072026-98rows/98b-split5.log 2>&1 &
```

### All other splits — swap the number N into the same command (no group flags)

```bash
nohup .venv/bin/python -m src.run \
  --sources client-runs/runs-07072026-98rows/splits/split-N.csv \
  --run-id 98b-splitN \
  --out-root client-runs/runs-07072026-98rows \
  --engine codex --effort high \
  --checker-engine claude --checker-model opus --checker-effort medium \
  --arbiter-engine claude --arbiter-model sonnet --arbiter-effort high \
  > client-runs/runs-07072026-98rows/98b-splitN.log 2>&1 &
```

Split contents (all firm-whole):

- split-2: AllianceBernstein ×7, Impax ×2, Seviora
- split-3: Aberdeen ×7, Schroders, Vanguard, Wells Fargo
- split-4: Invesco ×6, Columbia Threadneedle ×3, Russell Investments
- split-6: RBC GAM ×5, PGIM ×3, PIMCO, T. Rowe Price
- split-7: Lazard, Lord Abbett, M&G, Man Group, Manulife, Merrill,
  MetLife IM, Morgan Stanley, Nuveen, OCBC
- split-8: Federated Hermes, Fidelity, Franklin Templeton, Guggenheim,
  HSBC, J.P. Morgan, JPM AM, Julius Baer, KKR, LSEG
- split-9: BofA Private Bank, BlackRock, CI Global, Capital Group,
  Carmignac, Charles Schwab, Citizens PW, Coutts, EFG, Eastspring
- split-10 (7 rows): AEW, Aegon, Allianz, Allspring, Amundi, Aon's,
  BNP Paribas AM

## 2. After each split — digest (1 LLM call per source)

```bash
.venv/bin/python -m src.summarize digest \
  --run client-runs/runs-07072026-98rows/98b-splitN \
  --out-dir client-runs/runs-07072026-98rows/digests/98b-splitN
```

## 3. After all ten — crosscheck across every split output (same command,
## outputs are 97 rows total)

```bash
.venv/bin/python -m src.crosscheck \
  --outputs client-runs/runs-07072026-98rows/98b-split1/output.csv \
            client-runs/runs-07072026-98rows/98b-split2/output.csv \
            client-runs/runs-07072026-98rows/98b-split3/output.csv \
            client-runs/runs-07072026-98rows/98b-split4/output.csv \
            client-runs/runs-07072026-98rows/98b-split5/output.csv \
            client-runs/runs-07072026-98rows/98b-split6/output.csv \
            client-runs/runs-07072026-98rows/98b-split7/output.csv \
            client-runs/runs-07072026-98rows/98b-split8/output.csv \
            client-runs/runs-07072026-98rows/98b-split9/output.csv \
            client-runs/runs-07072026-98rows/98b-split10/output.csv \
  --out-dir client-runs/runs-07072026-98rows/crosscheck
```

## 4. Deliverable assembly (firm pages + Word binder) — ask Claude when ready

After all splits + digests + crosscheck: `firmpages` (1 call per firm, ~55
firms) + `bind`. Firm pages read digests, not documents, so step 2 must be
done for every split first.

## Cost expectation (from the 3-source cost slice, 2026-07-07)

~2.7 analyze (codex) + 1 checker (opus) calls per source, ~8 min/source →
a 10-row split ≈ 1–1.5 h wall clock. Ten splits, 2 at a time ≈ 5–7 h total.
Authoritative dollars: OpenAI + Anthropic dashboards around each run window.

## Notes

- 4 sources are `.txt` transcripts (Merrill + OCBC split-7; LSEG split-8;
  BofA Private Bank split-9). First live pass for the txt path — give their
  rows an extra look in review.
- The Vanguard "midyear market outlook" row was REMOVED (2026-07-08): its
  link serves pre-2026 content. One Vanguard row remains (split-3), reading
  the local 2026 update PDF.
- If a split dies mid-run: check the log tail, then relaunch the same
  command (a relaunch redoes that split only).
