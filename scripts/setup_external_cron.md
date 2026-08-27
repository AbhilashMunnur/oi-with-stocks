# Live scan kick (Mac does not need to stay awake)

GitHub’s own `schedule` trigger is **best-effort**. It skipped all of 09:30–11:30
IST on 27 Aug 2026. A missed slot is missed live paper (entries, stops,
scale-outs). It is not backfilled.

The Mac LaunchAgent is **optional extra** for when the laptop happens to be on.
**Production kick is a cloud ping**, not this Mac.

## What actually keeps the session alive

1. A cloud ping every 5 minutes during 08:00–15:55 IST (`repository_dispatch`).
2. In-repo **self-chain** after the first scan of the day (needs
   `SCAN_DISPATCH_TOKEN`). That chain **stops after 15:45**, so the next
   morning still needs the cloud ping.

## Required: cron-job.org (runs while you sleep)

Free account: https://console.cron-job.org/signup

1. Sign up (email you can confirm), then Console → **Settings** → create an
   **API key**.
2. On this Mac, in a terminal (the key is typed hidden, not into chat):

```bash
cd "/Users/abhilashmunnur/OI with stocks"
python3 scripts/provision_cronjob_org.py
```

That creates a POST every 5 minutes, **08:00–15:55 IST, Mon–Fri**, to:

`https://api.github.com/repos/AbhilashMunnur/oi-with-stocks/dispatches`

Body: `{"event_type":"oi-scan"}`. Auth is your current `gh` token (`repo` scope).

The workflow slot guard no-ops in a few seconds if that half-hour already ran.
Duplicate pings are safe.

Re-run the same script after `gh auth login` if the GitHub token is rotated.

## Already done in GitHub

- `SCAN_DISPATCH_TOKEN` refreshed (self-chain for the rest of a session).
- 08:30 warmup, if GitHub actually runs it, also dispatches the 09:30 chain.
- Chain job can wait 90 minutes (used to die at 40).

## Optional: this Mac, only if it is already awake

```bash
./scripts/install_mac_scan_watch.sh
```

Unload it if you do not want local pings:

```bash
launchctl bootout "gui/$(id -u)/com.oiwithstocks.scanwatch"
```
