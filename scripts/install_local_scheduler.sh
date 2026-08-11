#!/usr/bin/env bash
# Install a macOS LaunchAgent: weekdays 09:30, then every 30 minutes to 15:30.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.oiwithstocks.scan"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
RUNNER="$ROOT/scripts/run_scheduled_scan.sh"

chmod +x "$RUNNER" "$0"
mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/logs"

export ROOT LABEL PLIST RUNNER
python3 <<'PY'
import os
from pathlib import Path

root = Path(os.environ["ROOT"])
plist = Path(os.environ["PLIST"])
runner = Path(os.environ["RUNNER"])
label = os.environ["LABEL"]

slots = [(9, 30)]
for hour in range(10, 16):
    slots.append((hour, 0))
    slots.append((hour, 30))
slots = [(h, m) for h, m in slots if (h, m) <= (15, 30)]

# Unique, ordered.
seen, ordered = set(), []
for slot in slots:
    if slot not in seen:
        seen.add(slot)
        ordered.append(slot)

blocks = []
for weekday in range(1, 6):  # Mon–Fri
    for hour, minute in ordered:
        blocks.append(
            "\n".join(
                [
                    "    <dict>",
                    f"      <key>Weekday</key><integer>{weekday}</integer>",
                    f"      <key>Hour</key><integer>{hour}</integer>",
                    f"      <key>Minute</key><integer>{minute}</integer>",
                    "    </dict>",
                ]
            )
        )

plist.write_text(
    f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{runner}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{root}</string>
  <key>StartCalendarInterval</key>
  <array>
{chr(10).join(blocks)}
  </array>
  <key>RunAtLoad</key>
  <false/>
  <key>StandardOutPath</key>
  <string>{root / "logs" / "launchd.out.log"}</string>
  <key>StandardErrorPath</key>
  <string>{root / "logs" / "launchd.err.log"}</string>
</dict>
</plist>
"""
)
print(f"Wrote {plist}")
print("Daily slots:", ", ".join(f"{h:02d}:{m:02d}" for h, m in ordered))
PY

UID_NUM="$(id -u)"
launchctl bootout "gui/${UID_NUM}/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/${UID_NUM}" "$PLIST"
launchctl enable "gui/${UID_NUM}/${LABEL}" >/dev/null 2>&1 || true

echo
echo "Local scheduler active: $LABEL"
echo "Runs weekdays 09:30 → 15:30 every 30 minutes (Mac clock / IST check)."
echo "Logs: $ROOT/logs/scan.log"
echo "Status: launchctl print gui/${UID_NUM}/${LABEL} | head -20"
echo "Remove:  launchctl bootout gui/${UID_NUM}/${LABEL}"
