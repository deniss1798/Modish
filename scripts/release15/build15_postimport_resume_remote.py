"""One approved forward-only resume after the pinned, committed build15 import.

No SSH, downloads, migration, import, restore, replay or timer installation.
The previous smoke used a nonexistent /profile route; this worker verifies the
real protected /users/me route before consuming the original public-start tail.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
from time import monotonic
from types import SimpleNamespace

PLAN_ID = 'a4aa6774f26d469483e6c13cdb2186c5'
ROOT = Path('/opt/modish-releases/20261022-build15-activation-plan-' + PLAN_ID)
STATE = ROOT / 'activation-postimport-resume'
PRIOR = ROOT / 'activation-continuation'
PLAN_SHA = 'f5e49a2c04beca1c488f2cc4f014f3d0ba6c1fa276ee5a1a12224d97e724da5a'
PROXY_SHA = '7d0611e846af88231f42971bb06ab04631ce8e0fd3fa250f21bfd8550ca087f4'
BACKUP_SHA = '67bd59b07432ddda744c210976dd08be98f948a80af1757082608dfa354a6d36'
FAILURE_SHA = 'c699e85c6c42b588522299edf100a3662fe39eb87432caa822fb60e7432bbdfe'
COMMITTED_SHA = 'b0abe8ae9c11aa2802caf075cc2ecaff8aa85f175d6ef7afcd01a40f685d426a'
RENDER_ID = 'a18649cb87794b2b9f84c939134728be'
RENDER_SHA = '7ce4425bcf1573cc94905bc26ee1a8996bb026566c85f73c4821821d72e8ffdf'
CONTINUE_SHA = 'b97d6417019e2c59909c304efb23554d97be3910f472997749b93a3dd87c571e'
BACKUP_HELPER_SHA = '8b54caa30bf96036b8c9e28108794dc5ead29ce654b0491d0653fb2ee22acfba'
PROXY_HELPER_SHA = 'ae4e50a9260e6e0f955d17dd8b6b936772299c3744ab2196530b80650b30b2c6'
BASE_SHA = 'e1eeb23e6be60e5de4230c07313f115434f2ef03974909182ef9db0249e9fc44'
IMPORTER_SHA = 'b9bf37d9bb911f6354e7182922aa1d4acad78bc14f7d9f2f9632a4773ebc0688'
RUN = '9c4c8c7fe5a045068cde76d81df12104'
ARCHIVE = '5184f378245db0073154369f1b6e083c8d584cb9006788993cd4727866e26c24'
IMAGE = 'sha256:633bce227d3ce3277137c77f32f2c6e83bb338d91784803df424d7592a464aec'
MANIFEST = '5d2dde18bcb09cde948d044bc57d2178fd035aebc0698a0996d79dd5ab4ce23c'
APPROVAL = 'APPROVE_BUILD15_AND_FOUR_SOURCE_PRODUCTION_IMPORT'
COMMAND = ['python', '-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8000']
ROWS = {'baon': 9318, 'shoppinglive': 43402, 'serginnetti': 17065, 'postmeridiem': 378}
FEEDS = {'baon': '19982', 'shoppinglive': '25461', 'serginnetti': '26417', 'postmeridiem': '25200'}
CONT = BASE = UTIL = PROXY = PLAN = None
LOCK = None
STARTED = OPEN_ATTEMPTED = PUBLIC_CREATE_ATTEMPTED = False
IMPORT_PROOF_VERIFIED = False
OWNED = []


def require(value, code):
    if not value:
        raise RuntimeError(code)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def private(path, maximum=131072):
    info = path.lstat()
    require(path.is_file() and not path.is_symlink() and info.st_uid == 0
            and info.st_mode & 0o777 == 0o600 and info.st_nlink == 1
            and 0 < info.st_size <= maximum, 'private_bounded_input_required')
    return path.read_bytes()


def pinned_json(path, expected, maximum=131072):
    raw = private(path, maximum)
    require(hashlib.sha256(raw).hexdigest() == expected, 'exact_pinned_input_required')
    return json.loads(raw)


def load(path, expected, name):
    private(path)
    require(sha(path) == expected, 'frozen_helper_bytes_required')
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def valid_failure(value):
    require(value.get('operation') == 'build15_approved_production_activation'
            and value.get('status') == 'failed' and value.get('phase') == 'private_api_about_to_start'
            and value.get('error_type') == 'GuardError' and value.get('schema_migration_attempted') is True
            and value.get('import_attempted') is True and value.get('import_committed_verified') is True
            and value.get('import_outcome_uncertain_requires_review') is False
            and value.get('public_writes_may_exist') is False
            and value.get('owned_cleanup_requires_review') is False
            and value.get('automatic_retry') is False, 'exact_known_postimport_smoke_failure_required')


def valid_commit(value):
    require(value.get('scope') == 'build15_approved_four_source_production_import'
            and value.get('result') == 'production_import_committed'
            and value.get('plan_id') == PLAN_ID and value.get('plan_sha256') == PLAN_SHA
            and value.get('run_id') == RUN and value.get('archive_sha256') == ARCHIVE
            and value.get('image_id') == IMAGE and value.get('snapshot_manifest_sha256') == MANIFEST
            and value.get('single_transaction') is True and value.get('transaction_state') == 'committed'
            and value.get('needs_review') is False and value.get('existing_product_ids_preserved') is True
            and value.get('unaffected_rows_preserved') is True
            and value.get('committed_sources') == list(FEEDS)
            and len(value.get('sources', [])) == 4
            and {row['code']: row['rows'] for row in value['sources']} == ROWS,
            'durable_exact_committed_import_required')


def closed_history(directory):
    require(directory.is_dir() and not directory.is_symlink(), 'original_continuation_required')
    for index, path in enumerate(directory.rglob('*')):
        require(index < 5000 and not path.is_symlink(), 'bounded_regular_continuation_history_required')
        require('writes_open' not in path.name and path.name not in {'proxy-open-intent.json', 'activation-report.json'}
                and 'public_container_create' not in path.name and 'loopback_api' not in path.name
                and 'private_smoke_passed' not in path.name,
                'later_publication_history_forbids_this_resume')
    require(not (directory / 'production-import-outcome/failed.json').exists(),
            'failed_or_uncertain_import_forbids_resume')


def original_workers_stopped(rehearsal, feeds, importer):
    """Inspect, never start/stop/reuse, the three exact original workers."""
    raw = CONT.command('prior_workers_inventory', [*BASE.DOCKER, 'ps', '--all', '--no-trunc',
        '--filter', 'label=org.modish.activation_plan=' + PLAN_ID, '--format', '{{.ID}}'])
    identities = raw.decode().splitlines()
    require(len(identities) == 3 and len(set(identities)) == 3, 'exact_three_prior_workers_required')
    found = set()
    for index, identity in enumerate(identities):
        require(re.fullmatch(r'[0-9a-f]{64}', identity), 'immutable_prior_worker_id_required')
        obj = BASE.inspect(identity, 'prior_worker_' + str(index))
        kind = (obj['Config'].get('Labels') or {}).get('org.modish.activation_phase')
        require(kind in {'migration', 'import', 'private_api'} and kind not in found,
                'known_distinct_prior_worker_required')
        found.add(kind)
        mounts = []
        cmd, memory = COMMAND[1:], 1536 * 1024**2
        if kind == 'migration':
            cmd, memory = ['-m', 'alembic', 'upgrade', '20261015_0028'], 1024 * 1024**2
        elif kind == 'import':
            cmd = ['/run.py']
            mounts = [(str(importer), '/run.py', False),
                (str(ROOT / 'activation-plan.json'), '/plan/activation-plan.json', False),
                (str(feeds), '/feeds', False), (str(PRIOR / 'production-import-outcome'), '/outcome', True)]
        CONT.owned(obj, {'id': identity, 'name': 'modish_deploy15_' + kind + '_' + PLAN_ID,
            'phase': kind, 'cmd': cmd, 'memory_bytes': memory, 'mounts': mounts})
        require(obj['State']['Running'] is False and obj['State'].get('OOMKilled') is False
                and obj['State']['ExitCode'] == 0, 'prior_workers_must_be_cleanly_stopped')


def resume_owned(obj, item):
    """Separate namespace; frozen recovery's original worker inventory stays exact."""
    host, config = obj['HostConfig'], obj['Config']
    labels = config.get('Labels') or {}
    require(obj['Id'] == item['id'] and obj['Name'] == '/' + item['name'] and obj['Image'] == IMAGE
            and labels.get('org.modish.postimport_resume') == PLAN_ID
            and labels.get('org.modish.postimport_helper_sha256') == item['helper_sha256']
            and not labels.get('org.modish.activation_plan')
            and host.get('NetworkMode') == 'modish_default'
            and set(obj['NetworkSettings']['Networks']) in ({'modish_default'}, set())
            and not host.get('PortBindings') and not obj['Mounts']
            and config.get('Entrypoint') == ['python'] and config.get('Cmd') == COMMAND[1:]
            and dict(value.split('=', 1) for value in config.get('Env', [])) == item['environment'],
            'exact_owned_resume_private_api_required')
    require(host.get('NanoCpus') == 1000000000 and host.get('Memory') == 1536 * 1024**2
            and host.get('MemorySwap') == 1536 * 1024**2 and host.get('PidsLimit') == 128
            and host.get('ReadonlyRootfs') is True and host.get('Privileged') is False
            and not host.get('Devices') and not host.get('DeviceRequests')
            and host.get('PidMode', '') == '' and host.get('IpcMode') in {'private', ''}
            and host.get('RestartPolicy', {}).get('Name') == 'no'
            and 'ALL' in (host.get('CapDrop') or []) and not host.get('CapAdd')
            and any(value.startswith('no-new-privileges') for value in host.get('SecurityOpt', [])),
            'resume_private_resource_and_security_limits_required')
    return obj


