"""Upload/check build14 preparation; SSH credentials stay in a memory socket.

Planning builds rollback protection without changing production. Activation
requires an exact reviewed plan hash and separate explicit import approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
REMOTE = '/opt/modish-releases/20261014-build14'
sys.path.insert(0, str(ROOT / '.tmp/ssh-runtime'))
import paramiko
from release9_ssh_connection import ready_socket


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare', 'status', 'fetch', 'retry_photo', 'resume', 'bounded', 'plan', 'plan_retry', 'activate', 'proxy_plan', 'proxy_apply', 'verify'))
    parser.add_argument('--archive', default='modish-foundation-v30.tar')
    parser.add_argument('--plan-sha256')
    parser.add_argument('--production-import-approved', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'modish-foundation-v[0-9]+\.tar', args.archive):
        raise ValueError('fixed_workspace_archive_required')
    if args.operation == 'activate':
        if not args.production_import_approved or not re.fullmatch(r'[0-9a-f]{64}', args.plan_sha256 or ''):
            raise ValueError('explicit_approval_and_exact_plan_hash_required')
        plan = json.loads((ROOT / 'docs/modish2/build14-activation-plan.json').read_text())
        if digest(ROOT / 'docs/modish2/build14-activation-plan.json') != args.plan_sha256 or \
                digest(ROOT / 'scripts/release14_activation_remote.py') != plan['script_sha256']:
            raise ValueError('reviewed_plan_or_helper_changed')
    if args.operation == 'proxy_apply':
        proxy_plan = ROOT / 'docs/modish2/build14-proxy-timeout-plan.json'
        if not re.fullmatch(r'[0-9a-f]{64}', args.plan_sha256 or '') or digest(proxy_plan) != args.plan_sha256:
            raise ValueError('exact_reviewed_proxy_plan_required')
        if json.loads(proxy_plan.read_text())['script_sha256'] != digest(ROOT / 'scripts/release14_proxy_timeout_remote.py'):
            raise ValueError('proxy_helper_changed')
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(120)
    print(json.dumps({'password_port': listener.getsockname()[1]}), flush=True)
    connection, _ = listener.accept()
    listener.close()
    with connection, connection.makefile('r', encoding='utf-8') as incoming:
        password = json.loads(incoming.readline())['password']
    client = paramiko.SSHClient()
    client.load_host_keys(os.path.expanduser('~/.ssh/known_hosts'))
    try:
        client.connect('194.87.118.236', username='root', password=password,
                       sock=ready_socket(), look_for_keys=False, allow_agent=False,
                       timeout=20, banner_timeout=20, auth_timeout=15)
        if args.operation == 'prepare':
            archive = ROOT / '.tmp' / args.archive
            package_name = args.archive.removeprefix('modish-').removesuffix('.tar')
            checked = json.loads((ROOT / 'docs/modish2' / (package_name + '-package-check.json')).read_text())
            archive_sha = digest(archive)
            if checked['test_exit_code'] != 0 or checked['archive_sha256'] != archive_sha:
                raise ValueError('successful_exact_local_package_required')
            count = re.fullmatch(r'Ran (\d+) tests in [0-9.]+s', checked['test_summary'])
            if not count:
                raise ValueError('verified_test_summary_required')
            helper_names = ('release14_prepare_remote.py', 'check_photo_postgres.py',
                            'check_discovery_postgres.py', 'release14_import_clone.py',
                            'intent_v20_refresh_import.py')
            files = [(archive, 'source.tar'),
                     *[(ROOT / 'scripts' / name, name) for name in helper_names]]
            hashes = {name: digest(ROOT / 'scripts' / name) for name in helper_names}
            with client.open_sftp() as sftp:
                try:
                    sftp.mkdir(REMOTE, mode=0o700)
                except IOError:
                    stat = sftp.lstat(REMOTE)
                    import stat as stat_types
                    if not stat_types.S_ISDIR(stat.st_mode):
                        raise ValueError('regular_release_directory_required')
                for source, name in files:
                    try:
                        sftp.lstat(REMOTE + '/' + name)
                    except IOError:
                        pass
                    else:
                        raise ValueError('immutable_upload_destination_exists')
                for source, name in files:
                    sftp.put(str(source), REMOTE + '/' + name)
                    sftp.chmod(REMOTE + '/' + name, 0o600)
            parameters = ['--archive-sha256', archive_sha, '--expected-tests', count[1],
                          '--photo-helper-sha256', hashes['check_photo_postgres.py'],
                          '--import-helper-sha256', hashes['release14_import_clone.py'],
                          '--discovery-helper-sha256', hashes['check_discovery_postgres.py']]
            payload = json.dumps({'hashes': hashes, 'archive_sha': archive_sha, 'parameters': parameters})
            code = """import hashlib,json,os,subprocess
