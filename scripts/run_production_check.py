#!/usr/bin/env python3
"""Invoke the deployed agent and print only safe, non-sensitive evidence fields."""
from __future__ import annotations

import argparse
import os
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# Set these for your own deployment, or pass --worker/--viewer.
DEFAULT_WORKER = os.environ.get("KP_WORKER_URL", "")
DEFAULT_VIEWER = os.environ.get("KP_VIEWER_URL", "")


def _json_request(url: str, *, token: str | None = None, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    with urlopen(request, timeout=320) as response:  # noqa: S310 - explicit production URLs/CLI overrides
        result = json.load(response)
    if not isinstance(result, dict):
        raise ValueError("expected a JSON object")
    return result


def _identity_token() -> str:
    result = subprocess.run(
        ["gcloud", "auth", "print-identity-token"],
        check=True,
        capture_output=True,
        text=True,
    )
    token = result.stdout.strip()
    if not token:
        raise RuntimeError("gcloud returned an empty identity token")
    return token


def _evidence(run: dict, plan: dict) -> dict:
    return {
        "date": run.get("date"),
        "expected_covers": run.get("expected_covers"),
        "forecast_source": run.get("forecast_source"),
        "forecast_note": run.get("forecast_note"),
        "briefing_source": plan.get("briefing_source"),
        "inventory_basis": plan.get("inventory_basis"),
        "planning_basis": plan.get("planning_basis"),
        "prep_tasks": run.get("prep_tasks"),
        "prep_shortfalls": run.get("prep_shortfalls"),
        "replenishment_orders": run.get("replenishment_orders"),
        "waste_flagged": run.get("waste_flagged"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", default=DEFAULT_WORKER)
    parser.add_argument("--viewer", default=DEFAULT_VIEWER)
    parser.add_argument("--date", help="Optional YYYY-MM-DD; defaults to today in Europe/Oslo")
    parser.add_argument("--no-force", action="store_true", help="Use the idempotent saved plan instead of a forced replay")
    args = parser.parse_args()

    if not args.worker or not args.viewer:
        print(
            "ERROR: set KP_WORKER_URL and KP_VIEWER_URL, or pass --worker/--viewer.",
            file=sys.stderr,
        )
        return 2

    payload: dict[str, object] = {"force": not args.no_force}
    if args.date:
        payload["date"] = args.date

    try:
        token = _identity_token()
        print("Invoking authenticated Cloud Run worker...", flush=True)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(
                _json_request,
                f"{args.worker.rstrip('/')}/runs/daily",
                token=token,
                payload=payload,
            )
            started = time.monotonic()
            while not pending.done():
                elapsed = int(time.monotonic() - started)
                print(f"  agent running · {elapsed:02d}s", flush=True)
                time.sleep(3)
            run = pending.result()
        print("Worker completed. Reading the published plan...", flush=True)
        plan = _json_request(f"{args.viewer.rstrip('/')}/plans/latest")
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("ERROR: gcloud authentication failed. Run `gcloud auth login` first.", file=sys.stderr)
        return 2
    except (HTTPError, URLError, TimeoutError, ValueError, RuntimeError) as exc:
        print(f"ERROR: production check failed: {exc}", file=sys.stderr)
        return 1

    print("DEPLOYED AGENT EVIDENCE")
    print(json.dumps(_evidence(run, plan), indent=2, sort_keys=False))
    print(f"viewer_url: {args.viewer.rstrip('/')}/?lang=en")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
