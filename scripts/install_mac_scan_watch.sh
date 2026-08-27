#!/usr/bin/env bash
# Install a Mac LaunchAgent that pings GitHub every 5 minutes during the
# NSE session. This is the morning kick GitHub's schedule does not provide.
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
  <integer>300</integer>
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
echo "  Ping:   every 5 minutes, 09:15–15:50 IST weekdays"
echo "  Logs:   ~/Library/Logs/${LABEL}.log"
echo
echo "Mac must be awake during market hours. For when it sleeps, keep"
echo "cron-job.org as in scripts/setup_external_cron.md"
echo
echo "Firing one ping now..."
"$SCRIPT"
