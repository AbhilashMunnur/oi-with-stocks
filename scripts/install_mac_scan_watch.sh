#!/usr/bin/env bash
# Install a Mac LaunchAgent that pings GitHub near half-hour IST slots.
# Prefer cron-job.org (scripts/setup_external_cron.md) when this Mac sleeps.
# Do NOT use a every-5-minutes schedule — Telegram is once per 30-minute slot.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.oiwithstocks.scanwatch"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
SCRIPT="$ROOT/scripts/keep_scans_alive.sh"
GH_BIN="$(command -v gh || true)"

if [[ -z "$GH_BIN" ]]; then
  echo "Install GitHub CLI first: https://cli.github.com/"
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "Run: gh auth login"
  exit 1
fi

chmod +x "$SCRIPT"
mkdir -p "$HOME/Library/LaunchAgents"

PATH_VALUE="$(dirname "$GH_BIN"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${SCRIPT}</string>
  </array>
  <key>StartInterval</key>
  <integer>60</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>WorkingDirectory</key>
  <string>${ROOT}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>${PATH_VALUE}</string>
    <key>TZ</key>
    <string>Asia/Kolkata</string>
  </dict>
  <key>StandardOutPath</key>
  <string>${HOME}/Library/Logs/${LABEL}.log</string>
  <key>StandardErrorPath</key>
  <string>${HOME}/Library/Logs/${LABEL}.err</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/${LABEL}" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/${LABEL}" >/dev/null 2>&1 || true

echo "Installed ${LABEL}"
echo "  Plist:  $PLIST"
echo "  Ping:   checks every minute; only dispatches near :00/:15/:30/:45 IST"
echo "  Logs:   ~/Library/Logs/${LABEL}.log"
echo
echo "Mac must be awake during market hours. Prefer cron-job.org half-hour"
echo "jobs (scripts/setup_external_cron.md) when this Mac sleeps. Delete any"
echo "every-5-minutes cron that POSTs oi-scan — it re-sends Telegram."
echo
echo "Firing one ping now..."
"$SCRIPT"
