from pathlib import Path
for name, start, end in [
 ('lib/core/network/api_client.dart', '  Future<List<Map<String, dynamic>>> productComplements', '  Future<List<Map<String, dynamic>>> productsBrands'),
 ('lib/features/products/product_detail_screen.dart', '  void _rebuild()', '  Future<void> _pullLatest()'),
]:
 p=Path(name);s=p.read_text(encoding='utf-8');a=s.index(start);b=s.index(end,a)
 block=s[a:b];second=block.find(start,len(start))
 if second>=0:s=s[:a]+block[:second]+s[b:]
 p.write_text(s,encoding='utf-8')
for name, blocks in {
 'lib/features/products/models.dart': ['    this.outfitSlot,\n', '  final String? outfitSlot;\n', "      outfitSlot: json['outfit_slot']?.toString(),\n"],
 'lib/features/products/product_detail_screen.dart': ['    widget.controller.addListener(_rebuild);\n    widget.controller.loadRelatedProducts(_card.product.id);\n'],
 'lib/features/recommendations/feed_screen.dart': ['      WidgetsBinding.instance.addPostFrameCallback((_) {\n        if (mounted) unawaited(controller.loadRelatedProducts(id));\n      });\n'],
}.items():
 p=Path(name);s=p.read_text(encoding='utf-8')
 for block in blocks:
  while block+block in s:s=s.replace(block+block,block)
 p.write_text(s,encoding='utf-8')
p=Path('scripts/package_catalog_fixes.py');s=p.read_text(encoding='utf-8').replace('modish-catalog-fixes.tar','modish-styling-fixes.tar')
idx=s.index('\n]\n');s=s[:idx]+'''
    "app/services/catalog_audience.py", "app/services/garment_roles.py",
    "app/services/product_complements.py", "app/services/embedding_service.py",
    "app/services/feed_filters.py", "app/services/mie_scoring.py",
    "tests/test_styling_regressions.py",
'''+s[idx:]
Path('scripts/package_styling_fixes.py').write_text(s,encoding='utf-8')
