import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/features/products/models.dart';
import 'package:modish/features/products/product_detail_screen.dart';

import 'app_regressions_test.dart' show FakeApi;

Map<String, dynamic> _productFacts() => {
  'id': 'detail-product',
  'title': 'Прямые хлопковые брюки',
  'brand': 'Modish',
  'category': 'брюки',
  'category_name': 'Брюки',
  'subcategory': 'брюки прямые',
  'merchant_category': 'Одежда',
  'merchant_subcategory': 'Брюки',
  'outfit_slot': 'bottom',
  'price': 5000,
  'currency': 'RUB',
  'image_url': '',
  'product_url': 'https://merchant.example/products/trousers',
  'available_sizes': ['48', '50'],
  'available_sizes_detailed': [
    {'raw_size': '48', 'size_system': 'RU', 'availability': 'available'},
    {'raw_size': '50', 'size_system': 'RU', 'availability': 'available'},
  ],
  'size_system': 'RU',
  'size_original': '48;50',
  'colors': ['black'],
  'color_family': 'black',
  'color_original': 'чёрный',
  'material': 'cotton',
  'fit': 'regular',
  'silhouette': 'straight',
  'season': 'all_season',
  'occasion': 'office',
  'gender_target': 'menswear',
  'description': 'Прямые брюки из хлопка.',
  'style_tags': ['minimalism'],
  'raw_params_json': {'Состав': '100% хлопок', 'size_system': 'RU'},
  'recommendation_evidence_version': 'evidence-version-a',
  'availability_status': 'in_stock',
  'availability_state': 'available',
  'availability_confidence': 1.0,
  'is_available': true,
  'is_active': true,
  'is_deleted_from_feed': false,
};

const _tasteReason = 'В прошлых действиях вы чаще выбирали хлопок';
const _sizeReason = 'Размер RU 48 указан в наличии';
const _priceReason = 'Цена в пределах 7 000 ₽';
const _fallbackReason = 'Подобрано под ваш профиль';

class _DetailApi extends FakeApi {
  Map<String, dynamic> latest = _productFacts();
  bool failRefresh = false;
  final eventMetadata = <Map<String, dynamic>>[];

  @override
  Future<Map<String, dynamic>> productById(String id) async {
    expect(id, 'detail-product');
    if (failRefresh) throw Exception('offline');
    return latest;
  }

  @override
  Future<Map<String, dynamic>> recordRecommendationEvent({
    required String eventType,
    String? productId,
    String? outfitId,
    Map<String, dynamic>? meta,
  }) async {
    eventMetadata.add({'event': eventType, ...?meta});
    return {};
  }
}

Future<AppController> _showDetail(
  WidgetTester tester,
  _DetailApi api, {
  FeedCard? card,
  Map<String, dynamic> eventMeta = const {},
}) async {
  final controller = AppController(apiClient: api)..tab = 1;
  await tester.pumpWidget(
    MaterialApp(
      home: ProductDetailScreen(
        controller: controller,
        card:
            card ??
            FeedCard.fromApi({
              'product': _productFacts(),
              'reason': _fallbackReason,
              'reasons': [_sizeReason, _priceReason, _tasteReason],
            }),
        eventMeta: eventMeta,
      ),
    ),
  );
  await tester.pumpAndSettle();
  return controller;
}

void _expectReasonsRetained() {
  expect(find.text('Почему рекомендовано'), findsOneWidget);
  expect(find.text(_sizeReason), findsOneWidget);
  expect(find.text(_priceReason), findsOneWidget);
  expect(find.text(_tasteReason), findsOneWidget);
  expect(find.text(_fallbackReason), findsNothing);
}

void _expectNoExplanations() {
  expect(find.text('Почему рекомендовано'), findsNothing);
  expect(find.text(_sizeReason), findsNothing);
  expect(find.text(_priceReason), findsNothing);
  expect(find.text(_tasteReason), findsNothing);
  expect(find.text(_fallbackReason), findsNothing);
  expect(find.text('Подходит под ваш профиль'), findsNothing);
}

Future<void> _disposeDetail(
  WidgetTester tester,
  AppController controller,
) async {
  expect(tester.takeException(), isNull);
  await tester.pumpWidget(const SizedBox());
  controller.dispose();
}

