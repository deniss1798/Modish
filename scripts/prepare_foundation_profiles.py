"""Extend the release acceptance smoke to report every cohort, including failures."""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
code = (root / 'scripts/smoke_release9.py').read_text(encoding='utf-8')
code = code.replace("url.host=='modish_release9_test_pg'", "url.host=='modish_foundation_test_pg'")
code = code.replace("sum(r['viewed'] for r in report['profiles'])", "sum(r.get('viewed', 0) for r in report['profiles'])")
code = code.replace("'shoe_size':shoe,'styles':styles", "'shoe_size':shoe,'clothing_size_system':'RU' if size.isdigit() else 'LETTER','shoe_size_system':'EU','styles':styles")
start = code.index('        for index,(size,shoe,budget,styles) in enumerate(cases):')
end = code.index('    with SessionLocal() as db:\n        report[\'test_metrics\']', start)
body = code[start:end].split('\n', 1)[1]
needle = "            assert len(deck['products'])>=15"
diagnostic = '''            with SessionLocal() as db:
                from collections import Counter
                pool = a.candidate_pool(db, db.get(User, uid), db.get(ActivationSession, uid))
                print(json.dumps({'progress':'coverage','profile':index+1,'pool':len(pool),
                    'categories':dict(Counter(p.category for p in pool)),
                    'brands':dict(Counter(p.brand for p in pool))}),flush=True)
'''
body = body.replace(needle, diagnostic + needle)
code = code[:start] + '        def check_profile(index, size, shoe, budget, styles):\n' + body + '''        for index,(size,shoe,budget,styles) in enumerate(cases):
            try:
                check_profile(index,size,shoe,budget,styles)
            except AssertionError as exc:
                failed={'profile':index+1,'size':size,'shoe':shoe,'budget':budget,'result':'failed','error':str(exc)}
                report['profiles'].append(failed)
                print(json.dumps({'progress':'profile_failed',**failed}),flush=True)
''' + code[end:]
code = code.replace("    assert sum(p['wow']>0 for p in report['profiles'])>=8,'insufficient_complete_outfits_across_profiles'\n    report['result']='passed'", "    report['wow_coverage']=sum(p.get('wow',0)>0 for p in report['profiles'])\n    report['result']='passed' if report['wow_coverage']>=8 and all(p.get('result')!='failed' for p in report['profiles']) else 'failed'")
compile(code, 'foundation-profiles.py', 'exec')
(root / '.tmp/foundation-profiles.py').write_text(code, encoding='utf-8')
