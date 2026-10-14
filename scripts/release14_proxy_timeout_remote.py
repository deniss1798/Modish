"""Align the verified Modish proxy response timeout with photo deadline180s.

Only one exact 30s read-timeout directive is changed to240s. The backup stays on
the VPS. Apply requires the prepared plan hash; failed validation/reload restores
only this file, never data, and does not overwrite another operator's edits.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import urllib.request
from uuid import uuid4

R = Path('/opt/modish-releases/20261014-build14')
ENABLED = Path('/etc/nginx/sites-enabled/modish')
ALLOWED = (ENABLED, Path('/etc/nginx/sites-available/modish'))
PRIVATE = R / 'proxy-private'
PLAN = R / 'proxy-timeout-plan.json'


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def capture(*args):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout


def write_json(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def candidate(data):
    require(len(data) <= 65536 and b'\0' not in data, 'bounded_text_config_required')
    require(re.search(rb'^\s*server_name\s+[^;]*\bmodish\.org\.ru\b[^;]*;', data, re.M), 'modish_host_required')
    require(re.search(rb'^\s*proxy_pass\s+http://127\.0\.0\.1:8000/?\s*;', data, re.M), 'local_modish_upstream_required')
    directives = re.findall(rb'^\s*proxy_read_timeout\s+([^;]+);', data, re.M)
    require(directives == [b'30s'], 'single_existing_read_timeout30_required')
    changed, count = re.subn(rb'(^\s*proxy_read_timeout\s+)30s(\s*;)', rb'\g<1>240s\2', data, flags=re.M)
    require(count == 1, 'one_read_timeout_change_required')
    return changed


def check_scope():
    require(R.is_dir() and R.resolve() == R and not R.is_symlink(), 'fixed_release_directory')
    target = ENABLED.resolve(strict=True)
    require(target in ALLOWED and target.is_file() and not target.is_symlink(), 'fixed_modish_config_target')
    return target


def plan():
    target = check_scope()
    capture('nginx', '-t')
    original = target.read_bytes()
    changed = candidate(original)
    require(not PRIVATE.exists() and not PLAN.exists(), 'immutable_proxy_plan_exists')
    PRIVATE.mkdir(mode=0o700)
    for name, data in (('original.conf', original), ('candidate.conf', changed)):
        with (PRIVATE / name).open('xb') as stream:
            stream.write(data)
    info = target.stat()
    value = {'status': 'reviewable_proxy_timeout_plan', 'created_at': datetime.now(timezone.utc).isoformat(),
             'target': str(target), 'original_sha256': digest(original), 'candidate_sha256': digest(changed),
             'script_sha256': digest(Path(__file__).read_bytes()), 'changed_directives': 1,
             'read_timeout_before_seconds': 30, 'read_timeout_after_seconds': 240,
             'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid, 'gid': info.st_gid,
             'production_changed': False, 'backup': 'proxy-private/original.conf'}
    write_json(PLAN, value)
    print(json.dumps({'status': value['status'], 'plan_sha256': digest(PLAN.read_bytes()), 'production_changed': False}), flush=True)


def atomic(target, data, expected, metadata):
    require(not target.is_symlink() and digest(target.read_bytes()) == expected, 'proxy_config_changed_by_operator')
    temporary = target.with_name('.modish-build14-' + uuid4().hex + '.tmp')
    with temporary.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(metadata['mode'])
    os.chown(temporary, metadata['uid'], metadata['gid'])
    require(digest(target.read_bytes()) == expected, 'proxy_config_changed_during_write')
    os.replace(temporary, target)


def apply(expected):
    require(digest(PLAN.read_bytes()) == expected, 'exact_proxy_plan_required')
    value = json.loads(PLAN.read_text())
    require(value['script_sha256'] == digest(Path(__file__).read_bytes()), 'proxy_helper_changed')
    target = check_scope()
    require(str(target) == value['target'], 'proxy_target_changed')
    original, changed = ((PRIVATE / name).read_bytes() for name in ('original.conf', 'candidate.conf'))
    require(digest(original) == value['original_sha256'] and digest(changed) == value['candidate_sha256']
            and candidate(original) == changed, 'prepared_proxy_bytes_changed')
    write_json(R / 'proxy-timeout-started.json', {'plan_sha256': expected})
    atomic(target, changed, value['original_sha256'], value)
    try:
        capture('nginx', '-t')
        capture('nginx', '-s', 'reload')
        with urllib.request.urlopen('https://modish.org.ru/health', timeout=20) as response:
            body = json.load(response)
            require(response.status == 200 and body.get('database') == 'ok', 'proxy_health_failed')
        require(target.read_bytes() == changed, 'proxy_config_changed_after_reload')
    except Exception as error:
        atomic(target, original, value['candidate_sha256'], value)
        capture('nginx', '-t')
        capture('nginx', '-s', 'reload')
        write_json(R / 'proxy-timeout-failure.json', {'status': 'restored_original_config', 'error_type': type(error).__name__})
        raise RuntimeError('proxy_update_failed_original_restored') from None
    result = {'status': 'proxy_timeout_updated', 'finished_at': datetime.now(timezone.utc).isoformat(),
              'plan_sha256': expected, 'read_timeout_seconds': 240, 'nginx_test': 'passed', 'public_health': 200,
              'changed_directives': 1, 'backup_preserved': True, 'catalogue_or_schema_modified': False}
    write_json(R / 'proxy-timeout-record.json', result)
    print(json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('plan', 'apply'))
    parser.add_argument('--plan-sha256')
    args = parser.parse_args()
    os.umask(0o077)
    if args.mode == 'plan':
        plan()
    else:
        require(re.fullmatch(r'[0-9a-f]{64}', args.plan_sha256 or ''), 'proxy_plan_hash_required')
        apply(args.plan_sha256)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__,
                          'guard': str(error) if isinstance(error, RuntimeError) else None}), flush=True)
        raise SystemExit(1) from None
