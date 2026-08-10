#!/usr/bin/env bash
# Upload the credentials in .env to GitHub Actions secrets.
#
# Run this after setting up Telegram, or any time you rotate credentials.
# Values are piped through stdin so they never appear in a command line,
# your shell history, or the output.

set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "No .env file found. Run scripts/setup_angelone.py first."
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "GitHub CLI is not logged in. Run: gh auth login"
  exit 1
fi

uploaded=0
skipped=()

while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in ''|'#'*) continue;; esac

  key="${line%%=*}"
  value="${line#*=}"

  case "$key" in ANGEL_*|TELEGRAM_*) ;; *) continue;; esac

  if [ -z "$value" ]; then
    skipped+=("$key")
    continue
  fi

  printf '%s' "$value" | gh secret set "$key"
  uploaded=$((uploaded + 1))
done < .env

echo
echo "Uploaded $uploaded secret(s)."

if [ ${#skipped[@]} -gt 0 ]; then
  echo "Skipped (empty in .env): ${skipped[*]}"
fi

echo
gh secret list
