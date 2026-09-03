#!/usr/bin/env bash
# Configure reliable half-hour scans when GitHub's schedule goes quiet.
#
# What this does:
# 1) Stores your current `gh` token as repo secret SCAN_DISPATCH_TOKEN so the
#    workflow can self-chain to the next 09:30 / 10:00 / … / 15:30 IST slot.
# 2) Fires a test repository_dispatch (oi-halfhour-scan) so you can confirm Actions.
# 3) Prints optional cron-job.org settings as a second backup.
set -euo pipefail

REPO="${GITHUB_REPOSITORY:-AbhilashMunnur/oi-with-stocks}"

if ! command -v gh >/dev/null; then
  echo "Install GitHub CLI first: https://cli.github.com/"
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "Run: gh auth login"
  exit 1
fi

TOKEN="$(gh auth token)"
if [[ -z "$TOKEN" ]]; then
  echo "Could not read a GitHub token from gh."
  exit 1
fi

echo "Setting SCAN_DISPATCH_TOKEN on $REPO ..."
printf '%s' "$TOKEN" | gh secret set SCAN_DISPATCH_TOKEN --repo "$REPO"

echo "Triggering a test oi-halfhour-scan dispatch ..."
gh api --method POST "repos/$REPO/dispatches" -f event_type='oi-halfhour-scan'

cat <<EOF

Done. Self-chain is enabled for the rest of each market session once any scan starts.

Prefer scripts/provision_cronjob_org.py (half-hour IST slots only).
Do NOT create an every-5-minutes job — Telegram is once per 30-minute slot.

Watch runs: gh run list --workflow=scan.yml --limit 5
EOF
