#!/bin/bash
# Run from poc/. Full splits are launched only by an explicit `launch N`.
set -euo pipefail
B=client-runs/runs-23102026-16rows
F="$B/quality-v2-validation/implementation-currency-final-sha256.json"
SELF=docs/run-records/oct2026-quality-v3-rerun.sh
mode=${1:-status}
n=${2:-}
# Drift from the validated controls is reported, never blocking: every run's
# manifest records its own provenance (commit, uncommitted files, hashes).
warn_drift() {
  if [[ ! -f "$F" ]]; then
    echo "WARNING: validated fingerprint not found ($F); drift not checked." >&2
    return 0
  fi
  .venv/bin/python - "$F" <<'PY'
import json,sys
from pathlib import Path
from src.provenance import file_hashes
ref=json.loads(Path(sys.argv[1]).read_text()); now=file_hashes()
changed=sorted(n for n in ref if n in now and now[n]!=ref[n])
gone=sorted(n for n in ref if n not in now and not Path(n).is_file())
new=sorted(n for n in now if n not in ref)
if not (changed or gone or new):
    print('Implementation matches the validated controls.')
else:
    print('WARNING: implementation differs from the validated controls:')
    for label,names in (('changed',changed),('removed',gone),('new',new)):
        for n in names: print(f'  {label}: {n}')
    print('Drift does not block; each run manifest records its own provenance.')
PY
}
check_unused() {
  local r="oct26md-qv3-split$1"
  if tmux has-session -t "=$r" 2>/dev/null; then echo "Existing session: $r" >&2; return 1; fi
  local p
  for p in "$B/$r" "$B/work/$r" "$B/$r.log" "$B/$r.exit"; do
    if [[ -e "$p" || -L "$p" ]]; then echo "Existing target: $p" >&2; return 1; fi
  done
}
check_result() {
  local r="oct26md-qv3-split$1"
  if [[ ! -f "$B/$r.exit" ]] || [[ $(cat "$B/$r.exit") != 0 ]]; then
    echo "Not successfully completed: $r" >&2
    return 1
  fi
  .venv/bin/python - "$B/$r" <<'PY'
import sys
from pathlib import Path
from src.csvio import read_csv
p=Path(sys.argv[1]); m=(p/'manifest.md').read_text()
for line in ('- count check: pass','- chunk failures (no candidate): 0'):
    if line not in m: raise SystemExit('Incomplete/failed run: '+line)
for f in ('output.csv','output-client.csv','output-client.xlsx','failures.csv',
          'failures-client.csv','failures-client.xlsx','pagecheck-audit.csv'):
    if not (p/f).is_file(): raise SystemExit('Missing '+f)
print(p, 'COMPLETE',len(read_csv(p/'output.csv')[1]),'internal rows')
print(m)
PY
}
case "$mode" in
  preflight)
    warn_drift
    for i in 2 3 1; do check_unused "$i"; done
    .venv/bin/python -m src.run --help >/dev/null
    echo 'Unused sessions/targets checked; drift reported above. No runs launched.'
    ;;
  launch)
    [[ "$n" =~ ^[123]$ ]] || { echo 'Usage: launch 1|2|3' >&2; exit 2; }
    warn_drift
    check_unused "$n"
    if [[ "$n" == 1 ]]; then
      check_result 2
      check_result 3
    fi
    active=0
    for i in 1 2 3; do
      if tmux has-session -t "=oct26md-qv3-split$i" 2>/dev/null; then active=$((active+1)); fi
    done
    [[ $active -lt 2 ]] || { echo 'Two revised splits already active' >&2; exit 1; }
    tmux new-session -d -s "oct26md-qv3-split$n" -c "$PWD" "bash $SELF worker $n"
    ;;
  worker)
    [[ "$n" =~ ^[123]$ ]] || exit 2
    r="oct26md-qv3-split$n"
    set +e
    caffeinate -i .venv/bin/python -u -m src.run \
      --sources "$B/splits/split-$n.csv" --run-id "$r" --out-root "$B" \
      2>&1 | tee "$B/$r.log"
    codes=("${PIPESTATUS[@]}")
    # First is caffeinate/Python's exit, second is tee's exit.
    printf '%s\n' "${codes[0]}" > "$B/$r.exit"
    [[ ${codes[0]} == 0 && ${codes[1]} == 0 ]] || exit 1
    check_result "$n"
    ;;
  status)
    tmux list-sessions 2>/dev/null || true
    for i in 2 3 1; do
      r="oct26md-qv3-split$i"
      if [[ -f "$B/$r.exit" ]]; then check_result "$i" || exit 1
      elif [[ -f "$B/$r.log" ]]; then tail -n 5 "$B/$r.log"
      else echo "$r: not launched"; fi
    done
    ;;
  wait23)
    until [[ -f "$B/oct26md-qv3-split2.exit" && -f "$B/oct26md-qv3-split3.exit" ]]; do sleep 15; done
    check_result 2
    check_result 3
    # Launch separately after this command succeeds.
    ;;
  *) echo 'Usage: preflight | launch N | status | wait23' >&2; exit 2 ;;
esac
