"""Create fixed-purpose release 6 deployment helpers from the verified release flow."""
from pathlib import Path
root = Path(__file__).resolve().parents[1]
scripts = root/'scripts'

def write(name, value): (scripts/name).write_text(value, encoding='utf-8')
def read(name): return (scripts/name).read_text(encoding='utf-8')
def names(text):
    return text.replace('20260913-styling-fixes','20260915-release6').replace(
        'modish-styling-test','modish-release6-test').replace('modish_styling_test_pg','modish_release6_test_pg').replace(
        'modish_styling_smoke','modish_release6_smoke').replace('modish-backend:styling-fixes-20260913','modish-backend:release6-20260915')

package = read('package_styling_fixes.py').replace('"app/api/outfits.py",', '''"app/api/auth.py", "app/models.py", "app/services/auth_sessions.py", "app/services/catalog_scope.py",
    "alembic/versions/20260915_0020_auth_sessions.py", "tests/test_release6_regressions.py", "tests/test_candidate_retrieval.py",
    "app/api/outfits.py",''').replace('modish-styling-fixes.tar','modish-release6.tar')
write('package_release6.py', package)

prepare = names(read('prepare_styling_release_remote.py')).replace(
    "old['Config']['Image']=='modish-backend:catalog-fixes-20260913'",
    "old['Config']['Image']=='modish-backend:styling-fixes-20260913'")
prepare = prepare.replace("    with (R/'smoke.log').open('w') as log:", """    with (R/'migration-test.log').open('w') as log:
        run('docker','run','--rm','--network',NETWORK,'--env-file',str(R/'test-api.env'),
            '--entrypoint','alembic',TAG,'upgrade','20260915_0020',stdout=log,stderr=subprocess.STDOUT)
    with (R/'smoke.log').open('w') as log:""")
prepare = prepare.replace('smoke_styling_fixes.py','smoke_release6.py')
write('prepare_release6_remote.py', prepare)

smoke = names(read('smoke_styling_fixes.py')).replace(
    '    from app.services.catalog_audience import product_is_adult',
    '    from app.services.catalog_scope import product_is_fashion\n    from app.services.catalog_audience import product_is_adult')
smoke = smoke.replace('assert product_is_adult(model)', 'assert product_is_adult(model) and product_is_fashion(model)').replace(
    'assert product_is_adult(other)', 'assert product_is_adult(other) and product_is_fashion(other)')
smoke = smoke.replace("            headers={'Authorization':'Bearer '+auth['access_token']}", """            headers={'Authorization':'Bearer '+auth['access_token']}
            # Simulate a device coming back with an expired access token.
            import jwt
            from app.api.deps import JWT_SECRET, JWT_ALG
            payload=jwt.decode(auth['access_token'],JWT_SECRET,algorithms=[JWT_ALG]);payload['exp']=int(time.time())-1
            expired=jwt.encode(payload,JWT_SECRET,algorithm=JWT_ALG)
            assert client.get('/users/me',headers={'Authorization':'Bearer '+expired}).status_code==401
            renewed=req('POST','/auth/refresh',json={'refresh_token':auth['refresh_token']})
            headers={'Authorization':'Bearer '+renewed['access_token']}
            assert req('GET','/users/me',headers=headers)['id']==payload['sub']
            report.setdefault('session_checks',[]).append({'gender':gender,'expired_access_recovered':True})""")
smoke = smoke.replace("            assert req('GET','/feed',headers=headers)\n", """            remaining=req('GET','/feed',headers=headers)
            assert remaining
            hidden=[p['product']['id'] for p in remaining]
            refill=req('GET','/feed',headers=headers,params=[('exclude_ids',pid) for pid in hidden])
            assert refill and not set(hidden)&{p['product']['id'] for p in refill}
            req('POST','/auth/logout',json={'refresh_token':auth['refresh_token']})
            assert client.post('/auth/refresh',json={'refresh_token':auth['refresh_token']}).status_code==401
            report['session_checks'][-1]['logout_revoked']=True
            report['session_checks'][-1]['inflight_swipes_excluded']=True
""")
write('smoke_release6.py', smoke)

ssh = names(read('prepare_styling_release_ssh.py')).replace('modish-styling-fixes.tar','modish-release6.tar').replace(
    'prepare_styling_release_remote.py','prepare_release6_remote.py').replace('smoke_styling_fixes.py','smoke_release6.py')
write('prepare_release6_ssh.py', ssh)
write('verify_release6_ssh.py', names(read('verify_styling_release_ssh.py')).replace(
    "'prepare.log','unit-tests.log','smoke.log'", "'prepare.log','migration-test.log','unit-tests.log','smoke.log'"))

activation=names(read('activate_styling_fixes.py')).replace("OLD='modish-backend:catalog-fixes-20260913'", "OLD='modish-backend:styling-fixes-20260913'").replace(
    'sha256:347857dc74da5dbffeebd9a626e0c26d5bb7c374b7377a3786ba41a681085e03',
    'sha256:616e3501ca18e6ff309ed5a9d8dc1b096b2e52389ba770854da1e23c97acddf1').replace('95 tests','100 tests').replace('unit_tests=95','unit_tests=100')
activation=activation.replace("        activation_attempted=True", """        # The new session table is additive; old API remains compatible for rollback.
        with (R/'migration-live.log').open('w') as log:
            run('docker','run','--rm','--network','modish_default','--env-file','/opt/Modish/backend/.env',
                '--entrypoint','alembic',NEW,'upgrade','20260915_0020',stdout=log,stderr=subprocess.STDOUT)
        record['migrations_applied']=True
        activation_attempted=True""")
activation=activation.replace("assert '/products/{product_id}/complements' in data['paths']", "assert '/auth/refresh' in data['paths'] and '/auth/session' in data['paths']")
activation=activation.replace('No migrations or catalog repairs in this release.', 'Added auth_sessions table; no catalog repairs in this release.')
write('activate_release6.py', activation)
ssh=names(read('activate_styling_release_ssh.py')).replace('activate_styling_fixes.py','activate_release6.py')
write('activate_release6_ssh.py',ssh)
write('finalize_release6_ssh.py',names(read('finalize_styling_release_ssh.py')))
write('fetch_release6_report_ssh.py',names(read('fetch_styling_release_report_ssh.py')).replace('styling_fixes_deployment_record.json','release6_deployment_record.json'))
print('Release 6 fixed-purpose helpers created')
