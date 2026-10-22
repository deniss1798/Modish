"""Offline safety contracts only: no Docker, SQL, network or host changes."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
from unittest.mock import Mock

HERE = Path(__file__).resolve().parent


def load(filename):
    # fcntl is used only by executable Linux main(), never by these contracts.
    with patch.dict(sys.modules, {'fcntl': types.ModuleType('fcntl')}):
        spec = importlib.util.spec_from_file_location('offline_' + filename.replace('.', '_'), HERE / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class ActivationContracts(unittest.TestCase):
    def setUp(self):
        self.m = load('build15_activation_continue_remote.py')
        self.old = {'HostConfig': {'PortBindings': {'8000/tcp': [{'HostIp': '', 'HostPort': '8000'}]}},
            'NetworkSettings': {'Networks': {'modish_default': {}}},
            'Mounts': [{'Type': 'bind', 'Source': '/opt/Modish/uploads', 'Destination': '/app/uploads', 'RW': True}]}
        password = 'SYNTHETIC_PASSWORD' + chr(36) + 'LITERAL'
        self.env = {'DATABASE_URL': f'postgresql+psycopg://modish_user:{password}@postgres/modish',
                    'JWT_SECRET': 'synthetic-session-secret-for-offline-check'}
        self.rendered = {'services': {'backend': {'image': self.m.IMAGE, 'entrypoint': [],
            'command': self.m.COMMAND, 'environment': dict(self.env),
            'ports': [{'target': 8000, 'published': '8000', 'protocol': 'tcp'}],
            'networks': {'default': None},
            'volumes': [{'type': 'bind', 'source': '/opt/Modish/uploads', 'target': '/app/uploads'}]}},
            'networks': {'default': {'name': 'modish_default'}}}

    def test_current_runtime_continuity_accepts_resolved_config(self):
        self.m.validate_rendered(self.rendered, self.old, self.env)

    def test_unreviewed_environment_change_rejected(self):
        self.rendered['services']['backend']['environment']['JWT_SECRET'] = 'changed'
        with self.assertRaisesRegex(RuntimeError, 'exact_rendered_candidate_runtime_required'):
            self.m.validate_rendered(self.rendered, self.old, self.env)

    def test_extra_public_binding_rejected_at_baseline_review(self):
        self.rendered['services']['backend']['ports'].append({'target': 8000, 'published': '9999'})
        with self.assertRaisesRegex(RuntimeError, 'rendered_ports_changed'):
            self.m.validate_rendered(self.rendered, self.old, self.env)

    def test_wrong_network_and_storage_rejected(self):
        for change, code in (('network', 'rendered_networks_changed'), ('storage', 'rendered_storage_mounts_changed')):
            with self.subTest(change=change):
                value = deepcopy(self.rendered)
                if change == 'network': value['networks']['default']['name'] = 'unreviewed_network'
                else: value['services']['backend']['volumes'][0]['source'] = '/unreviewed/path'
                with self.assertRaisesRegex(RuntimeError, code): self.m.validate_rendered(value, self.old, self.env)

    def test_lifecycle_hooks_and_extra_replicas_rejected(self):
        for key, value in (('post_start', [{'command': 'unreviewed'}]), ('pre_stop', [{'command': 'unreviewed'}]), ('scale', 2), ('deploy', {'replicas': 2})):
            with self.subTest(key=key):
                config = deepcopy(self.rendered); config['services']['backend'][key] = value
                with self.assertRaisesRegex(RuntimeError, 'compose_lifecycle_hooks_or_extra_replicas_forbidden'):
                    self.m.validate_rendered(config, self.old, self.env)

    def test_resolved_compose_literal_dollars_survive_second_parse(self):
        raw = json.dumps(self.m.compose_literals(self.rendered))
        reparsed = json.loads(raw)
        # Compose's escape resolves each $$ as a literal $, not an env reference.
        value = reparsed['services']['backend']['environment']['DATABASE_URL'].replace('$$', '$')
        self.assertEqual(value, self.env['DATABASE_URL'])

    def test_full_config_port_replacement_removes_public_binding(self):
        value = deepcopy(self.rendered)
        value['services']['backend']['ports'] = [{'target': 8000, 'published': '8000', 'host_ip': '127.0.0.1', 'protocol': 'tcp'}]
        self.assertEqual(self.m.port_map(value['services']['backend']['ports']), {('8000/tcp', '127.0.0.1', '8000')})

    def test_incomplete_port_mapping_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'explicit_rendered_ports_required'):
            self.m.port_map([{'target': 8000}])

    def test_migration_proof_sql_preserves_normalized_business_rows(self):
        queries = []
        def sql(query):
            queries.append(query)
            return 'products\nusers\nalembic_version' if query.startswith('SELECT tablename') else json.dumps({'count': 1, 'rows': 'a' * 32})
        self.m.UTIL = types.SimpleNamespace(readonly_sql=sql)
        values = self.m.table_proof()
        self.assertEqual(set(values), {'products', 'users', 'alembic_version'})
        self.assertTrue(all('\\' not in query for query in queries))
        self.assertTrue(any('numeric(14,2)' in query and 'old_price' in query for query in queries))
        self.assertTrue(any("- 'recommendation_revision'" in query for query in queries))
        self.assertTrue(any("- 'version_num'" in query for query in queries))

    def test_final_backup_production_sql_write_refused_before_command(self):
        helper = load('build15_activation_final_backup_remote.py')
        with self.assertRaisesRegex(RuntimeError, 'production_sql_writes_forbidden'):
            helper.readonly_sql('DROP DATABASE forbidden;', test=False, write_test=True)

    def test_nginx_fence_closes_old_workers_before_start(self):
        proxy = load('build15_proxy_maintenance_remote.py')
        commands = []
        with patch.object(proxy, 'run', side_effect=lambda base, label, argv, **kw: commands.append(argv)), \
                patch.object(proxy, 'nginx_processes', side_effect=[[123], []]), patch.object(proxy, 'sleep'):
            proxy.restart_fenced_nginx(Mock())
        self.assertEqual(commands, [['systemctl','stop','nginx'], ['systemctl','start','nginx']])

    def test_nginx_fence_refuses_new_start_if_old_workers_survive(self):
        proxy = load('build15_proxy_maintenance_remote.py')
        commands = []
        with patch.object(proxy, 'run', side_effect=lambda base, label, argv, **kw: commands.append(argv)), \
                patch.object(proxy, 'nginx_processes', return_value=[123]), \
                patch.object(proxy, 'monotonic', side_effect=[0, 11]), patch.object(proxy, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'old_nginx_processes_still_alive'):
                proxy.restart_fenced_nginx(Mock())
        self.assertEqual(commands, [['systemctl','stop','nginx']])

    def worker(self):
        self.m.PLAN = {'plan_id': 'synthetic-plan'}
        item = {'id': 'a'*64, 'name': 'synthetic-worker', 'phase': 'import', 'cmd': ['/run.py'],
                'memory_bytes': 1536*1024*1024, 'mounts': [('/reviewed.py','/run.py',False)]}
        obj = {'Id': item['id'], 'Name': '/'+item['name'], 'Image': self.m.IMAGE,
            'State': {'Running': True}, 'Config': {'Labels': {'org.modish.activation_plan': 'synthetic-plan',
            'org.modish.activation_phase': 'import'}, 'Entrypoint': ['python'], 'Cmd': ['/run.py']},
            'HostConfig': {'NetworkMode': 'modish_default', 'PortBindings': {}, 'NanoCpus': 1000000000,
                'Memory': item['memory_bytes'], 'MemorySwap': item['memory_bytes'], 'PidsLimit': 128,
                'ReadonlyRootfs': True, 'Privileged': False, 'Devices': [], 'DeviceRequests': [],
                'PidMode': '', 'IpcMode': 'private', 'RestartPolicy': {'Name': 'no'},
                'CapDrop': ['ALL'], 'CapAdd': [], 'SecurityOpt': ['no-new-privileges']},
            'NetworkSettings': {'Networks': {'modish_default': {}}},
            'Mounts': [{'Source':'/reviewed.py','Destination':'/run.py','RW':False}]}
        return item,obj

    def test_worker_actual_limits_and_capabilities_checked(self):
        item,obj = self.worker()
        self.m.owned(obj,item)
        for key,value in (('Memory',0),('MemorySwap',-1),('NanoCpus',0),('PidsLimit',0),
                          ('ReadonlyRootfs',False),('Privileged',True),('Devices',[{}]),
                          ('DeviceRequests',[{}]),('PidMode','host'),('IpcMode','host'),
                          ('RestartPolicy',{'Name':'always'}),('CapDrop',[]),('CapAdd',['SYS_ADMIN']),('SecurityOpt',[])):
            with self.subTest(key=key):
                bad=deepcopy(obj);bad['HostConfig'][key]=value
                with self.assertRaisesRegex(RuntimeError,'owned_private_worker_resource_and_security_limits_required'):
                    self.m.owned(bad,item)

    def test_expired_workflow_still_stops_only_owned_worker_with_separate_budget(self):
        item,obj = self.worker()
        self.m.OWNED=[item]
        self.m.UTIL=types.SimpleNamespace(DEADLINE=0)
        runner=Mock(side_effect=[json.dumps([obj]).encode(),b'stopped'])
        self.m.BASE=types.SimpleNamespace(DOCKER=['docker'],run=runner)
        self.assertFalse(self.m.cleanup_owned_workers())
        self.assertEqual(runner.call_count,2)
        self.assertEqual(runner.call_args_list[1].args[1],['docker','stop','--time','5',item['id']])

    def test_cleanup_cannot_stop_worker_with_changed_identity_or_limits(self):
        item,obj=self.worker();obj['HostConfig']['Memory']=0
        self.m.OWNED=[item]
        runner=Mock(return_value=json.dumps([obj]).encode())
        self.m.BASE=types.SimpleNamespace(DOCKER=['docker'],run=runner)
        self.assertTrue(self.m.cleanup_owned_workers())
        self.assertEqual(runner.call_count,1)


if __name__ == '__main__': unittest.main(verbosity=2)