void main() {
  testWidgets('Refresh preserves unchanged discovery explanations', (
    tester,
  ) async {
    final api = _DetailApi();
    final controller = await _showDetail(tester, api);
    _expectReasonsRetained();

    await tester.tap(find.byTooltip('Обновить карточку'));
    await tester.pumpAndSettle();
    _expectReasonsRetained();
    await _disposeDetail(tester, controller);
  });

  testWidgets('Refresh invalidates a change in hidden evidence version alone', (
    tester,
  ) async {
    final api = _DetailApi()
      ..latest['recommendation_evidence_version'] = 'evidence-version-b';
    final controller = await _showDetail(tester, api);
    _expectNoExplanations();
    await _disposeDetail(tester, controller);
  });

  testWidgets(
    'Refresh preserves explanations with unchanged evidence version',
    (tester) async {
      final api = _DetailApi();
      final controller = await _showDetail(tester, api);
      _expectReasonsRetained();
      await _disposeDetail(tester, controller);
    },
  );

  testWidgets(
    'Refresh preserves unchanged task explanations and event context',
    (tester) async {
      final api = _DetailApi();
      const metadata = {
        'surface': 'shopping_intent',
        'intent_id': 'task-id',
        'intent_version': 3,
        'intent_outfit_id': 'look-id',
      };
      final controller = await _showDetail(tester, api, eventMeta: metadata);
      _expectReasonsRetained();
      await tester.tap(find.byIcon(Icons.favorite_border));
      await tester.pumpAndSettle();
      expect(api.eventMetadata, [
        {'event': 'like', ...metadata},
      ]);
      await _disposeDetail(tester, controller);
    },
  );

  const changes = <String, dynamic>{
    'price': 8000,
    'currency': 'USD',
    'available_sizes': ['50'],
    'available_sizes_detailed': [
      {'raw_size': '48', 'size_system': 'RU', 'availability': 'unavailable'},
      {'raw_size': '50', 'size_system': 'RU', 'availability': 'available'},
    ],
    'size_system': 'EU',
    'size_original': '50',
    'colors': ['white'],
    'color_family': 'white',
    'color_original': 'белый',
    'category': 'юбки',
    'category_name': 'Юбки',
    'subcategory': 'юбки миди',
    'merchant_category': 'Аксессуары',
    'merchant_subcategory': 'Юбки',
    'outfit_slot': 'onepiece',
    'brand': 'Another brand',
    'title': 'Шерстяные брюки',
    'description': 'Прямые брюки из шерсти.',
    'material': 'wool',
    'fit': 'slim',
    'silhouette': 'wide',
    'season': 'winter',
    'occasion': 'party',
    'gender_target': 'womenswear',
    'style_tags': ['sport'],
    'raw_params_json': {'Состав': '100% шерсть'},
    'availability_state': 'unknown',
    'availability_status': 'out_of_stock',
    'availability_confidence': 0.0,
    'is_available': false,
    'is_active': false,
    'is_deleted_from_feed': true,
  };
  for (final change in changes.entries) {
    testWidgets('Refresh invalidates explanations when ${change.key} changes', (
      tester,
    ) async {
      final api = _DetailApi()..latest[change.key] = change.value;
      final controller = await _showDetail(tester, api);
      _expectNoExplanations();

      // A second unchanged refresh must not revive the old fallback reason.
      await tester.tap(find.byTooltip('Обновить карточку'));
      await tester.pumpAndSettle();
      _expectNoExplanations();
      await _disposeDetail(tester, controller);
    });
  }

  testWidgets('Presentation and observation timestamps keep factual reasons', (
    tester,
  ) async {
    final api = _DetailApi()
      ..latest.addAll({
        'shop_label': 'Новая витрина',
        'product_url':
            'https://merchant.example/products/trousers?utm_source=app',
        'image_url': 'https://placehold.co/updated.png',
        'image_urls': ['https://placehold.co/gallery.png'],
        'last_checked_at': '2026-10-11T10:00:00Z',
        'last_seen_in_feed_at': '2026-10-11T10:00:00Z',
      });
    final controller = await _showDetail(tester, api);
    _expectReasonsRetained();
    await _disposeDetail(tester, controller);
  });

  testWidgets('Refresh preserves a factual legacy single reason', (
    tester,
  ) async {
    final api = _DetailApi();
    final controller = await _showDetail(
      tester,
      api,
      card: FeedCard.fromApi({
        'product': _productFacts(),
        'reason': _sizeReason,
      }),
    );
    expect(find.text(_sizeReason), findsOneWidget);
    await _disposeDetail(tester, controller);
  });

  testWidgets('Refresh drops explanations without an original fact snapshot', (
    tester,
  ) async {
    final api = _DetailApi();
    final controller = await _showDetail(
      tester,
      api,
      card: const FeedCard(
        product: Product(
          id: 'detail-product',
          title: 'Прямые хлопковые брюки',
          brand: 'Modish',
          shopLabel: 'Modish',
          category: 'брюки',
          price: 5000,
          currency: 'RUB',
          imageUrl: '',
          productUrl: 'https://merchant.example/products/trousers',
          colors: ['black'],
          availableSizes: ['48', '50'],
        ),
        reason: _fallbackReason,
        reasons: [_tasteReason],
      ),
    );
    _expectNoExplanations();
    await _disposeDetail(tester, controller);
  });

  for (final fallback in [null, _fallbackReason, 'Подходит под ваш профиль']) {
    testWidgets('Refresh does not advertise marketing fallback $fallback', (
      tester,
    ) async {
      final api = _DetailApi();
      final controller = await _showDetail(
        tester,
        api,
        card: FeedCard.fromApi({
          'product': _productFacts(),
          'reason': ?fallback,
        }),
      );
      _expectNoExplanations();
      await _disposeDetail(tester, controller);
    });
  }

  testWidgets('Refresh invalidates evidence removed from the latest product', (
    tester,
  ) async {
    final api = _DetailApi()..latest.remove('raw_params_json');
    final controller = await _showDetail(tester, api);
    _expectNoExplanations();
    await _disposeDetail(tester, controller);
  });

  testWidgets('Original snapshot survives mutation of the incoming payload', (
    tester,
  ) async {
    final rawProduct = _productFacts();
    final card = FeedCard.fromApi({
      'product': rawProduct,
      'reason': _fallbackReason,
      'reasons': [_sizeReason, _priceReason, _tasteReason],
    });
    (rawProduct['raw_params_json'] as Map)['Состав'] = '100% шерсть';
    final api = _DetailApi()..latest = rawProduct;
    final controller = await _showDetail(tester, api, card: card);
    _expectNoExplanations();
    await _disposeDetail(tester, controller);
  });

  testWidgets('Failed refresh retains the original explanations', (
    tester,
  ) async {
    final api = _DetailApi()..failRefresh = true;
    final controller = await _showDetail(tester, api);
    _expectReasonsRetained();
    await _disposeDetail(tester, controller);
  });
}
