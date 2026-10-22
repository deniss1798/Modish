"""Prepare a frozen runtime proposal before approval; no live changes.

Only inspect/image inspect and docker compose config are executed. No container
create/start/stop/build, DB connection, migration, import or nginx operation.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
from uuid import UUID

BASE_SHA = 'e1eeb23e6be60e5de4230c07313f115434f2ef03974909182ef9db0249e9fc44'
PROXY_SHA = 'ae4e50a9260e6e0f955d17dd8b6b936772299c3744ab2196530b80650b30b2c6'
RUN = '9c4c8c7fe5a045068cde76d81df12104'
IMAGE = 'sha256:633bce227d3ce3277137c77f32f2c6e83bb338d91784803df424d7592a464aec'


def require(value, code):
    if not value:
        raise RuntimeError(code)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path, maximum=1048576):
    require(path.is_file() and not path.is_symlink() and path.stat().st_uid == 0
            and path.stat().st_mode & 0o777 == 0o600 and path.stat().st_size <= maximum,
            'private_bounded_input_required')
    return path.read_bytes()


def load(path, expected, name):
    require(path.is_file() and not path.is_symlink() and sha(path) == expected, 'reviewed_render_module_required')
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'plan-sha256', 'proxy-plan-sha256', 'continuation-sha256', 'render-id'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    require(os.name == 'posix' and os.geteuid() == 0, 'linux_root_required')
    require(all(re.fullmatch(r'[0-9a-f]{64}', value) for value in
        (args.plan_sha256, args.proxy_plan_sha256, args.continuation_sha256))
        and re.fullmatch(r'[0-9a-f]{32}', args.render_id) and UUID(args.render_id).version == 4,
        'immutable_render_inputs_required')
    root = Path(args.root)
    require(re.fullmatch(r'/opt/modish-releases/20261022-build15-activation-plan-[0-9a-f]{32}', str(root))
            and root.is_dir() and not root.is_symlink() and root.resolve() == root
            and root.stat().st_uid == 0 and root.stat().st_mode & 0o777 == 0o700, 'private_plan_root_required')
    raw = read(root / 'activation-plan.json', 131072)
    require(hashlib.sha256(raw).hexdigest() == args.plan_sha256, 'pinned_activation_plan_required')
    plan = json.loads(raw)
    require(root.name.endswith(plan['plan_id']) and plan['run_id'] == RUN and plan['image_id'] == IMAGE,
            'matching_immutable_plan_required')
    for name, field in (('candidate.docker-env.private', 'candidate_environment_sha256'),
                        ('candidate.compose.private.json', 'candidate_override_sha256'),
                        ('current-runtime.private.json', 'current_runtime_sha256'),
                        ('current-compose.private.yml', 'current_compose_sha256')):
        read(root / name)
        require(sha(root / name) == plan[field], 'retained_runtime_hash_required')
    require(sha(Path('/opt/Modish/docker-compose.yml')) == plan['current_compose_sha256'], 'live_compose_changed')
    continuation = load(root / 'build15_activation_continue_remote.py', args.continuation_sha256, 'build15_render_functions')
    proxy = load(root / 'build15_proxy_maintenance_remote.py', PROXY_SHA, 'build15_proxy_proposal_contract')
    proxy_plan = proxy.proposal(root, args.proxy_plan_sha256, args.plan_sha256)
    environment = {}
    for line in read(root / 'candidate.docker-env.private').decode().splitlines():
        key, value = line.split('=', 1)
        require(key not in environment, 'unique_runtime_environment_required')
        environment[key] = value
    old = json.loads(read(root / 'current-runtime.private.json'))
    stage = root / ('render-review-' + args.render_id)
    require(not stage.exists(), 'new_render_review_id_required')
    os.umask(0o077); stage.mkdir(mode=0o700); proxy.syncdir(root)
    base = load(Path('/opt/modish-releases/20261021-catalog-refresh15-' + RUN) / 'catalog_refresh15_clone_remote.py',
                BASE_SHA, 'build15_render_metadata_utilities')
    base.R = stage
    client = stage / 'docker-client'; client.mkdir(mode=0o700); base.json_file(client / 'config.json', {})
    base.DOCKER.extend(['--config', str(client)])
    before = base.production_snapshot('render_production_before')
    actual = base.inspect('modish_backend', 'render_backend_actual')
    require(actual['Config'] == old['Config'] and actual['HostConfig'] == old['HostConfig']
            and actual['Mounts'] == old['Mounts'], 'live_runtime_changed_since_plan')
    image = json.loads(base.run('render_image', [*base.DOCKER, 'image', 'inspect', IMAGE]))[0]
    base.require_image(image, IMAGE, RUN)
    continuation.BASE, continuation.ROOT = base, root
    raw = base.run('render_compose_only', [*continuation.compose_argv(), 'config', '--format', 'json'],
                   timeout=40, max_stdout=1024*1024, max_stderr=131072)
    configuration = json.loads(raw)
    continuation.validate_rendered(configuration, old, environment)
    proxy.write_new(stage / 'rendered-original-ports.private.json', raw)
    configuration['services']['backend']['ports'] = [proxy_plan['public_api_binding']]
    require(continuation.port_map(configuration['services']['backend']['ports']) == {('8000/tcp','127.0.0.1','8000')},
            'only_loopback_backend_port_permitted')
    execution = (json.dumps(continuation.compose_literals(configuration), sort_keys=True, indent=2) + '\n').encode()
    proxy.write_new(stage / 'candidate-execution.compose.private.json', execution)
    require(base.production_snapshot('render_production_after') == before, 'production_changed_during_readonly_render')
    report = {'operation': 'build15_readonly_resolved_runtime_proposal', 'status': 'prepared_for_review',
        'plan_id': plan['plan_id'], 'plan_sha256': args.plan_sha256, 'proxy_plan_sha256': args.proxy_plan_sha256,
        'render_id': args.render_id, 'continuation_sha256': args.continuation_sha256,
        'image_id': IMAGE, 'candidate_environment_sha256': plan['candidate_environment_sha256'],
        'current_runtime_sha256': plan['current_runtime_sha256'], 'current_compose_sha256': plan['current_compose_sha256'],
        'raw_render_sha256': hashlib.sha256(raw).hexdigest(),
        'execution_compose_sha256': hashlib.sha256(execution).hexdigest(), 'baseline_runtime_continuity_verified': True,
        'public_api_host_ip': '127.0.0.1', 'public_api_port': 8000, 'storage_network_env_preserved': True,
        'production_changed': False, 'database_connected': False, 'api_started': False,
        'commands': ['docker_inspect', 'docker_image_inspect', 'docker_compose_config'],
        'prepared_at': datetime.now(timezone.utc).isoformat()}
    proxy.json_new(stage / 'render-report.json', report)
    print(json.dumps({**report, 'render_report_sha256': sha(stage / 'render-report.json')}))


if __name__ == '__main__':
    try: main()
    except Exception as error:
        value = {'operation': 'build15_readonly_resolved_runtime_proposal', 'status': 'failed',
                 'production_changed': False, 'error_type': type(error).__name__, 'raw_error_omitted': True}
        if type(error) is RuntimeError and re.fullmatch(r'[a-z][a-z0-9_]{0,100}', str(error)):
            value['check'] = str(error)
        print(json.dumps(value)); raise SystemExit(1) from None
