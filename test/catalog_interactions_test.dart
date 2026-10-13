import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/features/catalog/search_screen.dart';
import 'package:modish/features/products/models.dart';
import 'package:modish/features/products/product_detail_screen.dart';
import 'package:modish/features/profile/profile_screen.dart';

import 'app_regressions_test.dart' show FakeApi;
import 'support/fixture_http.dart';

Map<String, dynamic> catalogueProduct(String id) => {
  'id': id,
  'title': id == 'anchor' ? 'Кардиган первого товара' : 'Брюки второго товара',
  'brand': 'BAON',
  'price': 5000,
  'image_url': '',
  'description': 'Описание товара',
  'colors': ['navy', 'merchant_special_lilac'],
};

class CatalogueApi extends FakeApi {
  String imageUrl = '';
  final queries = <String?>[];
  final details = <String>[];
  final events = <Map<String, dynamic>>[];

  @override
  Future<List<Map<String, dynamic>>> productsBrands({int limit = 80}) async => [
    {'brand': 'BAON', 'count': 12},
  ];

  @override
  Future<List<Map<String, dynamic>>> productsList({
    int limit = 50,
    int offset = 0,
    String? category,
    String? source,
    String? brand,
    String? query,
  }) async {
    queries.add(query);
    return [catalogueProduct('related')];
  }

  @override
  Future<Map<String, dynamic>> productById(String id) async {
    details.add(id);
    return {...catalogueProduct(id), 'image_url': imageUrl};
  }

  @override
  Future<List<Map<String, dynamic>>> productComplements(String id) async =>
      id == 'anchor'
      ? [
          {...catalogueProduct('related'), 'image_url': imageUrl},
        ]
      : [];

  @override
  Future<Map<String, dynamic>> recordRecommendationEvent({
    required String eventType,
    String? productId,
    String? outfitId,
    Map<String, dynamic>? meta,
  }) async {
    events.add({'event': eventType, 'product_id': productId, ...?meta});
    return {};
  }
}

void main() {
  testWidgets(
    'Related photo opens its detail through an offline image transport',
    (tester) async {
      final previousHttp = HttpOverrides.current;
      late FixtureHttpOverrides fixture;
      await tester.runAsync(() async {
        fixture = FixtureHttpOverrides(
          await File('test/fixtures/build12-blouse.jpg').readAsBytes(),
        );
        HttpOverrides.global = fixture;
      });
      addTearDown(() => HttpOverrides.global = previousHttp);
      final api = CatalogueApi()
        ..imageUrl = 'https://ui-fixture.invalid/catalogue-flow.jpg';
      final controller = AppController(apiClient: api);
      await tester.pumpWidget(
        MaterialApp(
          home: ProductDetailScreen(
            controller: controller,
            card: FeedCard(
              product: Product.fromApi(catalogueProduct('anchor')),
              reason: '',
            ),
          ),
        ),
      );
      await tester.runAsync(
        () => Future<void>.delayed(const Duration(milliseconds: 100)),
      );
      await tester.pumpAndSettle();
      final related = find.byKey(const ValueKey('related-product-related'));
      await tester.scrollUntilVisible(
        related,
        300,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.pumpAndSettle();
      expect(
        find.descendant(of: related, matching: find.byType(RawImage)),
        findsOneWidget,
      );
      await tester.tap(related);
      await tester.pumpAndSettle();
      expect(api.details, ['anchor', 'related']);
      expect(api.events.single['reference_product_id'], 'anchor');
      expect(fixture.requests, isNotEmpty);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets(
    'Choosing a profile brand opens its search results without typing',
    (tester) async {
      final api = CatalogueApi();
      final controller = AppController(apiClient: api);
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(body: ProfileScreen(controller: controller)),
        ),
      );
      await tester.scrollUntilVisible(
        find.text('Бренды'),
        300,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Бренды'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('BAON'));
      await tester.pumpAndSettle();
      expect(find.byType(SearchScreen), findsOneWidget);
      expect(api.queries, ['BAON']);
      expect(
        tester.widget<TextField>(find.byType(TextField)).controller!.text,
        'BAON',
      );
      expect(find.text('Брюки второго товара'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets(
    'Related detail item opens its product and records complement attribution',
    (tester) async {
      final api = CatalogueApi();
      final controller = AppController(apiClient: api);
      await tester.pumpWidget(
        MaterialApp(
          home: ProductDetailScreen(
            controller: controller,
            card: FeedCard(
              product: Product.fromApi(catalogueProduct('anchor')),
              reason: '',
            ),
            eventMeta: const {
              'surface': 'shopping_intent',
              'intent_id': 'old-task',
            },
          ),
        ),
      );
      await tester.pumpAndSettle();
      final related = find.byKey(const ValueKey('related-product-related'));
      await tester.scrollUntilVisible(
        related,
        300,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.pumpAndSettle();
      await tester.tap(related);
      await tester.pumpAndSettle();
      expect(api.details, ['anchor', 'related']);
      expect(api.events, [
        {
          'event': 'open_product',
          'product_id': 'related',
          'surface': 'product_detail',
          'placement': 'complement',
          'reference_product_id': 'anchor',
        },
      ]);
      expect(api.events.single, isNot(contains('intent_id')));
      await tester.scrollUntilVisible(
        find.text('Брюки второго товара'),
        200,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.pumpAndSettle();
      expect(find.text('Брюки второго товара'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets(
    'Known colour labels are readable and unknown merchant values stay exact',
    (tester) async {
      final api = CatalogueApi();
      final payload = catalogueProduct('related');
      final controller = AppController(apiClient: api);
      await tester.pumpWidget(
        MaterialApp(
          home: ProductDetailScreen(
            controller: controller,
            card: FeedCard(product: Product.fromApi(payload), reason: ''),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Тёмно-синий'), findsOneWidget);
      expect(find.text('merchant_special_lilac'), findsOneWidget);
      expect(find.text('navy'), findsNothing);
      expect(payload['colors'], ['navy', 'merchant_special_lilac']);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );
}