def create_private(environment, image, helper_sha):
    name = 'modish_postimport15_private_' + PLAN_ID
    values = {**environment, 'PGOPTIONS': '-c default_transaction_read_only=on -c statement_timeout=30000 -c lock_timeout=3000'}
    path = CONT.envfile('resume-private-api.private.env', values)
    CONT.phase('resume_private_api_create_intent', no_published_ports=True, readonly_database=True)
    identity = CONT.command('create_resume_private_api', [*BASE.DOCKER, 'create', '--pull=never', '--name', name,
        '--cpus=1', '--memory=1536m', '--memory-swap=1536m', '--pids-limit=128', '--restart=no',
        '--cap-drop=ALL', '--security-opt=no-new-privileges', '--read-only', '--network=modish_default',
        '--env-file', str(path), '--entrypoint=python', '--label', 'org.modish.postimport_resume=' + PLAN_ID,
        '--label', 'org.modish.postimport_helper_sha256=' + helper_sha, IMAGE, *COMMAND[1:]]).decode().strip()
    require(re.fullmatch(r'[0-9a-f]{64}', identity), 'immutable_resume_worker_id_required')
    inherited = dict(value.split('=', 1) for value in image['Config'].get('Env', []))
    item = {'id': identity, 'name': name, 'environment': {**inherited, **values}, 'helper_sha256': helper_sha}
    OWNED.append(item)
    require(not resume_owned(BASE.inspect(identity, 'resume_worker_created'), item)['State']['Running'],
            'new_resume_worker_must_be_stopped')
    return item


