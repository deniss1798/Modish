import '../../core/network/api_client.dart';
import '../products/models.dart';

/// A request owns its selections; later edits in a filter sheet cannot change it.
class FeedQuery {
  FeedQuery({
    this.scenario = 'daily',
    this.minPrice,
    this.maxPrice,
    List<String> categories = const [],
    List<String> sizes = const [],
    List<String> colors = const [],
  }) : categories = List.unmodifiable(categories),
       sizes = List.unmodifiable(sizes),
       colors = List.unmodifiable(colors);

  final String scenario;
  final int? minPrice;
  final int? maxPrice;
  final List<String> categories;
  final List<String> sizes;
  final List<String> colors;

  Future<List<FeedCard>> fetch(
    ApiClient api, {
    required Iterable<String> excludeIds,
  }) async {
    final rows = await api.productFeed(
      limit: 30,
      scenario: scenario,
      minPrice: minPrice,
      maxPrice: maxPrice,
      categories: categories,
      sizes: sizes,
      colors: colors,
      excludeIds: excludeIds.toSet().toList(),
    );
    return rows.map(FeedCard.fromApi).toList();
  }
}

/// Call at response time with the latest queue/history, so a slow refill or
/// rerank never restores a card dismissed while the request was in flight.
List<FeedCard> mergeFeedCards(
  Iterable<FeedCard> cards, {
  required Set<String> dismissedKeys,
}) {
  final seen = {...dismissedKeys};
  return cards.where((card) {
    final keys = card.product.identityKeys;
    if (keys.any(seen.contains)) return false;
    seen.addAll(keys);
    return true;
  }).toList();
}
