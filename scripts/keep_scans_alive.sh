#!/usr/bin/env bash
# Ping GitHub only near unpaid half-hour IST slots (not every 5 minutes).
# Telegram P&L is once per slot; slot guard no-ops if that half-hour is done.
set -euo pipefail

export TZ=Asia/Kolkata
HOUR="$(date +%H)"
MINUTE="$(date +%M)"
DOW="$(date +%u)" # 1=Mon … 7=Sun

# Weekends off. Session covers 09:30 open through 15:45.
if [[ "$DOW" -ge 6 ]]; then
  exit 0
fi
if [[ "$HOUR" -lt 9 || "$HOUR" -gt 15 ]]; then
  exit 0
fi
if [[ "$HOUR" -eq 9 && "$MINUTE" -lt 20 ]]; then
  exit 0
fi
if [[ "$HOUR" -eq 15 && "$MINUTE" -gt 50 ]]; then
  exit 0
fi

# Only fire within 2 minutes of a real scan slot (:00 / :15 / :30 / :45).
# A every-5-minute LaunchAgent or cron must not trigger Telegram between slots.
case "$MINUTE" in
  0|1|2|15|16|17|28|29|30|31|32|43|44|45|46|47) ;;
  *)
    echo "$(date '+%Y-%m-%d %H:%M:%S') not near a 30-minute slot — skip"
    exit 0
    ;;
esac

if ! command -v gh >/dev/null; then
  echo "gh not on PATH" >&2
  exit 1
fi

REPO="${GITHUB_REPOSITORY:-AbhilashMunnur/oi-with-stocks}"

# Same Angel login as a running scan. Piling on causes AB1021.
in_flight="$(gh run list --repo "$REPO" --workflow=scan.yml --status in_progress --json databaseId --jq 'length' 2>/dev/null || echo 0)"
if [[ "${in_flight:-0}" != "0" ]]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') scan already in progress — skip dispatch"
  exit 0
fi

gh api --method POST "repos/${REPO}/dispatches" -f event_type='oi-halfhour-scan'
echo "$(date '+%Y-%m-%d %H:%M:%S') dispatched oi-halfhour-scan"
