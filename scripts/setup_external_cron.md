# Reliable scan scheduling

GitHub’s built-in `schedule` trigger is **best-effort**. On busy runners it is
often late or skipped entirely — that is a platform limitation, not a bug in
this repo’s scanner. **A missed 09:30–11:30 window is missed live paper**
(entries, stops, scale-outs). It is not backfilled.

## What we do in-repo

1. Poll every 5 minutes during the IST session, plus exact `:00` / `:30` crons.
2. **Never cancel** an in-flight scan (`cancel-in-progress: false`). Older
   polls used to kill a running scan when the next cron started.
3. Slot guard so each half-hour (09:30 … 15:30 IST) runs at most once.
4. **Self-chain** (recommended): after each poll, wait until the next half-hour
   and `repository_dispatch` the next run. Needs secret `SCAN_DISPATCH_TOKEN`.
   The chain **stops after 15:45**. Next morning needs a kick (warmup, Mac
   watch, or cron-job.org). Chain wait can be ~50 minutes before 09:30.

## Morning kick (required for live paper)

Do **at least one** of these. GitHub cron alone is not enough.

**A. This Mac (awake during market hours):**

```bash
chmod +x scripts/install_mac_scan_watch.sh scripts/keep_scans_alive.sh
./scripts/install_mac_scan_watch.sh
```

Pings GitHub every 5 minutes from 09:15–15:50 IST. Slot guard no-ops if that
half-hour already ran.

**B. Self-chain token** (rest of the session after the first scan of the day):

```bash
chmod +x scripts/setup_scan_cron.sh
./scripts/setup_scan_cron.sh
```

**C. Optional third-party backup** when the Mac is asleep — cron-job.org:

- URL: `https://api.github.com/repos/AbhilashMunnur/oi-with-stocks/dispatches`
- Method: `POST`
- Schedule: every 5 minutes, 03:45–10:20 UTC, Mon–Fri
- Headers:
  - `Accept: application/vnd.github+json`
  - `Authorization: Bearer YOUR_PAT`
  - `X-GitHub-Api-Version: 2022-11-28`
- Body: `{"event_type":"oi-scan"}`
