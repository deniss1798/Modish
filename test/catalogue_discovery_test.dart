import 'dart:async';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/theme/app_theme.dart';
import 'package:modish/core/widgets/modish_widgets.dart';
import 'package:modish/features/catalog/discovery_screen.dart';
import 'package:modish/features/catalog/catalogue_widgets.dart';
import 'package:modish/features/catalog/search_screen.dart';
import 'package:modish/features/products/models.dart';
import 'package:modish/features/products/product_detail_screen.dart';
import 'package:modish/features/profile/profile_screen.dart';

import 'app_regressions_test.dart' show FakeApi;

Map<String, dynamic> discoveryProduct(String id) => {
  'id': id,
  'title': 'Товар $id',
  'brand': 'Магазин',
  'price': 5000,
  'image_url': '',
  'available_sizes': ['39'],
  'catalogue_discovery': true,
  'size_verification': 'not_verified',
  'size_notice': 'Размер нужно уточнить у магазина',
  'merchant_sizes': [
    {'raw_size': '39', 'size_system': 'UNKNOWN', 'availability': 'unknown'},
    {'raw_size': '40', 'size_system': 'EU', 'availability': 'available'},
    {'raw_size': '41', 'size_system': 'EU', 'availability': 'unavailable'},
  ],
};

Map<String, dynamic> discoveryPage(List<String> ids, {String? cursor}) => {
  'products': ids.map(discoveryProduct).toList(),
  'next_cursor': cursor,
  'has_more': cursor != null,
  'size_verification': 'not_verified',
  'mode': 'catalogue_discovery',
  'personalized': false,
};

class DiscoveryRequest {
  DiscoveryRequest(this.category, this.query, this.cursor);
  final String? category;
  final String? query;
  final String? cursor;
  final result = Completer<Map<String, dynamic>>();
}

class DiscoveryApi extends FakeApi {
  bool deferred = false;
  Map<String, dynamic> response = discoveryPage(['p1']);
  final requests = <DiscoveryRequest>[];
  final discoveryDetails = <String>[];
  int strictDetails = 0;
  Map<String, dynamic>? detailResponse;
  Object? detailError;
  Completer<Map<String, dynamic>>? detailGate;

  @override
  Future<List<Map<String, dynamic>>> productsList({
    int limit = 50,
    int offset = 0,
    String? category,
    String? source,
    String? brand,
    String? query,
  }) async => [];

  @override
  Future<Map<String, dynamic>> catalogueDiscovery({
    int limit = 20,
    String? category,
    String? query,
    String? source,
    int? minPrice,
    int? maxPrice,
    String? cursor,
  }) {
    final request = DiscoveryRequest(category, query, cursor);
    requests.add(request);
    if (!deferred) request.result.complete(response);
    return request.result.future;
  }

  @override
  Future<Map<String, dynamic>> catalogueDiscoveryProduct(String id) async {
    discoveryDetails.add(id);
    if (detailError != null) throw detailError!;
    if (detailGate != null) return detailGate!.future;
    return detailResponse ?? discoveryProduct(id);
  }

  @override
  Future<Map<String, dynamic>> productById(String id) async {
    strictDetails++;
    return discoveryProduct(id);
  }
}

AppController discoveryController(DiscoveryApi api) =>
    AppController(apiClient: api)
      ..activationStatus = {
        'recommendation_features': ['catalogue_discovery_v1'],
      };

Future<void> showCatalogue(
  WidgetTester tester,
  AppController controller, {
  String category = '',
  String query = '',
}) => tester.pumpWidget(
  MaterialApp(
    theme: AppTheme.dark,
    home: DiscoveryScreen(
      controller: controller,
      initialCategory: category,
      initialQuery: query,
    ),
  ),
);

Future<void> tapVisible(WidgetTester tester, Finder finder) async {
  await tester.scrollUntilVisible(
    finder,
    250,
    scrollable: find.byType(Scrollable).first,
  );
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
  await tester.tap(finder);
  await tester.pump();
}

Future<void> acceptCatalogue(WidgetTester tester, DiscoveryApi api) async {
  expect(api.requests, isEmpty);
  expect(api.discoveryDetails, isEmpty);
  await tapVisible(
    tester,
    find.byKey(const ValueKey('catalogue-consent-accept')),
  );
  expect(api.requests, hasLength(1));
}