SMOKE = r'''
import json,sys,time
from decimal import Decimal
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request,build_opener,ProxyHandler
opener=build_opener(ProxyHandler({}))
def fetch(path,method='GET'):
    try:response=opener.open(Request('http://127.0.0.1:8000'+path,method=method),timeout=3)
    except HTTPError as error:response=error
    with response:
        raw=response.read(524289);assert len(raw)<=524288
        return response.status,json.loads(raw)
for _ in range(20):
    try:
        status,body=fetch('/health/ready')
        if status==200 and body.get('version')=='1.0.1-build15' and body.get('status')=='ok' and body.get('database')=='ok':break
    except Exception:pass
    time.sleep(1)
else:raise RuntimeError('readiness_not_achieved')
status,body=fetch('/catalog/status');assert status==200 and body['status']=='ready' and body['personalized'] is False
status,brands=fetch('/products/brands?limit=10');assert status==200 and brands and all(r['brand'] and r['count']>0 for r in brands)
status,product=fetch('/products/'+quote(sys.argv[1],safe=''));assert status==200 and product['id']==sys.argv[1] and Decimal(str(product['price']))==Decimal(sys.argv[2])
checks={}
for path in ('/feed?limit=1','/catalog/discovery?limit=1','/users/me'):
    status,_=fetch(path);assert status==401;checks[path.split('?')[0]]=status
status,_=fetch('/visual-analysis','POST');assert status==503
print(json.dumps({'status':'passed','api_version':'1.0.1-build15','catalogue_status':'ready','brands':len(brands),'real_sku_price_exact':True,'unauthenticated':checks,'visual_disabled_status':503,'production_user_fixtures_created':False}))
'''


def smoke(item, stock, label):
    result = json.loads(CONT.command(label, [*BASE.DOCKER, 'exec', '-i', item['id'], 'python', '-',
        stock['id'], stock['price']], data=SMOKE.encode(), timeout=110))
    require(result.get('status') == 'passed' and result.get('api_version') == '1.0.1-build15'
            and result.get('real_sku_price_exact') is True and result.get('production_user_fixtures_created') is False
            and result.get('unauthenticated') == {'/feed': 401, '/catalog/discovery': 401, '/users/me': 401}
            and result.get('visual_disabled_status') == 503, 'correct_route_smoke_required')
    return result


