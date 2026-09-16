from pathlib import Path
p=Path('test/app_regressions_test.dart');s=p.read_text(encoding='utf-8');s=s.replace("import 'package:modish/features/outfits/outfit_detail_screen.dart';", "import 'package:modish/features/outfits/outfit_detail_screen.dart';\nimport 'package:modish/features/catalog/filters_screen.dart';\nimport 'package:modish/features/settings/settings_screen.dart';")
s=s.replace('  int feedCalls = 0;', '  int feedCalls = 0;\n  Map<String, dynamic> lastFilters = {};')
s=s.replace('    feedCalls++;', "    feedCalls++;\n    lastFilters = {'min':minPrice, 'max':maxPrice, 'categories':categories, 'sizes':sizes, 'colors':colors};")
pos=s.index('void main() {')+len('void main() {');s=s[:pos]+'''
  testWidgets('Filters query the server and keep selections on reopening', (tester) async {
    final api = FakeApi()..rows = [{'product': product('fresh')}];
    final c = AppController(apiClient: api);
    await c.applyFeedFilters(categories: ['shoes'], sizes: ['42'], colors: ['Черный']);
    expect(api.lastFilters['categories'], ['shoes']);
    expect(api.lastFilters['colors'], ['Черный']);
    expect(api.lastFilters['min'], isNull);
    expect(c.currentProduct!.product.id, 'fresh');
    await tester.pumpWidget(MaterialApp(home: FiltersScreen(controller: c)));
    await tester.pumpAndSettle();
    expect(tester.widget<ChoiceChip>(find.widgetWithText(ChoiceChip, '42')).selected, isTrue);
    await tester.tap(find.text('Сбросить'));
    await tester.pump();
    await tester.tap(find.text('Показать товары'));
    await tester.pumpAndSettle();
    expect(api.lastFilters['categories'], isEmpty);
    expect(api.lastFilters['sizes'], isEmpty);
    expect(api.lastFilters['colors'], isEmpty);
    expect(api.lastFilters['max'], isNull);
  });

  testWidgets('Settings has working help and does not offer dead sections', (tester) async {
    await tester.pumpWidget(MaterialApp(home: SettingsScreen(controller: AppController(apiClient: FakeApi()))));
    await tester.pumpAndSettle();
    expect(find.text('Уведомления'), findsNothing);
    expect(find.text('Безопасность'), findsNothing);
    expect(find.text('Светлая'), findsNothing);
    await tester.tap(find.text('Как пользоваться Modish'));
    await tester.pumpAndSettle();
    expect(find.byType(HelpScreen), findsOneWidget);
    expect(find.text('Как устроена лента'), findsOneWidget);
  });
''' +s[pos:];p.write_text(s,encoding='utf-8')
# New package keeps the previous release and overlays only files reviewed for these bugs.
p=Path('scripts/package_outfit_hotfix.py');s=p.read_text(encoding='utf-8').replace('modish-outfit-hotfix.tar','modish-catalog-fixes.tar');pos=s.index('\n]\n');s=s[:pos]+'''
    "app/catalog_normalize.py", "app/api/products.py", "app/api/media_proxy.py",
    "app/schemas/api_product.py", "app/services/catalog_filters.py",
    "app/services/candidate_retrieval_service.py",
    "app/services/catalog/product_normalizer.py", "app/services/catalog/catalog_quality.py",
    "app/services/catalog/affiliate_link_service.py",
    "tests/test_catalog_device_regressions.py",
''' +s[pos:];Path('scripts/package_catalog_fixes.py').write_text(s,encoding='utf-8')
