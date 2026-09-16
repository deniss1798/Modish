from pathlib import Path
p=Path('lib/features/profile/profile_screen.dart');s=p.read_text(encoding='utf-8').replace("import '../subscription/plus_screen.dart';\n",'');start=s.index('              MenuTile(\n                icon: Icons.workspace_premium_outlined,');end=s.index('              MenuTile(',start+20);s=s[:start]+s[end:];p.write_text(s,encoding='utf-8')
