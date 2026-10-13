"""Derive fixed, reviewable release 9 operations; no arbitrary SSH commands."""
from pathlib import Path
import subprocess
root = Path(__file__).resolve().parent
for name in ['package_release7.py','prepare_release7_remote.py','prepare_release7_ssh.py','verify_release7_ssh.py',
             'activate_release7.py','activate_release7_ssh.py','finalize_release7_ssh.py','fetch_release7_report_ssh.py']:
    value=(root/name).read_text(encoding='utf-8').replace('release7','release9').replace('20260916-release9','20260917-release9').replace('release9-20260916','release9-20260917')
    value=value.replace('modish-backend:release6-20260915','modish-backend:release7-20260916').replace(
        'sha256:4c90e3b230343ded22da88c6c18e2f94243c5ad965b188731fee7a12750b7d83',
        'sha256:0ed3f3b05ebaf071850706d2976423adfa21c6f0b09050b33f87503d2d71d871')
    if name=='package_release7.py':
        value=value.replace('FILES = [','FILES = [\n    "app/services/activation_service.py", "alembic/versions/20260917_0021_activation_sessions.py",\n    "app/api/onboarding.py", "app/api/recommendations.py", "app/api/analytics.py", "app/api/affiliate.py",\n    "app/api/fit_profile.py", "tests/test_activation.py",')
    if name=='prepare_release7_remote.py':
        value=value.replace("'upgrade','20260915_0020'","'upgrade','20260917_0021'")
    if name=='activate_release7.py':
        start=value.index("    assert report['result']=='passed'"); end=value.index('    old=inspect', start)
        value=value[:start]+'''    assert report['result']=='passed' and len(report['profiles'])==10
    assert report['concurrent_idempotency'] is True
    unit=(R/'unit-tests.log').read_text();assert '\\nOK\\n' in unit
    import re
    unit_count=int(re.search(r'Ran (\\d+) tests',unit).group(1)); assert unit_count>=117
'''+value[end:]
        start=value.index('        activation_attempted=True')
        migrate='''        # Additive tables only. The previous image can safely keep serving
        # while this migration runs and remains a compatible rollback target.
        with (R/'migration-production.log').open('w') as log:
            run('docker','run','--rm','--network','modish_default','--env-file','/opt/Modish/backend/.env',
                '--entrypoint','alembic',NEW,'upgrade','20260917_0021',stdout=log,stderr=subprocess.STDOUT)
        record['migrations_applied']=True
'''
        value=value[:start]+migrate+value[start:]
        value=value.replace("assert '/auth/refresh' in data['paths'] and '/auth/session' in data['paths']", "assert '/onboarding/style-swipe' in data['paths'] and '/analytics/first-session' in data['paths']")
        value=value.replace('unit_tests=105','unit_tests=unit_count').replace("isolated_checks=report['checks']","isolated_profiles=report['profiles']")
        value=value.replace('Outfit rotation update; no schema changes or catalog repairs.', 'Activation funnel and strict female casual feed; additive activation tables; no catalog rewrites.')
    (root/name.replace('release7','release9')).write_text(value,encoding='utf-8')
# Remove unrelated formatter-only edits: these files were clean before this task.
for name in ['fit_quiz_screen.dart','intro_onboarding_screen.dart','result_screen.dart']:
    relative='lib/features/onboarding/'+name
    (root.parent/relative).write_bytes(subprocess.check_output(['git','show','HEAD:'+relative],cwd=root.parent))
print('Release 9 fixed helpers prepared')
