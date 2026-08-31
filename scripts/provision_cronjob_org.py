#!/usr/bin/env python3
"""Create or update cron-job.org pings at OI half-hour IST slots.

GitHub bills a full minute per run. The old every-5-minutes 08:00–15:55 job
shared the 2,000 min/month cap with nifty-index-trade and exhausted it.

Slots: 09:30, then :00/:30 through 15:30, plus 15:15 and 15:45 IST, Mon–Fri.

Usage:
  CRON_JOB_ORG_API_KEY=... python3 scripts/provision_cronjob_org.py
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
OLD_TITLE = "OI with stocks — scan kick"
DISPATCH_URL = f"https://api.github.com/repos/{REPO}/dispatches"
SPECS = (
    ("OI scan — 09:30 IST", [9], [30]),
    ("OI scan — 10:00–14:30 IST", list(range(10, 15)), [0, 30]),
    ("OI scan — 15:00–15:45 IST", [15], [0, 15, 30, 45]),
)


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


def job_body(github_token: str, title: str, hours: list[int], minutes: list[int]) -> dict:
    return {
        "job": {
            "enabled": True,
            "title": title,
            "saveResponses": False,
            "url": DISPATCH_URL,
            "requestMethod": 1,  # POST
            "schedule": {
                "timezone": "Asia/Kolkata",
                "expiresAt": 0,
                "hours": hours,
                "mdays": [-1],
                "minutes": minutes,
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
    old = next((j for j in existing if j.get("title") == OLD_TITLE), None)
    if old:
        request(
            "PATCH",
            f"/jobs/{old['jobId']}",
            key,
            {"job": {"enabled": False, "title": OLD_TITLE}},
        )
        print(f"Disabled old 5-minute job {old['jobId']} ({OLD_TITLE})")
        existing = request("GET", "/jobs", key).get("jobs") or []
    for title, hours, minutes in SPECS:
        payload = job_body(token, title, hours, minutes)
        match = next((j for j in existing if j.get("title") == title), None)
        if match:
            request("PATCH", f"/jobs/{match['jobId']}", key, payload)
            print(f"Updated cron-job.org job {match['jobId']} ({title})")
        else:
            created = request("PUT", "/jobs", key, payload)
            print(f"Created cron-job.org job {created.get('jobId')} ({title})")
        existing = request("GET", "/jobs", key).get("jobs") or []
    print("Schedule: 09:30 then :00/:30 through 15:30, plus 15:15 and 15:45 IST, Mon–Fri")
    print(f"Target:   POST {DISPATCH_URL} event_type=oi-scan")


if __name__ == "__main__":
    main()
