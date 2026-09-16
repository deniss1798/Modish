from pathlib import Path
p=Path('lib/app.dart')
s=p.read_text(encoding='utf-8'); start=s.index('  List<prod.FeedCard> get filteredProductFeed {'); end=s.index('\n  }',start)+4
s=s[:start]+'  List<prod.FeedCard> get filteredProductFeed => productFeed;'+s[end:]
s=s.replace('  int? feedMinPrice;', '  List<String> feedFilterCategories = [];\n  int? feedMinPrice;')
s=s.replace('  void applyFeedFilters({','  Future<void> applyFeedFilters({').replace('    List<String> colors = const [],\n  }) {\n    feedMinPrice', '    List<String> colors = const [],\n    List<String> categories = const [],\n  }) async {\n    feedMinPrice')
s=s.replace('    feedFilterColors = colors;\n    notifyListeners();', '    feedFilterColors = colors;\n    feedFilterCategories = categories;\n    productFeed = [];\n    await refreshFeedFromUser();')
s=s.replace('final rows = await api.productFeed(limit: 30);','final rows = await api.productFeed(\n        limit: 30, minPrice: feedMinPrice, maxPrice: feedMaxPrice,\n        categories: feedFilterCategories, sizes: feedFilterSizes, colors: feedFilterColors,\n      );')
s=s.replace('    _dismissedProductKeys.clear();', '    _dismissedProductKeys.clear();\n    feedMinPrice = null;\n    feedMaxPrice = null;\n    feedFilterCategories = [];\n    feedFilterSizes = [];\n    feedFilterColors = [];')
p.write_text(s,encoding='utf-8')
for path in ['lib/core/network/api_client.dart','test/app_regressions_test.dart']:
 p=Path(path); s=p.read_text(encoding='utf-8'); start=s.index('  Future<List<Map<String, dynamic>>> productFeed({'); end=s.index('\n  }',start)+4; block=s[start:end]
 block=block.replace('    String? source,','    String? source,\n    int? minPrice,\n    int? maxPrice,\n    List<String> categories = const [],\n    List<String> sizes = const [],\n    List<String> colors = const [],')
 block=block.replace("{'limit': limit}","{\n      'limit': limit,\n      if (minPrice != null) 'min_price': minPrice,\n      if (maxPrice != null) 'max_price': maxPrice,\n      if (categories.isNotEmpty) 'categories': categories,\n      if (sizes.isNotEmpty) 'sizes': sizes,\n      if (colors.isNotEmpty) 'colors': colors,\n    }")
 block=block.replace("queryParameters: queryParameters);", "queryParameters: queryParameters, options: Options(listFormat: ListFormat.multi));")
 s=s[:start]+block+s[end:]; p.write_text(s,encoding='utf-8')
p=Path('lib/features/catalog/filters_screen.dart'); s=p.read_text(encoding='utf-8')
s=s.replace("final _sizes = ['XS', 'S', 'M', 'L', 'XL'];", "final _sizes = ['XS', 'S', 'M', 'L', 'XL', 'XXL', 'XXXL', '36', '37', '38', '39', '40', '41', '42', '43', '44', '46', '48', '50', '52', '54', '56'];")
s=s.replace('RangeValues _price = const RangeValues(1000, 20000);', '''RangeValues _price = const RangeValues(0, 100000);
  bool _priceEnabled = false;
  static const categoryKeys = {'Верхняя одежда': 'outerwear', 'Топы': 'tops', 'Брюки': 'bottoms', 'Обувь': 'shoes'};
  @override
  void initState() {
    super.initState();
    final c = widget.controller;
    _selCat.addAll(categoryKeys.keys.where((k) => c.feedFilterCategories.contains(categoryKeys[k])));
    _selSize.addAll(c.feedFilterSizes);
    _selColor.addAll(c.feedFilterColors);
    _priceEnabled = c.feedMinPrice != null || c.feedMaxPrice != null;
    _price = RangeValues((c.feedMinPrice ?? 0).toDouble().clamp(0, 100000), (c.feedMaxPrice ?? 100000).toDouble().clamp(0, 100000));
  }''')
s=s.replace('_price = const RangeValues(1000, 20000);','_price = const RangeValues(0, 100000);\n              _priceEnabled = false;')
s=s.replace('min: 500,','min: 0,').replace('max: 30000,','max: 100000,').replace('divisions: 59,','divisions: 200,').replace('onChanged: (v) => setState(() => _price = v),','onChanged: (v) => setState(() { _price = v; _priceEnabled = true; }),')
s=s.replace("'от ${_price.start.round()} ₽ до ${_price.end.round()} ₽',", "_priceEnabled ? 'от ${_price.start.round()} ₽ до ${_price.end.round()} ₽' : 'Любая цена',")
s=s.replace('minPrice: _price.start.round(),','minPrice: _priceEnabled ? _price.start.round() : null,').replace('maxPrice: _price.end.round(),','maxPrice: _priceEnabled ? _price.end.round() : null,').replace('sizes: _selSize.toList(),','categories: _selCat.map((c) => categoryKeys[c]!).toList(),\n                sizes: _selSize.toList(),')
s=s.replace('width: 30,\n                  height: 30,','width: 40,\n                  height: 40,')
s=s.replace('child: Container(\n                  width: 40,','child: Tooltip(message: c, child: Container(\n                  width: 40,').replace(': null,\n                ),', ': null,\n                )),')
p.write_text(s,encoding='utf-8')
p=Path('lib/features/recommendations/feed_screen.dart'); s=p.read_text(encoding='utf-8').replace('return c.feedMinPrice != null ||', 'return c.feedFilterCategories.isNotEmpty || c.feedMinPrice != null ||'); p.write_text(s,encoding='utf-8')
