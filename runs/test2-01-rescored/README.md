# test2-01-rescored — visual-checker artifact

Purpose: targeted re-score of the 23 frozen `evidence_check_failed` candidates
from `runs/test2-01/failures.csv` after adding the visual-checker route for
print-captured / visual-heavy pages. `runs/test2-01/` remains frozen and
untouched.

## What was reconstructed

- Source rows: the 23 `evidence_check_failed` candidates only.
- Source files: existing snapshots and printed PDFs in `work/test2-01/`.
- Checker/review source: claude/opus/medium.
- Assembly/scoring: current `assemble_candidates` and `score_candidate` with
  `visual_pages` set from frozen `ingest_meta.json` (`printed_pdf` /
  `visual_heavy`).

## Result

- candidates rechecked: 23
- kept/rescued: 22
- failed after checker visual review or assembly: 1
- Wellington Japan Equities N rescued: yes
- Wellington UK rates/gilts Neutral rescued as frozen candidate `UK Duration N`: yes
- T. Rowe Price UK Gilts U rescued as submitted: yes

`output.csv` preserves all frozen `runs/test2-01/output.csv` rows verbatim and
appends the rescued rows from this targeted pass. `failures.csv` contains only
the targeted 23 candidates that still failed under the new route.

## Files

- `rescore.py` — provenance script.
- `checker-verdicts.json` — checker verdicts used for the targeted pass.
- `output.csv` — frozen output rows plus rescued visual rows.
- `failures.csv` — targeted failures remaining after visual review.