from pathlib import Path
os.umask(0o077)
r=Path('/opt/modish-releases/20261014-build14')
p=json.loads(__PAYLOAD__)
assert r.resolve()==r and not r.is_symlink()
for name,sha in {**p['hashes'],'source.tar':p['archive_sha']}.items():
 assert hashlib.sha256((r/name).read_bytes()).hexdigest()==sha
with (r/'prepare-console.log').open('x') as log:
 process=subprocess.Popen(['python3','-u',str(r/'release14_prepare_remote.py'),*p['parameters']],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
print(json.dumps({'status':'isolated_preparation_started','pid':process.pid,'production_database_writes':False}))
""".replace('__PAYLOAD__', repr(payload))
        elif args.operation == 'verify':
            helper = ROOT / 'scripts/release14_postdeploy_remote.py'
            checksum = digest(helper)
            with client.open_sftp() as sftp:
                target = REMOTE + '/' + helper.name
                try:
                    sftp.lstat(target)
                except IOError:
                    pass
                else:
                    raise ValueError('immutable_postdeploy_helper_exists')
                sftp.put(str(helper), target)
                sftp.chmod(target, 0o600)
            code = """import hashlib,subprocess
from pathlib import Path
r=Path('/opt/modish-releases/20261014-build14')
helper=r/'release14_postdeploy_remote.py'
assert hashlib.sha256(helper.read_bytes()).hexdigest()==__HELPER_SHA__
raise SystemExit(subprocess.call(['python3',str(helper)]))
""".replace('__HELPER_SHA__', repr(checksum))
        elif args.operation in ('proxy_plan', 'proxy_apply'):
            helper = ROOT / 'scripts/release14_proxy_timeout_remote.py'
            checksum = digest(helper)
            if args.operation == 'proxy_plan':
                with client.open_sftp() as sftp:
                    target = REMOTE + '/' + helper.name
                    try:
                        sftp.lstat(target)
                    except IOError:
                        pass
                    else:
                        raise ValueError('immutable_proxy_helper_exists')
                    sftp.put(str(helper), target)
                    sftp.chmod(target, 0o600)
            payload = json.dumps({'helper_sha': checksum, 'mode': 'plan' if args.operation == 'proxy_plan' else 'apply',
                                  'plan_sha': args.plan_sha256})
            code = """import hashlib,json,subprocess
from pathlib import Path
r=Path('/opt/modish-releases/20261014-build14')
p=json.loads(__PAYLOAD__)
assert r.resolve()==r and not r.is_symlink()
helper=r/'release14_proxy_timeout_remote.py'
assert hashlib.sha256(helper.read_bytes()).hexdigest()==p['helper_sha']
parameters=['python3',str(helper),p['mode']]
if p['mode']=='apply': parameters+=['--plan-sha256',p['plan_sha']]
raise SystemExit(subprocess.call(parameters))
""".replace('__PAYLOAD__', repr(payload))
        elif args.operation == 'activate':
            payload = json.dumps({'plan_sha': args.plan_sha256,
                                  'helper_sha': plan['script_sha256']})
            code = """import hashlib,json,os,subprocess
from pathlib import Path
os.umask(0o077)
r=Path('/opt/modish-releases/20261014-build14')
p=json.loads(__PAYLOAD__)
assert r.resolve()==r and not r.is_symlink()
assert hashlib.sha256((r/'activation-plan.json').read_bytes()).hexdigest()==p['plan_sha']
helper=r/'release14_activation_v2_remote.py'
assert hashlib.sha256(helper.read_bytes()).hexdigest()==p['helper_sha']
with (r/'activation-console.log').open('x') as log:
 process=subprocess.Popen(['python3','-u',str(helper),'activate','--plan-sha256',p['plan_sha'],'--production-import-approved'],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
print(json.dumps({'status':'approved_activation_started','pid':process.pid,'production_import_explicitly_approved':True}))
""".replace('__PAYLOAD__', repr(payload))
        elif args.operation in ('plan', 'plan_retry'):
            names = ('release14_activation_remote.py', 'release14_import_production.py',
                     'release14_rollback_guard.py', 'release14_rollback_guard_tests.py')
            hashes = {name: digest(ROOT / 'scripts' / name) for name in names}
            report_sha = digest(ROOT / 'docs/modish2/build14-bounded-report.json')
            remote_names = {name: name for name in names}
            if args.operation == 'plan_retry':
                remote_names['release14_activation_remote.py'] = 'release14_activation_v2_remote.py'
            with client.open_sftp() as sftp:
                for name in names:
                    try:
                        sftp.lstat(REMOTE + '/' + remote_names[name])
                    except IOError:
                        pass
                    else:
                        if args.operation == 'plan_retry' and name != 'release14_activation_remote.py':
                            with sftp.open(REMOTE + '/' + name, 'rb') as stream:
                                if hashlib.sha256(stream.read()).hexdigest() == hashes[name]:
                                    continue
                        raise ValueError('immutable_planning_destination_exists')
                for name in names:
                    if args.operation == 'plan_retry' and name != 'release14_activation_remote.py':
                        continue
                    sftp.put(str(ROOT / 'scripts' / name), REMOTE + '/' + remote_names[name])
                    sftp.chmod(REMOTE + '/' + remote_names[name], 0o600)
            payload = json.dumps({'hashes': {remote_names[name]: sha for name, sha in hashes.items()},
                'script': remote_names['release14_activation_remote.py'],
                'log': 'plan-v2-console.log' if args.operation == 'plan_retry' else 'plan-console.log',
                'report_sha': report_sha, 'parameters': [
                'plan', '--bounded-report-sha256', report_sha,
                '--rollback-guard-sha256', hashes['release14_rollback_guard.py'],
                '--rollback-tests-sha256', hashes['release14_rollback_guard_tests.py'],
            ]})
            code = """import hashlib,json,os,subprocess
from pathlib import Path
os.umask(0o077)
r=Path('/opt/modish-releases/20261014-build14')
p=json.loads(__PAYLOAD__)
assert r.resolve()==r and not r.is_symlink()
for name,sha in p['hashes'].items(): assert hashlib.sha256((r/name).read_bytes()).hexdigest()==sha
assert hashlib.sha256((r/'bounded-report.json').read_bytes()).hexdigest()==p['report_sha']
with (r/p['log']).open('x') as log:
 process=subprocess.Popen(['python3','-u',str(r/p['script']),*p['parameters']],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
print(json.dumps({'status':'activation_plan_preparation_started','pid':process.pid,'production_database_writes':False,'production_api_switch':False}))
""".replace('__PAYLOAD__', repr(payload))
        elif args.operation == 'bounded':
            files = {name: ROOT / 'scripts' / name for name in
                     ('release14_validate_bounded_remote.py', 'release14_import_bounded.py')}
            hashes = {name: digest(path) for name, path in files.items()}
            with client.open_sftp() as sftp:
                for name in files:
                    try:
                        sftp.lstat(REMOTE + '/' + name)
                    except IOError:
                        pass
                    else:
                        raise ValueError('immutable_bounded_destination_exists')
                for name, source in files.items():
                    sftp.put(str(source), REMOTE + '/' + name)
                    sftp.chmod(REMOTE + '/' + name, 0o600)
            payload = json.dumps({'hashes': hashes, 'parameters': [
              '--prepare-sha256', digest(ROOT / 'scripts/release14_prepare_remote.py'),
              '--import-sha256', hashes['release14_import_bounded.py'],
            ]})
            code = """import hashlib,json,os,subprocess
from pathlib import Path
os.umask(0o077)
r=Path('/opt/modish-releases/20261014-build14')
p=json.loads(__PAYLOAD__)
assert r.resolve()==r and not r.is_symlink()
for name,sha in p['hashes'].items(): assert hashlib.sha256((r/name).read_bytes()).hexdigest()==sha
with (r/'bounded-console.log').open('x') as log:
 process=subprocess.Popen(['python3','-u',str(r/'release14_validate_bounded_remote.py'),*p['parameters']],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
print(json.dumps({'status':'isolated_bounded_import_started','pid':process.pid,'production_database_writes':False}))
""".replace('__PAYLOAD__', repr(payload))
        elif args.operation == 'resume':
            files = {
              'release14_resume_remote.py': ROOT / 'scripts/release14_resume_remote.py',
              'check_photo_postgres_fixed.py': ROOT / 'scripts/check_photo_postgres.py',
              'check_discovery_postgres_fixed.py': ROOT / 'scripts/check_discovery_postgres.py',
              'check_discovery_http_postgres.py': ROOT / 'scripts/check_discovery_http_postgres.py',
            }
            hashes = {name: digest(path) for name, path in files.items()}
            with client.open_sftp() as sftp:
                for name in files:
                    try:
                        sftp.lstat(REMOTE + '/' + name)
                    except IOError:
                        pass
                    else:
                        raise ValueError('immutable_resume_destination_exists')
                for name, source in files.items():
                    sftp.put(str(source), REMOTE + '/' + name)
                    sftp.chmod(REMOTE + '/' + name, 0o600)
            payload = json.dumps({'hashes': hashes, 'parameters': [
              '--prepare-sha256', digest(ROOT / 'scripts/release14_prepare_remote.py'),
              '--photo-sha256', hashes['check_photo_postgres_fixed.py'],
              '--discovery-sha256', hashes['check_discovery_postgres_fixed.py'],
              '--http-sha256', hashes['check_discovery_http_postgres.py'],
            ]})
            code = """import hashlib,json,os,subprocess
from pathlib import Path
os.umask(0o077)
r=Path('/opt/modish-releases/20261014-build14')
p=json.loads(__PAYLOAD__)
assert r.resolve()==r and not r.is_symlink()
for name,sha in p['hashes'].items(): assert hashlib.sha256((r/name).read_bytes()).hexdigest()==sha
with (r/'resume-console.log').open('x') as log:
 process=subprocess.Popen(['python3','-u',str(r/'release14_resume_remote.py'),*p['parameters']],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
print(json.dumps({'status':'isolated_continuation_started','pid':process.pid,'production_database_writes':False}))
""".replace('__PAYLOAD__', repr(payload))
        elif args.operation == 'retry_photo':
            helper = ROOT / 'scripts/check_photo_postgres.py'
            checksum = digest(helper)
            prepare_checksum = digest(ROOT / 'scripts/release14_prepare_remote.py')
            with client.open_sftp() as sftp:
                target = REMOTE + '/check_photo_postgres_retry1.py'
                try:
                    sftp.lstat(target)
                except IOError:
                    pass
                else:
                    raise ValueError('immutable_retry_destination_exists')
                sftp.put(str(helper), target)
                sftp.chmod(target, 0o600)
            code = """import hashlib,importlib.util,json,os
from pathlib import Path
os.umask(0o077)
r=Path('/opt/modish-releases/20261014-build14')
assert hashlib.sha256((r/'release14_prepare_remote.py').read_bytes()).hexdigest()==__PREPARE_SHA__
assert hashlib.sha256((r/'check_photo_postgres_retry1.py').read_bytes()).hexdigest()==__HELPER_SHA__
spec=importlib.util.spec_from_file_location('prepare',r/'release14_prepare_remote.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
failed=json.loads((r/'failure-report.json').read_text())
assert failed['phase']=='postgres_photo_and_registration_concurrency' and failed['production_metadata_unchanged']
assert p.production_snapshot()==failed['production_before']
assert p.query('SELECT version_num FROM alembic_version')==p.REVISION
assert p.inspect(failed['image_id'])['Id']==failed['image_id']
result=p.clone_container(failed['image_id'],r/'private/test-api.env',
 ['/audit.py','--isolated-write-test','--expected-host',p.PG,'--expected-database',p.DATABASE],
 name='photo-retry1',mounts=((r/'check_photo_postgres_retry1.py','/audit.py'),),timeout=120)
print(result.read_text())
""".replace('__PREPARE_SHA__', repr(prepare_checksum)).replace('__HELPER_SHA__', repr(checksum))
        else:
            if args.operation == 'fetch':
                with client.open_sftp() as sftp:
                    for name in ('prepared-report.json', 'failure-report.json', 'resume-report.json', 'resume-failure-report.json', 'bounded-report.json', 'bounded-failure-report.json', 'activation-plan.json', 'deployment-record.json', 'activation-failure-report.json', 'proxy-timeout-plan.json', 'proxy-timeout-record.json', 'proxy-timeout-failure.json', 'postdeploy-verification.json'):
                        try:
                            sftp.stat(REMOTE + '/' + name)
                        except IOError:
                            continue
                        sftp.get(REMOTE + '/' + name,
                                 str(ROOT / 'docs/modish2' / ('build14-' + name)))
            code = """import json,subprocess
from urllib.parse import urlsplit
import re
from pathlib import Path
r=Path('/opt/modish-releases/20261014-build14')
result={}
backend=json.loads(subprocess.check_output(['docker','inspect','modish_backend']))[0]
environment=dict(item.split('=',1) for item in backend['Config']['Env'] if '=' in item)
url=urlsplit(environment.get('DATABASE_URL','').replace('postgresql+psycopg://','postgresql://'))
result['production_database_names']={'host':url.hostname,'database':url.path.lstrip('/'),'username':url.username}
result['production_backend_running']=backend['State']['Running']
labels=backend['Config'].get('Labels') or {}
result['production_compose']={key:labels.get('com.docker.compose.'+key) for key in ('project','service','project.working_dir','project.config_files')}
result['production_networks']=sorted(backend['NetworkSettings']['Networks'])
result['production_image']=backend['Image']
result['active_activation_workers']=[name for name in subprocess.check_output(['docker','ps','--format','{{.Names}}'],text=True).splitlines() if name.startswith('modish_build14_activation_')]
if result['active_activation_workers']:
 metrics=subprocess.check_output(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=5000','modish_postgres','psql','-X','-qAt','-U','modish_user','-d','modish','--set','ON_ERROR_STOP=1','-c',"SELECT json_build_object('active_queries',count(*) FILTER (WHERE state='active'),'lock_waits',count(*) FILTER (WHERE wait_event_type='Lock'),'longest_transaction_seconds',max(extract(epoch FROM now()-xact_start))::integer,'longest_query_seconds',max(extract(epoch FROM now()-query_start))::integer) FROM pg_stat_activity WHERE datname='modish' AND pid<>pg_backend_pid()"],text=True)
 result['database_activity_summary']=json.loads(metrics)
 stats=subprocess.check_output(['docker','stats','--no-stream','--format','{{json .}}',*result['active_activation_workers']],text=True)
 result['activation_worker_resources']=[{key:entry.get(key) for key in ('Name','CPUPerc','MemUsage')} for line in stats.splitlines() if (entry:=json.loads(line))]
proxy=subprocess.run(['nginx','-T'],capture_output=True,text=True)
result['proxy_configuration_valid']=proxy.returncode==0
result['proxy_timeouts']=[]
for section in re.split(r'# configuration file ',proxy.stdout)[1:]:
 if re.search(r'server_name\\s+[^;]*\\bmodish\\.org\\.ru\\b',section):
  filename,_,body=section.partition('\\n')
  result['proxy_timeouts'].append({'file':filename.rstrip(':'),'resolved':str(Path(filename.rstrip(':')).resolve()),'directives':re.findall(r'^\\s*(proxy_(?:read|send|connect)_timeout\\s+[^;]+;)',body,re.M)})
if (r/'activation-progress.json').exists():
 progress=json.loads((r/'activation-progress.json').read_text())
 result['activation_progress']={key:progress[key] for key in ('status','phase','updated_at','import_outcome','revision','error_type','guard','failed_phase','api_rollback','checks','outside_sources_unchanged','production_database_and_redis_unchanged') if key in progress}
if (r/'activation-console.log').exists():
 result['activation_console']=[json.loads(line) for line in (r/'activation-console.log').read_text().splitlines() if line.startswith('{')][-3:]
if (r/'activation-plan.json').exists():
 plan=json.loads((r/'activation-plan.json').read_text())
 import hashlib
 result['activation_plan']={key:plan[key] for key in ('status','created_at','candidate_image','rollback_image','verified','production_changed','production_import_approval_required')}
 result['activation_plan']['sha256']=hashlib.sha256((r/'activation-plan.json').read_bytes()).hexdigest()
elif (r/'plan-console.log').exists():
 current_log=r/('plan-v2-console.log' if (r/'plan-v2-console.log').exists() else 'plan-console.log')
 result['plan_progress']=[json.loads(line) for line in current_log.read_text().splitlines() if line.startswith('{')][-3:]
 result['plan_diagnostics']={}
 for name in ('rollback-build.log','rollback-guard-tests.log','rollback-base-probe.log'):
  path=r/('activation-private-v2' if (r/'activation-private-v2').exists() else 'activation-private')/name
  if path.exists():
   lines=path.read_text(errors='replace').splitlines()
   result['plan_diagnostics'][name]={'bytes':path.stat().st_size,'error_types':[match.group(1) for line in lines if (match:=re.match(r'^([A-Za-z_.]+(?:Error|Exception)):',line))][-3:]}
 import urllib.request
 try:
  body=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5))
  result['production_health']={key:body.get(key) for key in ('status','database','version')}
  api=json.load(urllib.request.urlopen('http://127.0.0.1:8000/openapi.json',timeout=5))
  result['production_openapi_version']=api['info']['version']
 except Exception as error:
  result['production_health_error_type']=type(error).__name__
kernel=subprocess.run(['journalctl','-k','--since','2026-10-14 06:06:50','--no-pager','-o','cat'],capture_output=True,text=True)
result['oom_processes']=[match.group(1) for line in kernel.stdout.splitlines() if (match:=re.search(r'Killed process [0-9]+ \\(([^)]+)\\)',line))][-5:]
for name in ('photo-postgres.json','photo-retry1.json','photo-fixed.json','clone-import.json','bounded-import.json'):
 p=r/'private'/name
 if p.exists():
  try: result[name]=json.loads(p.read_text())
  except ValueError: result[name]={'status':'running_or_incomplete'}
for name in ('prepared-report.json','failure-report.json','resume-report.json','resume-failure-report.json','bounded-report.json','bounded-failure-report.json'):
 p=r/name
 if p.exists():
  report=json.loads(p.read_text())
  result[name]={key:report[key] for key in ('status','phase','error_type','guard','image_id','archive_sha256','unit_tests','finished_at','production_metadata_unchanged') if key in report}
if not (r/'bounded-report.json').exists() and not (r/'bounded-failure-report.json').exists():
 names=subprocess.check_output(['docker','ps','--format','{{.Names}}'],text=True).splitlines()
 result['active_helpers']=[name for name in names if name.startswith('modish_build14_prepare_')]
 p=r/('bounded-console.log' if (r/'bounded-console.log').exists() else 'resume-console.log' if (r/'resume-console.log').exists() else 'prepare-console.log')
 result['progress']=[json.loads(line) for line in (p.read_text().splitlines() if p.exists() else []) if line.startswith('{')][-3:]
print(json.dumps(result))
"""
        _, output, error = client.exec_command('python3 -c ' + shlex.quote(code), timeout=45)
        raw = output.read().decode('utf-8')
        exit_code = output.channel.recv_exit_status()
        if exit_code:
            print(json.dumps({'status': 'ssh_operation_failed', 'exit_code': exit_code,
                              'raw_error_omitted': True}))
            return 1
        print(raw.replace(password, '[REDACTED]').strip())
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__, 'raw_error_omitted': True}))
        return 1
    finally:
        client.close()


if __name__ == '__main__':
    raise SystemExit(main())
