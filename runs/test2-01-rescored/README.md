# test2-01-rescored — visual-checker artifact

Purpose: targeted re-score of the 23 frozen `evidence_check_failed`
candidates from `runs/test2-01/failures.csv` after adding the visual-checker
route for print-captured / visual-heavy pages. `runs/test2-01/` remains frozen
and untouched.

## What was reconstructed

- Source rows: the 23 `evidence_check_failed` candidates only.
- Source files: existing snapshots and printed PDFs in `work/test2-01/`.
- Checker/review source: claude/opus/medium (verdicts REPLAYED from checker-verdicts.json, not re-run).
- Assembly/scoring: current `assemble_candidates` and `score_candidate` with
  `visual_pages` set from frozen `ingest_meta.json` (`printed_pdf` /
  `visual_heavy`).

## Collision policy (frozen-wins)

The rescued dial/graphic candidates re-emit leaves the frozen run already kept
from each firm's paired document. Every rescued row is joined against the frozen
kept rows on the SAME key `src/eval.py` uses — `normalize_firm(Firm)` + stripped
`Sub-Asset Class` leaf. On a collision the frozen clean-text row wins (the
outcome a single assembly pass with group-level dedup would have produced), and
the rescued row is recorded in `failures.csv` instead of being appended:

- `duplicate_same_view` — the rescued view agrees with the frozen row.
- `duplicate_conflicting_view` — the rescued view disagrees (frozen O, rescued
  reduced to N): the known reduce/neutralize dial-read defect, kept out of
  `output.csv` and flagged distinctly so an analyst can find all such pairs.

Only rescued rows on genuinely new leaves are appended. This leaves `output.csv`
free of duplicate join keys, so `src.eval` consumes the artifact directly.

## Result

- candidates rechecked: 23
- verified by checker + assembly: 22
- net-new leaves kept (appended to the frozen output): 3
- recorded as duplicates (frozen-wins): 19
  (14 same-view + 5 conflicting-view)
- assembly failures preserved (pre-existing): 1
- `failures.csv` rows: 20
- `output.csv` rows: 145 (= 142 frozen + 3 net-new)

Net-new leaves appended: Asia ex-Japan Equities U, Japan Equities N, UK Duration N.

- Wellington Japan Equities N kept as net-new: yes
- Wellington UK Duration N kept as net-new: yes
- T. Rowe Price UK Gilts U: frozen row wins (rescued duplicate, same view): no

`output.csv` preserves all frozen `runs/test2-01/output.csv` rows verbatim and
appends only the net-new rescued leaves. `failures.csv` records the frozen-wins
duplicates (with the full story per row) plus the genuine assembly failures.

## Files

- `rescore.py` — provenance script (default replay mode; `--live` re-runs the checker).
- `checker-verdicts.json` — the 23 saved opus verdicts, replayed here.
- `output.csv` — frozen output rows plus net-new rescued leaves (145 rows).
- `failures.csv` — frozen-wins duplicates plus preserved assembly failures.
