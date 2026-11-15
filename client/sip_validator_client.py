#!/usr/bin/env python3
"""Sample client: submit a SIP to the SIP Validator Service and save its XML report.

Exit codes: 0 = SIP valid, 1 = SIP invalid, 2 = validation error, 3 = timeout.
"""

import argparse
import os
import sys
import time
from pathlib import Path

import requests


class SipValidatorClient:
    def __init__(self, base_url: str, api_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        if api_key:
            self.session.headers["X-API-Key"] = api_key

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def submit(self, sip: Path, language: str = "de", log_type: str = "xml") -> dict:
        with sip.open("rb") as fh:
            resp = self.session.post(
                self._url("/validations"),
                files={"sip": (sip.name, fh, "application/zip")},
                data={"language": language, "log_type": log_type},
                timeout=(10, 3600),
            )
        resp.raise_for_status()
        return resp.json()

    def status(self, job_id: str) -> dict:
        resp = self.session.get(self._url(f"/validations/{job_id}"), timeout=30)
        resp.raise_for_status()
        return resp.json()

    def wait(self, job_id: str, poll_interval: float, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            try:
                job = self.status(job_id)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Unable to reach the API while waiting for job {job_id}"
                    ) from exc
                delay = min(max(poll_interval, 1.0), remaining)
                print(
                    f"  ... status request failed ({exc}); retrying in {delay:g}s",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(delay)
                continue

            if job["status"] not in ("queued", "running"):
                return job
            if time.monotonic() > deadline:
                raise TimeoutError(f"Job {job_id} still {job['status']} after {timeout}s")
            print(f"  ... {job['status']}", flush=True)
            time.sleep(poll_interval)

    def download_report(self, job_id: str, target: Path) -> None:
        with self.session.get(self._url(f"/validations/{job_id}/report"), stream=True, timeout=60) as resp:
            resp.raise_for_status()
            with target.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    fh.write(chunk)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a SIP with the SIP Validator Service.")
    parser.add_argument("sip", type=Path, help="Path to the zipped SIP")
    parser.add_argument(
        "--url",
        default=os.environ.get("SIP_VALIDATOR_URL", os.environ.get("KOSTVAL_URL", "http://localhost:8001")),
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("SIP_VALIDATOR_API_KEY", os.environ.get("KOSTVAL_API_KEY")),
    )
    parser.add_argument("--language", choices=["de", "fr", "it", "en"], default="de")
    parser.add_argument("--log-type", choices=["xml", "min", "max"], default="xml")
    parser.add_argument("--out", type=Path, help="Where to store the report (default: <sip>.kost-val.log.xml)")
    parser.add_argument("--poll", type=float, default=5.0, help="Polling interval in seconds")
    parser.add_argument("--timeout", type=float, default=3600.0, help="Max seconds to wait for the result")
    args = parser.parse_args()

    if not args.sip.is_file():
        parser.error(f"SIP not found: {args.sip}")

    client = SipValidatorClient(args.url, args.api_key)

    print(f"Uploading {args.sip} to {args.url} ...")
    job = client.submit(args.sip, args.language, args.log_type)
    print(f"Job {job['id']} {job['status']}")

    try:
        job = client.wait(job["id"], args.poll, args.timeout)
    except TimeoutError as exc:
        print(exc, file=sys.stderr)
        return 3

    print(f"Result: {job['status'].upper()}")
    if job.get("message"):
        print(f"Message: {job['message']}")
    for err in job["errors"]:
        print(f"  - {err['module']}: {err['message']}")

    if job.get("report_url"):
        out = args.out or args.sip.with_name(args.sip.name + ".kost-val.log.xml")
        client.download_report(job["id"], out)
        print(f"Report saved to {out}")

    return {"valid": 0, "invalid": 1}.get(job["status"], 2)


if __name__ == "__main__":
    sys.exit(main())
