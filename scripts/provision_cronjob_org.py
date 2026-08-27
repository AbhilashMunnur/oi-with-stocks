#!/usr/bin/env python3
"""Create or update the cron-job.org ping that kicks GitHub scans.

GitHub's own schedule is best-effort and can skip an entire morning. This
job POSTs repository_dispatch (oi-scan) every 5 minutes during the NSE
session so the Mac does not need to stay awake.

Usage:
  CRON_JOB_ORG_API_KEY=... python3 scripts/provision_cronjob_org.py

The API key is created in cron-job.org Console → Settings. The GitHub
token is read from `gh auth token` (already has repo scope).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.cron-job.org"
REPO = os.environ.get("GITHUB_REPOSITORY", "AbhilashMunnur/oi-with-stocks")
JOB_TITLE = "OI with stocks — scan kick"
DISPATCH_URL = f"https://api.github.com/repos/{REPO}/dispatches"


def gh_token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token.strip()
    try:
        out = subprocess.check_output(["gh", "auth", "token"], text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f"Need a GitHub token (gh auth login): {exc}")
    token = out.strip()
    if not token:
        sys.exit("gh auth token was empty")
    return token


def api_key() -> str:
    key = os.environ.get("CRON_JOB_ORG_API_KEY", "").strip()
    if key:
        return key
    try:
        import getpass
    except ImportError:
        sys.exit("Set CRON_JOB_ORG_API_KEY")
    key = getpass.getpass("cron-job.org API key (Settings → API): ").strip()
    if not key:
        sys.exit("No API key given")
    return key


def request(method: str, path: str, key: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        sys.exit(f"cron-job.org {method} {path} failed ({exc.code}): {body}")
    if not raw:
        return {}
    return json.loads(raw)


def job_body(github_token: str) -> dict:
    # Cartesian product: 08:00–15:55 IST Mon–Fri every 5 minutes.
    # Extra pings before 09:30 / after 15:30 are no-ops (slot guard).
    return {
        "job": {
            "enabled": True,
            "title": JOB_TITLE,
            "saveResponses": False,
            "url": DISPATCH_URL,
            "requestMethod": 1,  # POST
            "schedule": {
                "timezone": "Asia/Kolkata",
                "expiresAt": 0,
                "hours": list(range(8, 16)),
                "mdays": [-1],
                "minutes": list(range(0, 60, 5)),
                "months": [-1],
                "wdays": [1, 2, 3, 4, 5],
            },
            "extendedData": {
                "headers": {
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {github_token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                "body": json.dumps({"event_type": "oi-scan"}),
            },
        }
    }


def main() -> None:
    key = api_key()
    token = gh_token()
    existing = request("GET", "/jobs", key).get("jobs") or []
    match = next((j for j in existing if j.get("title") == JOB_TITLE), None)
    payload = job_body(token)
    if match:
        job_id = match["jobId"]
        request("PATCH", f"/jobs/{job_id}", key, payload)
        print(f"Updated cron-job.org job {job_id} ({JOB_TITLE})")
    else:
        created = request("PUT", "/jobs", key, payload)
        print(f"Created cron-job.org job {created.get('jobId')} ({JOB_TITLE})")
    print("Schedule: every 5 minutes, 08:00–15:55 IST, Mon–Fri")
    print(f"Target:   POST {DISPATCH_URL} event_type=oi-scan")


if __name__ == "__main__":
    main()