def cleanup():
    end, review = monotonic() + 45, False
    for index, item in enumerate(OWNED):
        try:
            remaining = end - monotonic()
            require(remaining > 0, 'cleanup_budget_exhausted')
            obj = resume_owned(json.loads(BASE.run('resume_cleanup_inspect_' + str(index),
                [*BASE.DOCKER, 'inspect', item['id']], timeout=min(6, remaining), max_stdout=1024**2,
                max_stderr=131072))[0], item)
            if obj['State']['Running']:
                remaining = end - monotonic(); require(remaining > 0, 'cleanup_budget_exhausted')
                BASE.run('resume_cleanup_stop_' + str(index), [*BASE.DOCKER, 'stop', '--time', '5', item['id']],
                    timeout=min(8, remaining), max_stdout=131072, max_stderr=131072)
        except Exception:
            review = True
    return review


def main():
    global BASE, CONT, UTIL, PROXY, PLAN, LOCK, STARTED, OPEN_ATTEMPTED, PUBLIC_CREATE_ATTEMPTED, IMPORT_PROOF_VERIFIED
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approval-token', required=True)
    parser.add_argument('--expected-self-sha256', required=True)
    cli = parser.parse_args()
    require(os.name == 'posix' and os.geteuid() == 0 and cli.approval_token == APPROVAL,
            'approved_linux_root_required')
    private(Path(__file__))
    require(re.fullmatch(r'[0-9a-f]{64}', cli.expected_self_sha256)
            and sha(Path(__file__)) == cli.expected_self_sha256, 'externally_reviewed_resume_bytes_required')
    require(ROOT.is_dir() and not ROOT.is_symlink() and ROOT.resolve() == ROOT
            and ROOT.stat().st_uid == 0 and ROOT.stat().st_mode & 0o777 == 0o700,
            'fixed_private_plan_root_required')
    require(not STATE.exists() and not STATE.is_symlink()
            and not (ROOT / 'postimport-resume-started.json').exists()
            and not (ROOT / 'postimport-resume-started.json').is_symlink()
            and not (ROOT / 'prewrite-recovery-started.json').exists()
            and not (ROOT / 'prewrite-recovery-started.json').is_symlink(), 'new_one_shot_forward_resume_required')
    closed_history(PRIOR)
    valid_failure(pinned_json(PRIOR / 'activation-failed.json', FAILURE_SHA))
    committed = pinned_json(PRIOR / 'production-import-outcome/committed.json', COMMITTED_SHA)
    valid_commit(committed)
    IMPORT_PROOF_VERIFIED = True
    PLAN = pinned_json(ROOT / 'activation-plan.json', PLAN_SHA)
    contract = load(ROOT / 'catalog_refresh15_production_import.py', IMPORTER_SHA, 'resume_plan_contract')
    contract.validate_plan_value(PLAN)
    CONT = load(ROOT / 'build15_activation_continue_remote.py', CONTINUE_SHA, 'resume_frozen_continuation')
    UTIL = load(ROOT / 'build15_activation_final_backup_remote.py', BACKUP_HELPER_SHA, 'resume_frozen_backup')
    PROXY = load(ROOT / 'build15_proxy_maintenance_remote.py', PROXY_HELPER_SHA, 'resume_frozen_proxy')
    backup = pinned_json(ROOT / 'final-backup-attempt/final-backup-report.json', BACKUP_SHA)
    require(backup.get('status') == 'maintenance_final_backup_restore_verified' and backup.get('plan_id') == PLAN_ID
            and backup.get('plan_sha256') == PLAN_SHA and backup.get('restored_full_rows_preserved') is True
            and backup.get('source_rows_unchanged') is True and backup.get('table_count') == 31
            and backup.get('backend_stopped') is True and backup.get('writes_open') is False
            and backup.get('backend_auto_restart_disabled') is True
            and backup.get('proxy_plan_sha256') == PROXY_SHA, 'verified_original_final_backup_required')
    dump = ROOT / 'final-backup-attempt/final-production.dump'
    info = dump.lstat()
    require(dump.is_file() and not dump.is_symlink() and info.st_uid == 0 and info.st_mode & 0o777 == 0o600
            and info.st_nlink == 1 and info.st_size == backup['dump_bytes'] and sha(dump) == backup['dump_sha256'],
            'original_final_dump_unchanged_required')
    for name, field in (('candidate.docker-env.private', 'candidate_environment_sha256'),
            ('candidate.compose.private.json', 'candidate_override_sha256'),
            ('current-runtime.private.json', 'current_runtime_sha256'),
            ('current-compose.private.yml', 'current_compose_sha256')):
        private(ROOT / name, 1024**2)
        require(sha(ROOT / name) == PLAN[field], 'pinned_original_runtime_artifacts_required')
    require(sha(Path('/opt/Modish/docker-compose.yml')) == PLAN['current_compose_sha256'], 'live_compose_changed')
    old = json.loads(private(ROOT / 'current-runtime.private.json', 1024**2))
    pairs = [line.split('=', 1) for line in private(ROOT / 'candidate.docker-env.private').decode().splitlines()]
    environment = dict(pairs)
    require(len(pairs) == len(environment) and all(environment.get(k) == v for k, v in CONT.POLICY.items())
            and environment.get('JWT_SECRET') == dict(v.split('=', 1) for v in old['Config']['Env']).get('JWT_SECRET')
            and len(environment.get('JWT_SECRET', '')) >= 32 and not any(k.startswith('PG') for k in environment)
            and hashlib.sha256(environment['DATABASE_URL'].encode()).hexdigest() == PLAN.get('api_database_url_sha256')
            and PLAN.get('preserve_api_database_credentials') is True, 'preserved_exact_production_environment_required')
    contract.validate_database_url(environment['DATABASE_URL'])
    render_stage = ROOT / ('render-review-' + RENDER_ID)
    require(render_stage.is_dir() and not render_stage.is_symlink() and render_stage.resolve() == render_stage
            and render_stage.stat().st_uid == 0 and render_stage.stat().st_mode & 0o777 == 0o700,
            'original_private_render_stage_required')
    render = pinned_json(render_stage / 'render-report.json', RENDER_SHA)
    require(render.get('operation') == 'build15_readonly_resolved_runtime_proposal'
            and render.get('status') == 'prepared_for_review' and render.get('render_id') == RENDER_ID
            and render.get('plan_sha256') == PLAN_SHA and render.get('proxy_plan_sha256') == PROXY_SHA
            and render.get('continuation_sha256') == CONTINUE_SHA and render.get('image_id') == IMAGE
            and render.get('baseline_runtime_continuity_verified') is True
            and render.get('candidate_environment_sha256') == PLAN['candidate_environment_sha256']
            and render.get('current_runtime_sha256') == PLAN['current_runtime_sha256']
            and render.get('current_compose_sha256') == PLAN['current_compose_sha256']
            and render.get('public_api_host_ip') == '127.0.0.1' and render.get('public_api_port') == 8000,
            'original_reviewed_render_contract_required')
    raw_rendered = private(render_stage / 'rendered-original-ports.private.json', 1024**2)
    execution = private(render_stage / 'candidate-execution.compose.private.json', 1024**2)
    require(hashlib.sha256(raw_rendered).hexdigest() == render['raw_render_sha256']
            and hashlib.sha256(execution).hexdigest() == render['execution_compose_sha256'],
            'original_render_bytes_required')
    proxy_plan = PROXY.proposal(ROOT, PROXY_SHA, PLAN_SHA)
    fence = pinned_json(ROOT / 'final-backup-attempt/proxy-fence-report.json', backup['proxy_fence_report_sha256'])
    rehearsal = Path('/opt/modish-releases/20261021-catalog-refresh15-' + RUN)
    feeds = rehearsal / 'feeds'
    manifest = pinned_json(feeds / 'download-manifest.json', MANIFEST, 16384)
    require(manifest.get('run_id') == RUN and manifest.get('result') == 'download_complete'
            and manifest.get('observation_clock_basis') == 'real_download_request_start_utc'
            and len(manifest.get('sources', [])) == 4
            and {row['code'] for row in manifest['sources']} == set(FEEDS), 'fixed_complete_manifest_required')
    for item in manifest['sources']:
        stamp = contract.utc_stamp(item['observed_at'])
        now = datetime.now(timezone.utc)
        require(now - timedelta(hours=72) <= stamp <= now + timedelta(minutes=5)
                and item['feed_id'] == FEEDS[item['code']] and item['filename'] == item['code'] + '.csv',
                'original_observation_must_remain_fresh')
        path = feeds / item['filename']; info = path.lstat()
        require(path.is_file() and not path.is_symlink() and info.st_uid == 0 and info.st_mode & 0o777 == 0o600
                and info.st_nlink == 1 and info.st_size == item['bytes'] and sha(path) == item['sha256'],
                'original_snapshot_bytes_required')
    BASE = load(rehearsal / 'catalog_refresh15_clone_remote.py', BASE_SHA, 'resume_bounded_commands')
    LOCK = os.open('/opt/modish-releases/build15-activation.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(LOCK)
    require(info.st_uid == 0 and info.st_mode & 0o777 == 0o600 and info.st_nlink == 1, 'private_global_activation_lock_required')
    fcntl.flock(LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)
    # No production mutation has occurred above. This exclusive marker is never
    # removed, including on failure. The global flock excludes the old process.
    closed_history(PRIOR)
    require(not STATE.exists() and not STATE.is_symlink()
            and not (ROOT / 'postimport-resume-started.json').exists()
            and not (ROOT / 'postimport-resume-started.json').is_symlink()
            and not (ROOT / 'prewrite-recovery-started.json').exists()
            and not (ROOT / 'prewrite-recovery-started.json').is_symlink(), 'one_shot_resume_already_attempted')
    valid_failure(pinned_json(PRIOR / 'activation-failed.json', FAILURE_SHA))
    valid_commit(pinned_json(PRIOR / 'production-import-outcome/committed.json', COMMITTED_SHA))
    os.umask(0o077); STATE.mkdir(mode=0o700); PROXY.syncdir(ROOT)
    PROXY.json_new(ROOT / 'postimport-resume-started.json', {'plan_sha256': PLAN_SHA,
        'helper_sha256': cli.expected_self_sha256, 'failed_report_sha256': FAILURE_SHA,
        'committed_report_sha256': COMMITTED_SHA, 'automatic_retry': False,
        'at': datetime.now(timezone.utc).isoformat()})
    STARTED = True
    BASE.R = STATE
    client = STATE / 'docker-client'; client.mkdir(mode=0o700); BASE.json_file(client / 'config.json', {})
    BASE.DOCKER.extend(['--config', str(client)])
    args = SimpleNamespace(root=str(ROOT), plan_id=PLAN_ID, plan_sha256=PLAN_SHA,
        final_backup_report_sha256=BACKUP_SHA, proxy_plan_sha256=PROXY_SHA,
        render_id=RENDER_ID, render_report_sha256=RENDER_SHA, approval_token=APPROVAL)
    CONT.BASE, CONT.UTIL, CONT.PLAN, CONT.ROOT, CONT.STATE, CONT.ARGS = BASE, UTIL, PLAN, ROOT, STATE, args
    UTIL.BASE, UTIL.ARGS, UTIL.ROOT, UTIL.STATE = BASE, args, ROOT, STATE
    UTIL.DEADLINE = monotonic() + 3600
    CONT.phase('postimport_resume_preflight', import_replay=False, migration_replay=False, writes_open=False)
    for name in ('modish_postgres', 'modish_redis'):
        obj = BASE.inspect(name, 'resume_' + name)
        require(obj['Id'] == BASE.PROD[name][0] and obj['Image'] == BASE.PROD[name][1] and obj['State']['Running'],
                'original_database_and_redis_identity_required')
    current = CONT.old_stopped('resume_original_backend_stopped')
    expected_host = dict(old['HostConfig']); expected_host['RestartPolicy'] = {'Name': 'no', 'MaximumRetryCount': 0}
    require(current['Config'] == old['Config'] and current['Mounts'] == old['Mounts']
            and current['HostConfig'] == expected_host, 'unchanged_stopped_original_backend_required')
    PROXY.assert_fenced(BASE, ROOT, PROXY_SHA, PLAN_SHA, fence)
    original_workers_stopped(rehearsal, feeds, ROOT / 'catalog_refresh15_production_import.py')
    UTIL.assert_no_other_sessions(); CONT.schema0028()
    clocks = json.loads(UTIL.readonly_sql("SELECT json_object_agg(code,last_sync_at) FROM product_sources WHERE "
        "code IN ('baon','shoppinglive','serginnetti','postmeridiem');"))
    require(all(contract.utc_stamp(clocks[row['code']]) == contract.utc_stamp(row['observed_at'])
                for row in manifest['sources']), 'persisted_committed_observations_required')
    image = json.loads(CONT.command('resume_image', [*BASE.DOCKER, 'image', 'inspect', IMAGE]))[0]
    BASE.require_image(image, IMAGE, RUN)
    require(image['Config'].get('Entrypoint') in ([], None) and image['Config'].get('Cmd') == COMMAND,
            'original_direct_uvicorn_image_required')
    rendered = json.loads(raw_rendered); CONT.validate_rendered(rendered, old, environment)
    rendered['services']['backend']['ports'] = [proxy_plan['public_api_binding']]
    desired_ports = {('8000/tcp', '127.0.0.1', '8000')}
    require(CONT.port_map(rendered['services']['backend']['ports']) == desired_ports
            and json.loads(execution) == CONT.compose_literals(rendered), 'original_loopback_execution_render_required')
    execution_compose = STATE / 'resolved-execution.compose.private.json'
    with execution_compose.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600); stream.write(execution); stream.flush(); os.fsync(stream.fileno())
    execution_sha = sha(execution_compose)
    stock = json.loads(UTIL.readonly_sql("SELECT json_build_object('count',count(*),'id',(array_agg(id ORDER BY "
        "CASE WHEN price<>trunc(price) THEN 0 ELSE 1 END,id))[1],'price',(array_agg(price::text ORDER BY "
        "CASE WHEN price<>trunc(price) THEN 0 ELSE 1 END,id))[1]) FROM products WHERE is_active=1 AND is_available=1 "
        "AND is_deleted_from_feed=0 AND source IN ('baon','shoppinglive','serginnetti','postmeridiem') "
        "AND availability_status IN ('in_stock','available') AND last_seen_in_feed_at>=now()-interval '72 hours' "
        "AND last_seen_in_feed_at<=now()+interval '5 minutes';"))
    require(stock['count'] > 0 and isinstance(stock['id'], str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', stock['id']),
            'real_fresh_committed_stock_required')
    proof_before = UTIL.source_integrity(); require(len(proof_before) == 31, 'all_31_table_proofs_required')
    CONT.record('postimport-full-rows-before', proof_before)
    shadow = create_private(environment, image, cli.expected_self_sha256)
    CONT.phase('resume_private_api_about_to_start', no_published_ports=True, readonly_database=True)
    CONT.command('resume_private_start', [*BASE.DOCKER, 'start', shadow['id']])
    resume_owned(BASE.inspect(shadow['id'], 'resume_private_running'), shadow)
    smoke(shadow, stock, 'resume_corrected_private_smoke')
    require(UTIL.source_integrity() == proof_before, 'readonly_smoke_changed_committed_database')
    CONT.command('resume_private_stop', [*BASE.DOCKER, 'stop', '--time', '15', shadow['id']], timeout=30)
    obj = resume_owned(BASE.inspect(shadow['id'], 'resume_private_stopped'), shadow)
    require(not obj['State']['Running'] and obj['State']['ExitCode'] == 0 and obj['State'].get('OOMKilled') is False,
            'resume_private_must_stop_cleanly')
    UTIL.assert_no_other_sessions(); CONT.old_stopped('resume_before_public_create')
    PROXY.assert_fenced(BASE, ROOT, PROXY_SHA, PLAN_SHA, fence)
    closed_history(PRIOR)
    CONT.phase('corrected_private_smoke_passed', protected_profile_route='/users/me', all_31_rows_unchanged=True)
    # The remainder follows the exact original create/start/open sequence. It
    # never calls the previous main(), migration or importer entry point.
    CONT.phase('public_container_create_about_to_start', writes_open=False)
    PUBLIC_CREATE_ATTEMPTED = True
    require(sha(execution_compose) == execution_sha, 'execution_compose_changed')
    CONT.command('resume_compose_create_backend', [*BASE.DOCKER, 'compose', '--project-name', 'modish',
        '--project-directory', '/opt/Modish', '-f', str(execution_compose),
        'up', '--no-start', '--no-deps', '--no-build', 'backend'], timeout=120)
    new = BASE.inspect('modish_backend', 'resume_public_created')
    inherited = dict(value.split('=', 1) for value in image['Config'].get('Env', []))
    require(new['Id'] != BASE.PROD['modish_backend'][0] and new['Image'] == IMAGE and not new['State']['Running']
            and new['Config'].get('Entrypoint') in ([], None) and new['Config'].get('Cmd') == COMMAND
            and dict(value.split('=', 1) for value in new['Config']['Env']) == {**inherited, **environment}
            and CONT.runtime_ports(new) == desired_ports
            and {(m['Type'], m['Source'], m['Destination'], m['RW']) for m in new['Mounts']}
                == {(m['Type'], m['Source'], m['Destination'], m['RW']) for m in old['Mounts']}
            and set(new['NetworkSettings']['Networks']) == {'modish_default'}, 'reviewed_stopped_public_runtime_required')
    labels = new['Config'].get('Labels') or {}
    require(labels.get('com.docker.compose.project') == 'modish' and labels.get('com.docker.compose.service') == 'backend',
            'original_compose_public_identity_required')
    CONT.schema0028(); UTIL.assert_no_other_sessions()
    PROXY.assert_fenced(BASE, ROOT, PROXY_SHA, PLAN_SHA, fence)
    CONT.phase('loopback_api_about_to_start', new_backend_id=new['Id'], writes_open=False)
    CONT.command('resume_fenced_public_start', [*BASE.DOCKER, 'start', new['Id']], timeout=60)
    public = BASE.inspect(new['Id'], 'resume_public_running')
    require(public['Id'] == new['Id'] and public['Image'] == IMAGE and public['State']['Running'], 'candidate_public_must_run')
    post = smoke({'id': new['Id']}, stock, 'resume_corrected_public_smoke')
    require(UTIL.source_integrity() == proof_before, 'public_smoke_changed_committed_database')
    PROXY.assert_fenced(BASE, ROOT, PROXY_SHA, PLAN_SHA, fence)
    # Mirror only a NEW safety marker into the original history. The frozen
    # recovery sees writes_open and must refuse restoration after this point.
    OPEN_ATTEMPTED = True
    PROXY.json_new(PRIOR / 'postimport-resume-writes_open-intent.json', {'plan_sha256': PLAN_SHA,
        'resume_helper_sha256': cli.expected_self_sha256, 'backend_id': new['Id'],
        'public_writes_may_exist_after_this_marker': True, 'at': datetime.now(timezone.utc).isoformat()})
    CONT.phase('writes_open_about_to_start', new_backend_id=new['Id'], public_writes_may_exist_after_this_marker=True)
    PROXY.open_after_smoke(BASE, STATE, ROOT, PROXY_SHA, PLAN_SHA, fence, APPROVAL)
    result = {'operation': 'build15_approved_postimport_forward_resume', 'status': 'activated_and_smoke_verified',
        'plan_id': PLAN_ID, 'plan_sha256': PLAN_SHA, 'run_id': RUN, 'image_id': IMAGE,
        'archive_sha256': ARCHIVE, 'manifest_sha256': MANIFEST, 'schema': '20261015_0028',
        'prior_failure_sha256': FAILURE_SHA, 'committed_import_report_sha256': COMMITTED_SHA,
        'final_backup_report_sha256': BACKUP_SHA, 'resume_helper_sha256': cli.expected_self_sha256,
        'executed_compose_sha256': execution_sha, 'preapproval_render_report_sha256': RENDER_SHA,
        'proxy_plan_sha256': PROXY_SHA, 'backend_id': new['Id'], 'api_version': post['api_version'],
        'api_published_only_to_loopback': True, 'writes_open': True, 'jwt_preserved': True,
        'input_rows': sum(ROWS.values()), 'sources': 4, 'all_31_rows_unchanged_during_smokes': True,
        'migration_replayed': False, 'import_replayed': False, 'timer_installed': False,
        'automatic_retry': False, 'automatic_old_api_rollback_allowed': False,
        'finished_at': datetime.now(timezone.utc).isoformat()}
    CONT.record('activation-report', result)
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        review = cleanup() if BASE is not None and OWNED else False
        result = {'operation': 'build15_approved_postimport_forward_resume', 'status': 'failed',
            'phase': CONT.PHASE if CONT is not None else 'arguments', 'error_type': type(error).__name__,
            'raw_error_omitted': True, 'requires_review': True, 'import_committed_verified': IMPORT_PROOF_VERIFIED,
            'migration_replayed': False, 'import_replayed': False, 'public_create_attempted': PUBLIC_CREATE_ATTEMPTED,
            'public_writes_may_exist': OPEN_ATTEMPTED, 'owned_cleanup_requires_review': review,
            'automatic_retry': False, 'automatic_restore_or_old_api_start': False, 'artifacts_preserved': True}
        if type(error) is RuntimeError and re.fullmatch(r'[a-z][a-z0-9_]{0,100}', str(error)):
            result['check'] = str(error)
        if STARTED:
            try:
                CONT.record('activation-failed', result)
            except Exception:
                result['ledger_write_requires_review'] = True
        print(json.dumps(result)); raise SystemExit(1) from None
    finally:
        if LOCK is not None:
            os.close(LOCK)
