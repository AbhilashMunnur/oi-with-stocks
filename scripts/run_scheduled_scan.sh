#!/usr/bin/env bash
# Run one scan when the IST market window is open (weekdays 09:30–15:45).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/scan.log"

{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') ====="

  OPEN="$(
    /usr/bin/python3 - <<'PY'
from datetime import datetime, time
try:
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
except Exception:
    # Fallback if zoneinfo data is missing; assume the Mac is on IST.
    now = datetime.now()

start, end = time(9, 30), time(15, 45)
open_ = now.weekday() < 5 and start <= now.time() <= end
print("yes" if open_ else "no")
print(f"IST {now:%Y-%m-%d %H:%M} weekday={now.weekday()} open={open_}", file=__import__("sys").stderr)
PY
  )"

  if [ "$OPEN" != "yes" ]; then
    echo "Outside scan window (weekdays 09:30–15:45 IST); skipping."
    exit 0
  fi

  if [ -x "$ROOT/.venv/bin/python" ]; then
    PYTHON="$ROOT/.venv/bin/python"
  else
    PYTHON="python3"
  fi

  "$PYTHON" -u main.py --once
} >>"$LOG" 2>&1
