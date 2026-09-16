from pathlib import Path
p=Path('backend/app/services/candidate_retrieval_service.py');s=p.read_text(encoding='utf-8');a=s.index('  raw_values =',s.index('def _fit_gender_condition'));b=s.index('\n\n\ndef _norm_values',a);s=s[:a]+'''  # Existing rows are repaired by scripts/repair_catalog_gender.py at release.
  # Imports persist the same resolver's result. Keep this indexed gate cheap;
  # authoritative merchant metadata is checked again before returning a card.
  return Product.gender_target.in_([gender, "unisex"])
'''+s[b:];p.write_text(s,encoding='utf-8')
p=Path('scripts/package_catalog_fixes.py');s=p.read_text(encoding='utf-8').replace('    "tests/test_catalog_device_regressions.py",','    "tests/test_catalog_device_regressions.py", "scripts/repair_catalog_gender.py",');p.write_text(s,encoding='utf-8')
