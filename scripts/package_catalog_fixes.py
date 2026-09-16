"""Package only the reviewed outfit/feed fixes on the deployed baseline."""
from pathlib import Path
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BASE = "da5a0be1ae96b9656ede7d97dee3799ff3b4c5ca"
FILES = [
    "app/api/outfits.py", "app/services/onboarding_service.py",
    "app/services/outfit_engine_v2.py", "app/services/outfit_service.py",
    "app/services/outfit_quality.py", "app/services/product_identity.py",
    "app/services/recommendation_engine.py",
    "app/services/catalog/rule_filters.py",
    "tests/test_outfit_engine_v2.py", "tests/test_recommendation_exclusions.py",
    "app/catalog_normalize.py", "app/api/products.py", "app/api/media_proxy.py",
    "app/schemas/api_product.py", "app/services/catalog_filters.py",
    "app/services/candidate_retrieval_service.py",
    "app/services/catalog/product_normalizer.py", "app/services/catalog/catalog_quality.py",
    "app/services/catalog/affiliate_link_service.py",
    "tests/test_catalog_device_regressions.py", "scripts/repair_catalog_gender.py",

]

def main():
    archive = subprocess.check_output(["git", "archive", BASE, "backend"], cwd=ROOT)
    entries = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as src:
        for member in src.getmembers():
            if member.isfile():
                entries[member.name] = src.extractfile(member).read()
    for relative in FILES:
        entries[f"backend/{relative}"] = (ROOT / "backend" / relative).read_bytes().replace(b"\r\n", b"\n")
    entries["backend/.dockerignore"] = b".env*\n.venv\n__pycache__\n*.pyc\n.git\n*.db\n"
    # Example env files are documentation, not needed in the image/context.
    entries = {k: v for k, v in entries.items() if not Path(k).name.startswith(".env")}
    manifest = {"baseline": BASE, "files": {k: hashlib.sha256(v).hexdigest() for k, v in entries.items()}}
    destination = Path(tempfile.gettempdir()) / "modish-catalog-fixes.tar"
    with tarfile.open(destination, "w") as tar:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name.endswith(".sh") else 0o644
            tar.addfile(info, io.BytesIO(data))
        data = json.dumps(manifest, indent=2).encode()
        info = tarfile.TarInfo("source-manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    print(json.dumps({"archive": str(destination), "files": len(entries), "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}))

if __name__ == "__main__":
    main()
