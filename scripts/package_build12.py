"""Small repeat-card fix on verified build11; no unfinished Foundation changes."""
from pathlib import Path
import hashlib
import io
import json
import tarfile

ROOT = Path(__file__).resolve().parents[1]
with tarfile.open(ROOT/'.tmp/modish-build11-server.tar') as archive:
    baseline = json.load(archive.extractfile('source-manifest.json'))
    assert baseline == json.loads((ROOT/'docs/modish2/build11-server-manifest.json').read_text())
    entries = {name: archive.extractfile(name).read() for name in baseline['files']}
assert all(hashlib.sha256(entries[name]).hexdigest() == sha for name, sha in baseline['files'].items())
path = 'backend/app/api/products.py'
code = entries[path].decode()
for old, new in [
    ('from ..models import Product, User, UserProductState, FitProfile',
     'from ..models import Product, User, UserProductState, FitProfile, RecommendationEventV2'),
    ('from datetime import datetime, timezone', 'from datetime import datetime, timezone, timedelta'),
]:
    assert code.count(old) == 1
    code = code.replace(old, new, 1)
current = (ROOT/path).read_text(encoding='utf-8')
block = current.split('  # Covers old short cooldowns too,', 1)[1].split('  try:\n', 1)[0]
marker = '  hidden_ids.update(exclude_ids or [])\n'
assert code.count(marker) == 1
code = code.replace(marker, marker+'  # Covers old short cooldowns too,'+block, 1)
entries[path] = code.encode()
for name in ['app/services/product_identity.py', 'tests/test_feed_repeat_regressions.py']:
    entries['backend/'+name] = (ROOT/'backend'/name).read_bytes().replace(b'\r\n', b'\n')
assert not any('0022' in name or 'stock_freshness' in name or 'product_dna' in name for name in entries)
manifest = {
    'release': 'build12-feed-repeats',
    'base_image': json.loads((ROOT/'docs/modish2/build11-deployment-record.json').read_text())['image_id'],
    'database_migration': False, 'catalog_foundation_included': False,
    'overlay': ['app/api/products.py:recent_feedback_only', 'app/services/product_identity.py', 'tests/test_feed_repeat_regressions.py'],
    'files': {name: hashlib.sha256(data).hexdigest() for name, data in entries.items()},
}
entries['source-manifest.json'] = json.dumps(manifest, indent=2).encode()
destination = ROOT/'.tmp/modish-build12-server.tar'
with tarfile.open(destination, 'w') as archive:
    for name, data in entries.items():
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.mode = 0o755 if name.endswith('.sh') else 0o644
        archive.addfile(info, io.BytesIO(data))
sha = hashlib.sha256(destination.read_bytes()).hexdigest()
(ROOT/'docs/modish2/build12-server-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
# Reuse the guarded API-only deployment procedure with new, isolated paths.
remote = (ROOT/'scripts/build11_remote.py').read_text(encoding='utf-8')
remote = remote.replace('20260922-build11', '20261006-build12')
remote = remote.replace("OLD = 'modish-backend:release9-quality-20260920'", "OLD = 'modish-backend:build11-20260922'")
remote = remote.replace('sha256:02a0a2531da09c51b8924081b67377c556e2b2fe771bcc5a184b88117f24386c', manifest['base_image'])
remote = remote.replace("NEW = 'modish-backend:build11-20260922'", "NEW = 'modish-backend:build12-20261006'")
remote = remote.replace('fcaeee0e8101e49f9e4bd3fb9c55382519e23e286660e8341276a7f788d708af', sha)
remote = remote.replace('assert unit_count >= 128', 'assert unit_count >= 130')
remote = remote.replace('Catalogue search and weather-compatible complements.', 'Thirty-day feed feedback exclusion and merchant-model deduplication.')
(ROOT/'scripts/build12_remote.py').write_text(remote, encoding='utf-8')
ssh = (ROOT/'scripts/build11_ssh.py').read_text(encoding='utf-8')
ssh = ssh.replace('20260922-build11', '20261006-build12').replace('modish-build11-server.tar', 'modish-build12-server.tar')
ssh = ssh.replace('scripts/build11_remote.py', 'scripts/build12_remote.py').replace("('build11-' + name)", "('build12-' + name)")
(ROOT/'scripts/build12_ssh.py').write_text(ssh, encoding='utf-8')
print(json.dumps({'archive_sha256': sha, 'files': len(manifest['files']), 'migration': False}))
