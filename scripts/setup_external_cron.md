# Live scan kick (Mac does not need to stay awake)

GitHub Free private repos share **2,000 Actions minutes/month** with
nifty-index-trade. Every run is billed at least 1 minute, including skips
and `sleep` on the runner.

## Minute budget (OI)

Half-hour IST slots: **09:30, then :00/:30 through 15:30, plus 15:15 and 15:45**.
Do **not** ping every 5 minutes from 08:00. Nifty has its own 15-minute cron;
this repo must not dispatch `nifty-scan`.

## Required: cron-job.org (runs while you sleep)

Free account: https://console.cron-job.org/signup

1. Sign up (email you can confirm), then Console → **Settings** → create an
   **API key**.
2. On this Mac, in a terminal (the key is typed hidden, not into chat):

```bash
cd "/Users/abhilashmunnur/OI with stocks"
python3 scripts/provision_cronjob_org.py
```

That POSTs at the half-hour slots above, Mon–Fri, to:

`https://api.github.com/repos/AbhilashMunnur/oi-with-stocks/dispatches`

Body: `{"event_type":"oi-scan"}`. Auth is your current `gh` token (`repo` scope).

The script disables the old every-5-minutes job. Re-run after `gh auth login`
if the GitHub token is rotated.

## Already in GitHub

- No self-chain sleep job (that billed wait time).
- No `kick-nifty` job.

## Optional: this Mac, only if it is already awake

```bash
./scripts/install_mac_scan_watch.sh
```

Unload it if you do not want local pings:

```bash
launchctl bootout "gui/$(id -u)/com.oiwithstocks.scanwatch"
```