void main() {
  test('Merchant metadata is an immutable snapshot and never infers units', () {
    final input = discoveryProduct('p1');
    final parsed = Product.fromApi(input);
    ((input['merchant_sizes'] as List).first as Map)['size_system'] = 'RU';
    expect(parsed.catalogueDiscovery, isTrue);
    expect(parsed.merchantSizes.first.sizeSystem, 'UNKNOWN');
    expect(parsed.merchantSizes.first.label, '39');
    expect(parsed.merchantSizes[1].label, '40 (EU)');
    expect(parsed.merchantSizes[2].availability, 'unavailable');
    expect(() => parsed.merchantSizes.clear(), throwsUnsupportedError);
    expect(parsed.recommendationFacts, isNot(contains('merchant_sizes')));
    expect(Product.fromApi({'id': 'strict'}).catalogueDiscovery, isFalse);
  });

  testWidgets(
    'Discovery shows honest merchant stock and opens dedicated detail without Taste',
    (tester) async {
      final api = DiscoveryApi();
      final controller = discoveryController(api);
      await showCatalogue(tester, controller, category: 'shoes');
      await acceptCatalogue(tester, api);
      await tester.pumpAndSettle();
      expect(api.requests.single.category, 'shoes');
      expect(find.text('Размер нужно уточнить у магазина'), findsOneWidget);
      expect(
        find.textContaining('Система размеров не указана'),
        findsOneWidget,
      );
      expect(find.textContaining('ваш размер'), findsNothing);
      await tapVisible(
        tester,
        find.byKey(const ValueKey('catalogue-product-p1')),
      );
      await tester.pumpAndSettle();
      expect(api.discoveryDetails, ['p1']);
      expect(api.strictDetails, 0);
      expect(api.relatedCalls, 0);
      expect(api.eventCalls, 0);
      expect(find.byIcon(Icons.favorite_border), findsNothing);
      expect(find.text('Почему рекомендовано'), findsNothing);
      expect(find.text('С чем носить'), findsNothing);
      await tester.scrollUntilVisible(
        find.text('Открыть магазин'),
        250,
        scrollable: find.byType(Scrollable).first,
      );
      // The merchant launcher is covered separately by its injected transport.
      await tester.pumpAndSettle();
      expect(api.eventCalls, 0);
      expect(find.text('Размеры магазина'), findsOneWidget);
      expect(find.text('40 (EU)'), findsOneWidget);
      expect(find.text('41 (EU) · нет в наличии'), findsOneWidget);
      expect(find.byType(ChoiceChip), findsNothing);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets(
    'Category and query changes discard stale pages and reset the cursor',
    (tester) async {
      final api = DiscoveryApi()..deferred = true;
      final controller = discoveryController(api);
      await showCatalogue(tester, controller);
      await acceptCatalogue(tester, api);
      await tester.tap(find.byKey(const ValueKey('catalogue-category-shoes')));
      await tester.pump();
      expect(api.requests, hasLength(2));
      api.requests[1].result.complete(
        discoveryPage(['shoe'], cursor: 'shoe-next'),
      );
      await tester.pumpAndSettle();
      api.requests[0].result.complete(discoveryPage(['stale']));
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('catalogue-product-shoe')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('catalogue-product-stale')),
        findsNothing,
      );
      await tester.enterText(
        find.byKey(const ValueKey('catalogue-query')),
        'ботинки',
      );
      await tester.pump(const Duration(milliseconds: 460));
      expect(api.requests.last.query, 'ботинки');
      expect(api.requests.last.cursor, isNull);
      api.requests.last.result.complete(discoveryPage(['boot']));
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('catalogue-product-shoe')),
        findsNothing,
      );
      expect(
        find.byKey(const ValueKey('catalogue-product-boot')),
        findsOneWidget,
      );
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets(
    'Pagination coalesces taps, preserves stock on error and suppresses duplicates',
    (tester) async {
      final api = DiscoveryApi()
        ..response = discoveryPage(['p1'], cursor: 'opaque-next');
      final controller = discoveryController(api);
      await showCatalogue(tester, controller);
      await acceptCatalogue(tester, api);
      await tester.pumpAndSettle();
      api.deferred = true;
      final more = find.byKey(const ValueKey('catalogue-more'));
      await tester.scrollUntilVisible(
        more,
        250,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.tap(more);
      await tester.tap(more);
      await tester.pump();
      expect(api.requests, hasLength(2));
      expect(api.requests.last.cursor, 'opaque-next');
      api.requests.last.result.completeError(Exception('offline'));
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('catalogue-product-p1')),
        findsOneWidget,
      );
      await tapVisible(tester, find.text('Повторить'));
      expect(api.requests.last.cursor, 'opaque-next');
      api.requests.last.result.complete(discoveryPage(['p1', 'p2']));
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('catalogue-product-p1')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('catalogue-product-p2')),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('catalogue-more')), findsNothing);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets(
    'A stale opaque cursor offers a fresh catalogue instead of replaying it',
    (tester) async {
      final api = DiscoveryApi()
        ..response = discoveryPage(['p1'], cursor: 'expired');
      final controller = discoveryController(api);
      await showCatalogue(tester, controller);
      await acceptCatalogue(tester, api);
      await tester.pumpAndSettle();
      api.deferred = true;
      await tapVisible(tester, find.byKey(const ValueKey('catalogue-more')));
      final options = RequestOptions(path: '/catalog/discovery');
      api.requests.last.result.completeError(
        DioException(
          requestOptions: options,
          response: Response(requestOptions: options, statusCode: 422),
        ),
      );
      await tester.pumpAndSettle();
      await tapVisible(tester, find.text('Обновить каталог'));
      expect(api.requests.last.cursor, isNull);
      api.requests.last.result.complete(discoveryPage(['fresh']));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('catalogue-product-p1')), findsNothing);
      expect(
        find.byKey(const ValueKey('catalogue-product-fresh')),
        findsOneWidget,
      );
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets(
    'Logout clears catalogue state and ignores an old in-flight page',
    (tester) async {
      const storage = MethodChannel(
        'plugins.it_nomads.com/flutter_secure_storage',
      );
      tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        storage,
        (_) async => null,
      );
      addTearDown(
        () => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
          storage,
          null,
        ),
      );
      final api = DiscoveryApi()..deferred = true;
      final controller = discoveryController(api);
      await showCatalogue(tester, controller);
      await acceptCatalogue(tester, api);
      api.onUnauthorized!.call();
      api.requests.single.result.complete(discoveryPage(['old-account']));
      await tester.pumpAndSettle();
      expect(
        find.text('Вы сменили аккаунт. Откройте каталог заново.'),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('catalogue-product-old-account')),
        findsNothing,
      );
      expect(find.text('Повторить'), findsNothing);
      expect(api.requests, hasLength(1));
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets(
    'Discovery empty and invalid response states offer a working retry',
    (tester) async {
      final api = DiscoveryApi()..response = discoveryPage([]);
      final controller = discoveryController(api);
      await showCatalogue(tester, controller);
      await acceptCatalogue(tester, api);
      await tester.pumpAndSettle();
      expect(find.text('Пока ничего не найдено'), findsOneWidget);
      api.response = {
        'products': [discoveryProduct('unverified')],
        'personalized': true,
      };
      await tester.tap(find.byKey(const ValueKey('catalogue-category-shoes')));
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('catalogue-product-unverified')),
        findsNothing,
      );
      expect(find.text('Повторить'), findsOneWidget);
      api.response = discoveryPage(['good']);
      await tapVisible(tester, find.text('Повторить'));
      await tester.pumpAndSettle();
      expect(api.requests.last.category, 'shoes');
      expect(
        find.byKey(const ValueKey('catalogue-product-good')),
        findsOneWidget,
      );
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets(
    'Discovery detail blocks old account actions and ignores its late refresh',
    (tester) async {
      const storage = MethodChannel(
        'plugins.it_nomads.com/flutter_secure_storage',
      );
      tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        storage,
        (_) async => null,
      );
      addTearDown(
        () => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
          storage,
          null,
        ),
      );
      final api = DiscoveryApi()
        ..detailGate = Completer<Map<String, dynamic>>();
      final controller = discoveryController(api);
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.dark,
          home: ProductDetailScreen(
            controller: controller,
            card: FeedCard(
              product: Product.fromApi(discoveryProduct('p1')),
              reason: '',
            ),
          ),
        ),
      );
      api.onUnauthorized!.call();
      api.detailGate!.complete({
        ...discoveryProduct('p1'),
        'title': 'Поздний ответ старой сессии',
      });
      await tester.pumpAndSettle();
      expect(find.text('Поздний ответ старой сессии'), findsNothing);
      expect(
        find.text('Вы сменили аккаунт. Откройте товар заново.'),
        findsOneWidget,
      );
      await tester.scrollUntilVisible(
        find.text('Открыть магазин'),
        250,
        scrollable: find.byType(Scrollable).first,
      );
      final button = tester.widget<PrimaryButton>(
        find.ancestor(
          of: find.text('Открыть магазин'),
          matching: find.byType(PrimaryButton),
        ),
      );
      expect(button.onPressed, isNull);
      expect(api.relatedCalls, 0);
      expect(api.eventCalls, 0);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets(
    'Discovery detail remains honest after refresh, and blocks a rejected offer',
    (tester) async {
      final api = DiscoveryApi();
      final controller = discoveryController(api);
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.dark,
          home: ProductDetailScreen(
            controller: controller,
            card: FeedCard(
              product: Product.fromApi(discoveryProduct('p1')),
              reason: 'Есть ваш размер',
              reasons: const ['Есть ваш размер'],
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Есть ваш размер'), findsNothing);
      await tester.tap(find.byTooltip('Обновить карточку'));
      await tester.pumpAndSettle();
      expect(api.discoveryDetails, ['p1', 'p1']);
      expect(api.strictDetails, 0);
      api.detailError = Exception('product is no longer admitted');
      await tester.tap(find.byTooltip('Обновить карточку'));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.text('Открыть магазин'),
        250,
        scrollable: find.byType(Scrollable).first,
      );
      final button = tester.widget<PrimaryButton>(
        find.ancestor(
          of: find.text('Открыть магазин'),
          matching: find.byType(PrimaryButton),
        ),
      );
      expect(button.onPressed, isNull);
      expect(find.text('Размер нужно уточнить у магазина'), findsOneWidget);
      expect(api.relatedCalls, 0);
      expect(api.eventCalls, 0);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets(
    'Unknown merchant units remain visible in a compact card at larger text',
    (tester) async {
      tester.view.physicalSize = const Size(360, 800);
      tester.view.devicePixelRatio = 1;
      addTearDown(() {
        tester.view.resetPhysicalSize();
        tester.view.resetDevicePixelRatio();
      });
      final api = DiscoveryApi();
      final controller = discoveryController(api);
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.dark,
          builder: (context, child) => MediaQuery(
            data: MediaQuery.of(
              context,
            ).copyWith(textScaler: TextScaler.linear(1.6)),
            child: child!,
          ),
          home: DiscoveryScreen(controller: controller),
        ),
      );
      await acceptCatalogue(tester, api);
      await tester.pumpAndSettle();
      final card = find.byKey(const ValueKey('catalogue-product-p1'));
      await tester.scrollUntilVisible(
        card,
        250,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.pumpAndSettle();
      final units = find.descendant(
        of: card,
        matching: find.text('Система размеров не указана'),
      );
      expect(units, findsOneWidget);
      final label = tester.widget<Text>(units);
      expect(label.maxLines, isNull);
      final cardBounds = tester.getRect(card);
      final unitBounds = tester.getRect(units);
      expect(unitBounds.top, greaterThanOrEqualTo(cardBounds.top));
      expect(unitBounds.bottom, lessThanOrEqualTo(cardBounds.bottom));
      expect(find.byType(MerchantSizeSummary), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets('Empty search can browse the catalogue with its existing query', (
    tester,
  ) async {
    final api = DiscoveryApi();
    final controller = discoveryController(api);
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.dark,
        home: SearchScreen(controller: controller, initialQuery: 'ботинки'),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Ничего не найдено'), findsOneWidget);
    await tester.tap(find.text('Посмотреть каталог'));
    await tester.pumpAndSettle();
    expect(find.byType(DiscoveryScreen), findsOneWidget);
    await acceptCatalogue(tester, api);
    expect(api.requests.single.query, 'ботинки');
    expect(api.eventCalls, 0);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });

  for (final supported in [false, true]) {
    testWidgets(
      'Profile and empty search expose discovery only with capability: $supported',
      (tester) async {
        final api = DiscoveryApi();
        final controller = supported
            ? discoveryController(api)
            : AppController(apiClient: api);
        await tester.pumpWidget(
          MaterialApp(
            theme: AppTheme.dark,
            home: Scaffold(body: ProfileScreen(controller: controller)),
          ),
        );
        if (supported) {
          await tester.scrollUntilVisible(
            find.text('Каталог магазинов'),
            250,
            scrollable: find.byType(Scrollable).first,
          );
          expect(find.text('Каталог магазинов'), findsOneWidget);
        } else {
          expect(find.text('Каталог магазинов'), findsNothing);
        }
        await tester.pumpWidget(
          MaterialApp(
            theme: AppTheme.dark,
            home: SearchScreen(controller: controller),
          ),
        );
        await tester.pumpAndSettle();
        expect(
          find.text('Посмотреть каталог'),
          supported ? findsOneWidget : findsNothing,
        );
        await tester.pumpWidget(const SizedBox());
        controller.dispose();
      },
    );
  }
}
