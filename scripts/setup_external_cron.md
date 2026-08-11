# External cron (optional backup if GitHub schedule is late)

GitHub’s own `schedule` event is used first. If it keeps skipping, add a free
external ping that triggers the same workflow:

1. Create a GitHub **classic PAT** with the `workflow` scope  
   https://github.com/settings/tokens
2. Sign up at https://cron-job.org (free)
3. Create a job:
   - URL: `https://api.github.com/repos/AbhilashMunnur/oi-with-stocks/actions/workflows/scan.yml/dispatches`
   - Method: `POST`
   - Schedule: every 30 minutes, 04:00–10:00 UTC, Mon–Fri  
     (09:30–15:30 IST)
   - Headers:
     - `Accept: application/vnd.github+json`
     - `Authorization: Bearer YOUR_PAT`
     - `X-GitHub-Api-Version: 2022-11-28`
   - Body (JSON): `{"ref":"main"}`

The workflow’s slot guard still prevents double scans for the same half-hour.
