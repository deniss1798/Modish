from pathlib import Path
p = Path('lib/app.dart')
s = p.read_text(encoding='utf-8').replace("import 'features/products/outfit_slots.dart' as slots;\n", '')
start = s.index('  List<prod.FeedCard> relatedProducts(')
end = s.index('  Future<void> boot()', start)
s = s[:start] + '''  final _related = <String, List<prod.FeedCard>>{};
  final _relatedLoading = <String>{};
  int _relatedGeneration = 0;

  List<prod.FeedCard> relatedProducts(String productId, {int limit = 6}) =>
      (_related[productId] ?? const <prod.FeedCard>[]).take(limit).toList();

  String relatedProductsHint(String productId) => 'Дополнения к этой вещи';

  Future<void> loadRelatedProducts(String productId) async {
    if (_related.containsKey(productId) || !_relatedLoading.add(productId)) return;
    final generation = _relatedGeneration;
    try {
      final rows = await api.productComplements(productId);
      if (generation != _relatedGeneration) return;
      final seen = <String>{'id:$productId'};
      _related[productId] = rows.map((row) => prod.FeedCard(
        product: prod.Product.fromApi(row), reason: 'Дополняет образ',
      )).where((card) {
        final keys = card.product.identityKeys;
        if (keys.any(seen.contains)) return false;
        seen.addAll(keys);
        return true;
      }).toList();
      while (_related.length > 20) {
        _related.remove(_related.keys.first);
      }
      notifyListeners();
    } catch (_) {
      // A missing complement must never be replaced by an unrelated feed item.
    } finally {
      if (generation == _relatedGeneration) _relatedLoading.remove(productId);
    }
  }

  void _clearRelated() {
    _relatedGeneration++;
    _related.clear();
    _relatedLoading.clear();
  }

''' + s[end:]
s = s.replace('    _detailViewSent.clear();', '    _clearRelated();\n    _detailViewSent.clear();')
s = s.replace('      styleTarget = genderTarget;\n      await refreshRemoteData();', '      styleTarget = genderTarget;\n      _clearRelated();\n      await refreshRemoteData();')
p.write_text(s, encoding='utf-8')
