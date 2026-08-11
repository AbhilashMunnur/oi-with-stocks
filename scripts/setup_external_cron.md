# Reliable scan scheduling

GitHub’s built-in `schedule` trigger is **best-effort**. On busy runners it is
often late or skipped entirely — that is a platform limitation, not a bug in
this repo’s scanner.

## What we do in-repo

1. Poll every 5 minutes during the IST session, plus exact `:00` / `:30` crons.
2. **Never cancel** an in-flight scan (`cancel-in-progress: false`). Older
   polls used to kill a running scan when the next cron started.
3. Slot guard so each half-hour (09:30 … 15:30 IST) runs at most once.
4. **Self-chain** (recommended): after each poll, wait until the next half-hour
   and `repository_dispatch` the next run. Needs secret `SCAN_DISPATCH_TOKEN`.

## One-time setup (recommended)

From your Mac, in this repo:

```bash
chmod +x scripts/setup_scan_cron.sh
./scripts/setup_scan_cron.sh
```

That stores your `gh` token as `SCAN_DISPATCH_TOKEN` and fires a test dispatch.

## Optional third-party backup

If you also want an external ping (belt and suspenders), create a free job at
https://cron-job.org :

- URL: `https://api.github.com/repos/AbhilashMunnur/oi-with-stocks/dispatches`
- Method: `POST`
- Schedule: every 5 minutes, 04:00–10:15 UTC, Mon–Fri
- Headers:
  - `Accept: application/vnd.github+json`
  - `Authorization: Bearer YOUR_PAT`
  - `X-GitHub-Api-Version: 2022-11-28`
- Body: `{"event_type":"oi-scan"}`
