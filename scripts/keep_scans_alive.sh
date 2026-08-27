#!/usr/bin/env bash
# Ping GitHub to run the OI+RSI scan during the NSE session.
# GitHub's own schedule trigger is often skipped for a whole morning.
# Slot guard on the workflow no-ops in a few seconds if the half-hour is done.
set -euo pipefail

export TZ=Asia/Kolkata
HOUR="$(date +%H)"
MINUTE="$(date +%M)"
DOW="$(date +%u)" # 1=Mon … 7=Sun

# Weekends off. Session pings 09:15–15:50 IST (covers 09:30 open through 15:45).
if [[ "$DOW" -ge 6 ]]; then
  exit 0
fi
if [[ "$HOUR" -lt 9 || "$HOUR" -gt 15 ]]; then
  exit 0
fi
if [[ "$HOUR" -eq 9 && "$MINUTE" -lt 15 ]]; then
  exit 0
fi
if [[ "$HOUR" -eq 15 && "$MINUTE" -gt 50 ]]; then
  exit 0
fi

if ! command -v gh >/dev/null; then
  echo "gh not on PATH" >&2
  exit 1
fi

REPO="${GITHUB_REPOSITORY:-AbhilashMunnur/oi-with-stocks}"
gh api --method POST "repos/${REPO}/dispatches" -f event_type='oi-scan'
echo "$(date '+%Y-%m-%d %H:%M:%S') dispatched oi-scan"
