"""Surgical category-filter fix on the verified production build12 archive."""
from pathlib import Path
import hashlib
import io
import json
import tarfile

ROOT = Path(__file__).resolve().parents[1]
base = json.loads((ROOT/'docs/modish2/build12-deployment-record.json').read_text())
archive_path = ROOT/'.tmp/modish-build12-server.tar'
assert base['status'] == 'deployed'
assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == base['archive_sha256']
with tarfile.open(archive_path) as archive:
    baseline = json.load(archive.extractfile('source-manifest.json'))
    entries = {name: archive.extractfile(name).read() for name in baseline['files']}
assert all(hashlib.sha256(entries[name]).hexdigest() == sha for name, sha in baseline['files'].items())
path = 'backend/app/services/activation_service.py'
code = entries[path].decode()
current = (ROOT/path).read_text(encoding='utf-8')
marker = '    wanted = set(profile.style_preferences or []) | set(taste.liked_styles or [])\n'
assert code.count(marker) == 1
insert = current.split('    # An explicit category expresses current intent.', 1)[1].split(marker, 1)[0]
code = code.replace(marker, '    # An explicit category expresses current intent.'+insert+marker, 1)
start, end = code.index('def diverse_products('), code.index('\ndef configure(')
new_function = current[current.index('def diverse_products('):current.index('\ndef configure(')]
code = code[:start]+new_function+code[end:]
old = '    chosen = diverse_products(pool, cap, exploration=True)\n'
assert code.count(old) == 1
code = code.replace(old, '    chosen = diverse_products(pool, cap, exploration=True,\n'
    '                              allow_category_fallback=bool(filters and filters.categories))\n', 1)
assert 'fresh_stock_conditions' not in code and 'size_evidence' not in code
entries[path] = code.encode()
test = 'backend/tests/test_activation_category_filters.py'
entries[test] = (ROOT/test).read_bytes().replace(b'\r\n', b'\n')
assert not any('0022' in name or 'stock_freshness' in name or 'product_dna' in name for name in entries)
manifest = {'release': 'build13-explicit-category-filters', 'base_image': base['image_id'],
    'database_migration': False, 'catalog_foundation_included': False,
    'overlay': [path, test],
    'files': {name: hashlib.sha256(data).hexdigest() for name, data in entries.items()}}
entries['source-manifest.json'] = json.dumps(manifest, indent=2).encode()
destination = ROOT/'.tmp/modish-build13-server.tar'
with tarfile.open(destination, 'w') as archive:
    for name, data in entries.items():
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.mode = 0o755 if name.endswith('.sh') else 0o644
        archive.addfile(info, io.BytesIO(data))
sha = hashlib.sha256(destination.read_bytes()).hexdigest()
(ROOT/'docs/modish2/build13-server-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
remote = (ROOT/'scripts/build12_remote.py').read_text(encoding='utf-8')
remote = remote.replace('20261006-build12', '20261006-build13')
remote = remote.replace("OLD = 'modish-backend:build11-20260922'", "OLD = 'modish-backend:build12-20261006'")
remote = remote.replace("NEW = 'modish-backend:build12-20261006'", "NEW = 'modish-backend:build13-20261006'")
remote = remote.replace(base['previous_image'], base['image_id']).replace(base['archive_sha256'], sha)
remote = remote.replace('assert unit_count >= 130', 'assert unit_count >= 135')
remote = remote.replace('Thirty-day feed feedback exclusion and merchant-model deduplication.',
    'Explicit category intent overrides learned interests; filtered pages can fill a single category.')
(ROOT/'scripts/build13_remote.py').write_text(remote, encoding='utf-8')
ssh = (ROOT/'scripts/build12_ssh.py').read_text(encoding='utf-8')
ssh = ssh.replace('20261006-build12', '20261006-build13').replace('modish-build12-server.tar', 'modish-build13-server.tar')
ssh = ssh.replace('scripts/build12_remote.py', 'scripts/build13_remote.py').replace("('build12-' + name)", "('build13-' + name)")
ssh = ssh.replace('scripts/smoke_build11.py', 'scripts/smoke_build13.py')
(ROOT/'scripts/build13_ssh.py').write_text(ssh, encoding='utf-8')
print(json.dumps({'archive_sha256': sha, 'files': len(manifest['files']), 'migration': False}))
