"""Derive the fixed deployment flow from build 6, with targeted rotation checks."""
from pathlib import Path
root = Path(__file__).resolve().parent

def names(value):
    return value.replace('release6', 'release7').replace('20260915-release7', '20260916-release7').replace('release7-20260915', 'release7-20260916')

for name in ['package_release6.py', 'prepare_release6_remote.py', 'prepare_release6_ssh.py',
             'verify_release6_ssh.py', 'activate_release6.py', 'activate_release6_ssh.py',
             'finalize_release6_ssh.py', 'fetch_release6_report_ssh.py']:
    value = names((root/name).read_text(encoding='utf-8'))
    value = value.replace('modish-backend:styling-fixes-20260913', 'modish-backend:release6-20260915')
    value = value.replace('sha256:616e3501ca18e6ff309ed5a9d8dc1b096b2e52389ba770854da1e23c97acddf1',
                          'sha256:4c90e3b230343ded22da88c6c18e2f94243c5ad965b188731fee7a12750b7d83')
    if name == 'package_release6.py':
        value = value.replace('tests/test_release7_regressions.py', 'tests/test_release6_regressions.py')
    if name == 'activate_release6.py':
        start = value.index("    assert report['result']=='passed'")
        end = value.index('    old=inspect', start)
        value = value[:start] + """    assert report['result']=='passed' and len(report['checks'])==6
    assert all(row['refreshes']==3 and row['distinct_batches']==3 for row in report['checks'])
    assert report['concurrent_refreshes']==2
    unit=(R/'unit-tests.log').read_text();assert 'Ran 105 tests' in unit and '\\nOK\\n' in unit
""" + value[end:]
        start = value.index('        # The new session table')
        end = value.index('        activation_attempted=True', start)
        value = value[:start] + value[end:]
        value = value.replace('unit_tests=100', 'unit_tests=105').replace('Added auth_sessions table; no catalog repairs in this release.', 'Outfit rotation update; no schema changes or catalog repairs.')
    (root/name.replace('release6','release7')).write_text(value, encoding='utf-8')
print('Build 7 deployment helpers created')
