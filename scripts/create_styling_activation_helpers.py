"""Create fixed, reviewable release and cleanup helpers; no remote execution."""
from pathlib import Path
base=Path('scripts/recheck_styling_release_ssh.py').read_text(encoding='utf-8')
a=base.index('  uploads=[');b=base.index('  for local,name in uploads:',a)
base=base[:a]+"  uploads=[(ROOT/'scripts/activate_styling_fixes.py','activate.py')]\n"+base[b:]
base=base.replace("r/'prepare.log'","r/'activation.log'").replace("r/'prepare.py'","r/'activate.py'")
Path('scripts/activate_styling_release_ssh.py').write_text(base,encoding='utf-8')
cleanup=Path('scripts/finalize_catalog_release_ssh.py').read_text(encoding='utf-8')
cleanup=cleanup.replace('20260913-catalog-fixes','20260913-styling-fixes').replace('catalog-fixes-20260913','styling-fixes-20260913')
cleanup=cleanup.replace('modish_catalog_test_pg','modish_styling_test_pg').replace('modish-catalog-test','modish-styling-test')
Path('scripts/finalize_styling_release_ssh.py').write_text(cleanup,encoding='utf-8')
