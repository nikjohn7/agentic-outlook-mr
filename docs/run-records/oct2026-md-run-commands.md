# October 2026 Markdown batch commands (16-pair evaluation intake, 2026-10-23)

_Batch folder: `client-runs/runs-23102026-16rows/`. Inputs: the client
engine's Markdown conversions under `client-inputs/2026-10-review/markdown/`
(PDFs alongside under `pdfs/`, not used by this baseline), hashed in
`intake-manifest.json`. Source list: `md-sources-15.csv`. That is 15 of the 16
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
the matrix default `codex / gpt-5.6-luna / medium` (Nikhil's choice, this run
only; the code default and `tests/test_model_matrix.py` are unchanged).

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

## Split 1 (first checkpoint)

`md-split1-sources.csv`: both Neuberger Berman documents, MetLife IM,
Franklin Templeton (24 chunks). Bare model matrix.

```bash
nohup .venv/bin/python -m src.run \
  --sources client-runs/runs-23102026-16rows/md-split1-sources.csv \
  --run-id oct26md-split1 \
  --out-root client-runs/runs-23102026-16rows \
  > client-runs/runs-23102026-16rows/oct26md-split1.log 2>&1 &
```

Check before launching the remaining 11: ingestion, evidence validation, the
client spreadsheet, and the shared-evidence gate's dropped rows and survivor
notes (its first live run).
