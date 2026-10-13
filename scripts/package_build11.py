"""Package the six user-reported fixes on the verified release9 server sources."""
from pathlib import Path
import hashlib
import io
import json
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = (
    'app/api/products.py',
    'app/services/product_search.py',
    'app/services/outfit_quality.py',
    'tests/test_build11_fixes.py',
)


def main():
    record = json.loads((ROOT / 'docs/modish2/release9_deployment_record.json').read_text(encoding='utf-8'))
    source = Path(tempfile.gettempdir()) / 'modish-release9.tar'
    with tarfile.open(source) as archive:
        baseline = json.load(archive.extractfile('source-manifest.json'))
        assert baseline == record['manifest'], 'Release9 source archive must match deployed manifest'
        entries = {name: archive.extractfile(name).read() for name in baseline['files']}
    assert all(hashlib.sha256(entries[name]).hexdigest() == checksum
               for name, checksum in baseline['files'].items())
    for relative in OVERLAY:
        entries['backend/' + relative] = (ROOT / 'backend' / relative).read_bytes().replace(b'\r\n', b'\n')
    assert not any('0022' in name or 'catalog_records' in name for name in entries)
    manifest = {
        'release': 'build11-six-fixes',
        'base_image': record['deployment']['image_id'],
        'overlay': list(OVERLAY),
        'database_migration': False,
        'catalog_foundation_included': False,
        'files': {name: hashlib.sha256(data).hexdigest() for name, data in entries.items()},
    }
    entries['source-manifest.json'] = json.dumps(manifest, indent=2).encode()
    destination = ROOT / '.tmp/modish-build11-server.tar'
    with tarfile.open(destination, 'w') as archive:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name.endswith('.sh') else 0o644
            archive.addfile(info, io.BytesIO(data))
    unpacked = ROOT / '.tmp/build11-server'
    unpacked.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination) as archive:
        archive.extractall(unpacked, filter='data')
    (ROOT / 'docs/modish2/build11-server-manifest.json').write_text(
        json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'archive': str(destination), 'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
                      'files': len(manifest['files']), 'migration': False}))


if __name__ == '__main__':
    main()
