from pathlib import Path
src=Path('scripts/verify_catalog_release_ssh.py').read_text(encoding='utf-8-sig')
a=src.index('command="""');b=src.index('\nlistener=',a)
command='''command="""python3 - <<'PY'
import json,subprocess
from pathlib import Path
r=Path('/opt/modish-releases/20260913-catalog-fixes')
assert r.resolve()==r and json.loads((r/'deployment-record.json').read_text())['status']=='deployed'
def inspect(name):return json.loads(subprocess.check_output(['docker','inspect',name]))[0]
live={n:inspect(n)['Id'] for n in ('modish_backend','modish_postgres','modish_redis')}
assert inspect('modish_backend')['Config']['Image']=='modish-backend:catalog-fixes-20260913'
test=inspect('modish_catalog_test_pg')
assert test['Name']=='/modish_catalog_test_pg'
assert set(test['NetworkSettings']['Networks'])=={'modish-catalog-test'}
assert test['Id'] not in live.values()
assert all(m['Type']=='volume' and m['Destination']=='/var/lib/postgresql/data' for m in test['Mounts'])
production_mounts={m.get('Name',m['Source']) for n in ('modish_postgres','modish_redis') for m in inspect(n)['Mounts']}
assert all(m['Name'] not in production_mounts for m in test['Mounts'])
subprocess.run(['docker','rm','--force','--volumes','modish_catalog_test_pg'],check=True,stdout=subprocess.DEVNULL)
network=json.loads(subprocess.check_output(['docker','network','inspect','modish-catalog-test']))[0]
assert not network['Containers']
subprocess.run(['docker','network','rm','modish-catalog-test'],check=True,stdout=subprocess.DEVNULL)
for name in ('test-api.env','test-postgres.env'):
 p=r/name
 assert p.resolve().parent==r
 p.unlink(missing_ok=True)
assert all(inspect(n)['Id']==cid for n,cid in live.items())
result={'temporary_test_database_removed':True,'test_secrets_removed':True,'production_containers_preserved':True}
(r/'cleanup-record.json').write_text(json.dumps(result))
print(json.dumps(result))
PY"""'''
Path('scripts/finalize_catalog_release_ssh.py').write_text(src[:a]+command+src[b:],encoding='utf-8')
