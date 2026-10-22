"""Download exactly four fixed HTTPS exports; credentials arrive only on stdin.

No app/DB imports, production environment files, redirects or environment proxy.
Only completed new snapshots and a URL-free observation manifest are published.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
from contextlib import contextmanager
import sys
from time import monotonic
from urllib.parse import urlencode

import httpx

FEEDS = {"baon": "19982", "shoppinglive": "25461", "serginnetti": "26417", "postmeridiem": "25200"}
MAX_BYTES = 256 * 1024 * 1024
TARGET = Path("/feeds")


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


@contextmanager
def total_request_deadline(seconds):
    # Linux container: unlike an httpx read timeout, ITIMER_REAL also bounds
    # waiting for headers and an arbitrarily slow sequence of body reads.
    require(hasattr(signal, "setitimer") and hasattr(signal, "SIGALRM"),
        "linux_wall_clock_timer_required")
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    require(previous_timer == (0.0, 0.0), "unexpected_existing_wall_clock_timer")
    def expired(signum, frame):
        raise RuntimeError("download_total_deadline")
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def main():
    os.umask(0o077)
    require(not os.getenv("AUDIT_FIXED_NOW_UTC"), "historical_clock_forbidden")
    data = sys.stdin.buffer.read(8193)
    require(len(data) <= 8192, "configuration_input_bound")
    config = json.loads(data)
    require(set(config) == {"website_id", "user", "code", "run_id"}, "configuration_schema")
    require(isinstance(config["website_id"], str) and re.fullmatch(r"[0-9]+", config["website_id"]), "website_id_format")
    require(re.fullmatch(r"[0-9a-f]{32}", config["run_id"] or ""), "run_id_format")
    for key in ("user", "code"):
        require(isinstance(config[key], str) and config[key].strip() == config[key] and
            0 < len(config[key]) <= 1024 and not any(ord(c) < 32 for c in config[key]), "credential_format")
    require(TARGET.is_dir() and not TARGET.is_symlink(), "new_feed_directory_required")
    require(not any(TARGET.iterdir()), "feed_directory_must_be_empty")
    report = {"scope": "four_fixed_admitad_https_exports_no_database", "run_id": config["run_id"],
        "result": "running", "started_at": datetime.now(timezone.utc).isoformat(), "sources": [],
        "raw_urls_exported": False, "credentials_persisted": False, "database_connected": False,
        "max_bytes_per_source": MAX_BYTES, "observation_clock_basis": "real_download_request_start_utc"}
    with httpx.Client(timeout=httpx.Timeout(120, connect=20), follow_redirects=False, trust_env=False) as client:
        for code, feed_id in FEEDS.items():
            request_started = datetime.now(timezone.utc)
            url = "https://export.admitad.com/ru/webmaster/websites/" + config["website_id"] + "/products/export_adv_products/?" + urlencode(
                {"user": config["user"], "code": config["code"], "feed_id": feed_id, "format": "csv"})
            partial, complete = TARGET / (code + ".csv.partial"), TARGET / (code + ".csv")
            require(not partial.exists() and not complete.exists(), "snapshot_already_exists")
            size, digest, deadline = 0, hashlib.sha256(), monotonic() + 600
            with total_request_deadline(600), client.stream("GET", url) as response:
                require(response.status_code == 200, "unexpected_http_status")
                require(response.headers.get("content-type", "").split(";")[0].lower() == "text/csv", "csv_content_type_required")
                length = response.headers.get("content-length")
                require(length is None or (length.isdigit() and 0 < int(length) <= MAX_BYTES), "content_length_bound")
                with partial.open("xb") as output:
                    for chunk in response.iter_bytes(chunk_size=65536):
                        size += len(chunk)
                        require(size <= MAX_BYTES and monotonic() <= deadline, "download_bound")
                        digest.update(chunk)
                        output.write(chunk)
                    require(size > 0, "empty_snapshot")
                    output.flush()
                    os.fsync(output.fileno())
            # Atomic exclusive publication on the same mounted filesystem.
            os.link(partial, complete)
            partial.unlink()
            report["sources"].append({"code": code, "feed_id": feed_id, "filename": complete.name,
                "bytes": size, "sha256": digest.hexdigest(), "observed_at": request_started.isoformat(),
                "download_finished_at": datetime.now(timezone.utc).isoformat()})
            print(json.dumps({"progress": "snapshot_downloaded", "source": code, "bytes": size}), file=sys.stderr, flush=True)
    config.clear()
    url = None
    report.update(result="download_complete", finished_at=datetime.now(timezone.utc).isoformat())
    with (TARGET / "download-manifest.json").open("x") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    print(json.dumps(report))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"result": "failed", "error_type": type(error).__name__, "raw_error_omitted": True}))
        raise SystemExit(1) from None
