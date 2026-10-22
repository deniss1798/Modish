"""Prepare an exact private nginx proposal; apply/open are approval-only functions.

The CLI ONLY reads known nginx files and writes new private proposal files.
It never installs configuration, reloads nginx, creates a live flag or opens API.
Activation callers must pin this helper, proposal SHA, original config and plan.
"""
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from time import monotonic, sleep

SITE = Path('/etc/nginx/sites-enabled/modish')
MAIN = Path('/etc/nginx/nginx.conf')
SITE_SHA = 'baf24d87e4812dc9a0d2aff59d5a5ce32dcd1d8b5f6bc65455aeb2aa551799cb'
MAIN_SHA = '48c6a4ec1e1fd28ccf968490f07e34a1d7f755793b2108a3ed8670b1ee2a0aa2'
FLAG = Path('/etc/nginx/modish-build15-maintenance.flag')
DOMAIN = 'modish.org.ru'
APPROVAL = 'APPROVE_BUILD15_AND_FOUR_SOURCE_PRODUCTION_IMPORT'
COUNTER = 0


def require(value, code):
    if not value:
        raise RuntimeError(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def syncdir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path, raw, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    syncdir(path.parent)


def json_new(path, value):
    write_new(path, (json.dumps(value, sort_keys=True, indent=2) + '\n').encode())


def regular(path, maximum=262144, private=False):
    require(path.is_file() and not path.is_symlink() and path.stat().st_uid == 0
            and 0 < path.stat().st_size <= maximum and path.stat().st_mode & 0o022 == 0,
            'root_owned_regular_configuration_required')
    if private:
        require(path.stat().st_mode & 0o777 == 0o600, 'private_configuration_mode_required')
    return path.read_bytes()


def resolved_site():
    target = SITE.resolve(strict=True)
    require(target in {SITE, Path('/etc/nginx/sites-available/modish')}, 'known_nginx_site_target_required')
    require(target.parent.resolve() == target.parent and target.parent.stat().st_uid == 0
            and target.parent.stat().st_mode & 0o022 == 0, 'root_owned_site_directory_required')
    return target


def proposal(root, expected, plan_sha):
    raw = regular(root / 'proxy-plan.json', private=True)
    require(digest(raw) == expected, 'pinned_proxy_plan_required')
    value = json.loads(raw)
    require(value.get('operation') == 'build15_exact_nginx_maintenance_proposal'
            and value.get('activation_plan_sha256') == plan_sha and value.get('site_sha256') == SITE_SHA
            and value.get('nginx_main_sha256') == MAIN_SHA and value.get('maintenance_flag') == str(FLAG)
            and value.get('tls_domain') == DOMAIN
            and value.get('public_api_binding') == {'target': 8000, 'published': '8000', 'host_ip': '127.0.0.1', 'protocol': 'tcp'},
            'exact_proxy_policy_required')
    require(str(resolved_site()) == value['canonical_site_path'] and digest(regular(MAIN)) == MAIN_SHA,
            'nginx_configuration_identity_changed')
    require(digest(regular(root / 'nginx-site.proposed.private.conf', private=True)) == value['proposed_site_sha256']
            and digest(regular(root / 'nginx-site.original.private.conf', private=True)) == SITE_SHA
            and digest(regular(root / 'nginx-main.test.private.conf', private=True)) == value['private_test_main_sha256'],
            'private_proxy_artifacts_changed')
    return value


def run(base, label, argv, **options):
    global COUNTER
    COUNTER += 1
    return base.run('proxy_' + str(COUNTER).zfill(4) + '_' + label, argv, **options)


def tls_probe(base, expected):
    # Connect only to loopback but preserve actual SNI/Host and certificate
    # verification. No DNS/proxy/external connection or authorization is used.
    source = r'''
import http.client,json,socket,ssl,sys
sock=socket.create_connection(('127.0.0.1',443),timeout=5)
sock=ssl.create_default_context().wrap_socket(sock,server_hostname='modish.org.ru')
connection=http.client.HTTPConnection('modish.org.ru',443,timeout=5);connection.sock=sock
try:
    connection.request('GET','/health/ready',headers={'Host':'modish.org.ru'})
    response=connection.getresponse();raw=response.read(65537)
    assert len(raw)<=65536
    assert response.status==int(sys.argv[1])
    if response.status==200:
        value=json.loads(raw);assert value.get('database')=='ok' and value.get('status')=='ok' and value.get('version')=='1.0.1-build15'
    print(json.dumps({'status_code':response.status,'loopback_only':True,'tls_verified':True}))
finally:connection.close()
'''
    value = json.loads(run(base, 'tls_probe', ['python3', '-', str(expected)], data=source.encode(), timeout=20))
    require(value == {'status_code': expected, 'loopback_only': True, 'tls_verified': True}, 'loopback_tls_proxy_probe_required')
    return value


def replace_site(target, raw, mode, plan_id):
    temporary = target.parent / ('.modish-build15-' + plan_id + '.temporary')
    write_new(temporary, raw, mode)
    os.replace(temporary, target)
    syncdir(target.parent)


def nginx_processes(proc_root=Path('/proc')):
    result, inspected = [], 0
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        inspected += 1
        require(inspected <= 4096, 'bounded_nginx_process_inventory_required')
        try:
            with (entry / 'comm').open('r') as stream:
                comm = stream.read(65).strip()
        except (FileNotFoundError, ProcessLookupError):
            continue
        if comm == 'nginx':
            result.append(int(entry.name))
    return result


def restart_fenced_nginx(base):
    # A graceful reload can leave workers with the old proxy configuration and
    # keepalive connections. A controlled stop -> verified zero nginx processes
    # -> start closes those routes before any candidate API is exposed.
    run(base, 'stop_nginx_for_fence', ['systemctl', 'stop', 'nginx'], timeout=40)
    until = monotonic() + 10
    while nginx_processes():
        require(monotonic() < until, 'old_nginx_processes_still_alive')
        sleep(0.2)
    run(base, 'start_nginx_fenced', ['systemctl', 'start', 'nginx'], timeout=30)


def apply_fence(base, state, root, proxy_sha, plan_sha, approval_token):
    require(approval_token == APPROVAL, 'explicit_production_approval_required')
    value = proposal(root, proxy_sha, plan_sha)
    target = resolved_site()
    require(digest(regular(target)) == SITE_SHA and not FLAG.exists() and not FLAG.is_symlink(),
            'new_maintenance_attempt_and_original_site_required')
    # Syntax-check private proposed configuration before changing live files.
    run(base, 'test_private', ['nginx', '-t', '-c', str(root / 'nginx-main.test.private.conf')], timeout=20)
    json_new(state / 'proxy-fence-intent.json', {'proxy_plan_sha256': proxy_sha, 'activation_plan_sha256': plan_sha,
        'flag': str(FLAG), 'live_site_change_about_to_start': True})
    write_new(FLAG, b'', 0o644)
    proposed = regular(root / 'nginx-site.proposed.private.conf', private=True)
    replace_site(target, proposed, value['site_mode'], value['plan_id'])
    try:
        run(base, 'test_live', ['nginx', '-t'], timeout=20)
    except Exception:
        # No reload was attempted. Restore only our exact installed bytes; a
        # concurrent change is never overwritten. Leave the own flag for review.
        if digest(regular(target)) == value['proposed_site_sha256']:
            replace_site(target, regular(root / 'nginx-site.original.private.conf', private=True),
                         value['site_mode'], value['plan_id'] + '-restore')
        raise
    restart_fenced_nginx(base)
    tls_probe(base, 503)
    info = FLAG.stat()
    report = {'operation': 'build15_nginx_fence_verified', 'proxy_plan_sha256': proxy_sha,
        'activation_plan_sha256': plan_sha, 'flag_device': info.st_dev, 'flag_inode': info.st_ino,
        'site_sha256': value['proposed_site_sha256'], 'tls_maintenance_status': 503,
        'old_nginx_connections_closed': True, 'nginx_zero_processes_before_restart_verified': True,
        'verified_at': datetime.now(timezone.utc).isoformat()}
    json_new(state / 'proxy-fence-report.json', report)
    return report


def assert_fenced(base, root, proxy_sha, plan_sha, fence):
    value = proposal(root, proxy_sha, plan_sha)
    require(fence.get('operation') == 'build15_nginx_fence_verified'
            and fence.get('proxy_plan_sha256') == proxy_sha and fence.get('activation_plan_sha256') == plan_sha,
            'matching_proxy_fence_report_required')
    require(fence.get('old_nginx_connections_closed') is True
            and fence.get('nginx_zero_processes_before_restart_verified') is True,
            'old_nginx_workers_must_be_proven_closed')
    require(FLAG.is_file() and not FLAG.is_symlink() and FLAG.stat().st_uid == 0
            and FLAG.stat().st_mode & 0o777 == 0o644 and FLAG.stat().st_size == 0
            and (FLAG.stat().st_dev, FLAG.stat().st_ino) == (fence['flag_device'], fence['flag_inode'])
            and digest(regular(resolved_site())) == value['proposed_site_sha256'], 'persistent_nginx_fence_changed')
    tls_probe(base, 503)


def open_after_smoke(base, state, root, proxy_sha, plan_sha, fence, approval_token):
    require(approval_token == APPROVAL, 'explicit_production_approval_required')
    assert_fenced(base, root, proxy_sha, plan_sha, fence)
    json_new(state / 'proxy-open-intent.json', {'public_writes_may_exist_after_this_marker': True,
        'proxy_plan_sha256': proxy_sha, 'activation_plan_sha256': plan_sha})
    FLAG.unlink(); syncdir(FLAG.parent)
    return tls_probe(base, 200)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True); parser.add_argument('--plan-sha256', required=True)
    args = parser.parse_args()
    require(os.name == 'posix' and os.geteuid() == 0, 'linux_root_required')
    root = Path(args.root)
    require(re.fullmatch(r'/opt/modish-releases/20261022-build15-activation-plan-[0-9a-f]{32}', str(root))
            and root.is_dir() and not root.is_symlink() and root.resolve() == root
            and root.stat().st_uid == 0 and root.stat().st_mode & 0o777 == 0o700, 'private_existing_plan_root_required')
    plan_raw = regular(root / 'activation-plan.json', private=True)
    require(re.fullmatch(r'[0-9a-f]{64}', args.plan_sha256) and digest(plan_raw) == args.plan_sha256,
            'pinned_activation_plan_required')
    plan = json.loads(plan_raw)
    require(plan['operation'] == 'build15_production_activation_plan' and root.name.endswith(plan['plan_id']),
            'matching_activation_plan_identity_required')
    original, main_raw = regular(resolved_site()), regular(MAIN)
    require(digest(original) == SITE_SHA and digest(main_raw) == MAIN_SHA, 'original_nginx_hashes_required')
    require(not FLAG.exists() and not FLAG.is_symlink(), 'no_existing_maintenance_flag_required')
    anchor = b'    ssl_certificate /etc/letsencrypt/live/modish.org.ru/fullchain.pem;'
    require(original.count(anchor) == 1, 'exact_ssl_server_anchor_required')
    proposed = original.replace(anchor, b'    if (-f /etc/nginx/modish-build15-maintenance.flag) { return 503; }\n\n' + anchor, 1)
    include = b'include /etc/nginx/sites-enabled/*;'
    require(main_raw.count(include) == 1, 'exact_enabled_sites_include_required')
    test_main = main_raw.replace(include, b'include ' + str(root / 'nginx-site.proposed.private.conf').encode() + b';', 1)
    for name, raw in (('nginx-site.original.private.conf', original), ('nginx-site.proposed.private.conf', proposed),
                       ('nginx-main.test.private.conf', test_main)):
        write_new(root / name, raw)
    value = {'operation': 'build15_exact_nginx_maintenance_proposal', 'plan_id': plan['plan_id'],
        'activation_plan_sha256': args.plan_sha256, 'site_sha256': SITE_SHA, 'nginx_main_sha256': MAIN_SHA,
        'canonical_site_path': str(resolved_site()), 'site_mode': resolved_site().stat().st_mode & 0o777,
        'proposed_site_sha256': digest(proposed), 'private_test_main_sha256': digest(test_main),
        'maintenance_flag': str(FLAG), 'tls_domain': DOMAIN, 'upstream': 'http://127.0.0.1:8000',
        'public_api_binding': {'target': 8000, 'published': '8000', 'host_ip': '127.0.0.1', 'protocol': 'tcp'},
        'approval_required': True, 'production_changed': False}
    json_new(root / 'proxy-plan.json', value)
    print(json.dumps({'operation': 'private_nginx_proposal_only', 'status': 'prepared_for_review',
        'plan_id': plan['plan_id'], 'proxy_plan_sha256': digest((root / 'proxy-plan.json').read_bytes()),
        'production_changed': False, 'live_flag_created': False, 'nginx_reloaded': False}))


if __name__ == '__main__':
    try: main()
    except Exception as error:
        value = {'operation': 'private_nginx_proposal_only', 'status': 'failed',
                 'error_type': type(error).__name__, 'production_changed': False, 'raw_error_omitted': True}
        if type(error) is RuntimeError and re.fullmatch(r'[a-z][a-z0-9_]{0,100}', str(error)):
            value['check'] = str(error)
        print(json.dumps(value)); raise SystemExit(1) from None
