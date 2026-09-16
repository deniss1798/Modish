"""Run on the host after isolated validation; updates only the API service."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
import urllib.request

RELEASE = Path("/opt/modish-releases/20260913-outfit-fixes")
OLD = "modish-backend:github-da5a0be1-d00"
NEW = "modish-backend:outfit-fixes-20260913"


def main():
    smoke = [json.loads(line) for line in (RELEASE / "catalog-smoke.log").read_text().splitlines() if line.startswith("{")]
    assert smoke[-1].get("result") == "passed" and smoke[-1].get("scenarios") == 6, "isolated_smoke_required"
    assert "Ran 82 tests" in (RELEASE / "unit-tests.log").read_text() and "\nOK\n" in (RELEASE / "unit-tests.log").read_text(), "unit_tests_required"
    current = json.loads(subprocess.check_output(["docker", "inspect", "modish_backend"]))[0]
    old_id = subprocess.check_output(["docker", "image", "inspect", OLD, "--format", "{{.Id}}"], text=True).strip()
    assert current["Image"] == old_id, "active_image_changed"
    compose = Path("/opt/Modish/docker-compose.yml")
    original = compose.read_text()
    assert OLD in original, "unexpected_compose"
    record = {"started_at": datetime.now(timezone.utc).isoformat(), "previous_image":old_id, "status":"starting"}
    with (RELEASE / "activation.log").open("w") as log:
        try:
            subprocess.run([str(RELEASE / "release.sh")], check=True, stdout=log, stderr=subprocess.STDOUT)
            for attempt in range(30):
                try:
                    with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3) as response:
                        health = json.load(response)
                    if health.get("database") == "ok":
                        break
                except Exception:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError("new_api_health_failed")
            checks = []
            for base in ("http://127.0.0.1:8000", "https://modish.org.ru"):
                for route in ("/health", "/products?limit=1"):
                    with urllib.request.urlopen(base + route, timeout=15) as response:
                        assert response.status == 200
                    checks.append({"base":base,"route":route,"status":200})
            compose.write_text(original.replace(OLD, NEW))
            record.update(status="deployed", health=health, checks=checks,
                image_id=subprocess.check_output(["docker","inspect","modish_backend","--format","{{.Image}}"],text=True).strip(),
                source_files=117, unit_tests=82, restored_catalog_products=183543,
                isolated_checks=smoke[-1]["checks"], migrations_applied=False,
                finished_at=datetime.now(timezone.utc).isoformat())
        except Exception:
            compose.write_text(original)
            subprocess.run([str(RELEASE / "rollback.sh")], check=True, stdout=log, stderr=subprocess.STDOUT)
            record["status"] = "rolled_back"
            (RELEASE / "deployment-record.json").write_text(json.dumps(record, indent=2))
            raise
    (RELEASE / "deployment-record.json").write_text(json.dumps(record, indent=2))
    print(json.dumps(record))


if __name__ == "__main__":
    main()
