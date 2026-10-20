import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/presentation/catalog_availability.dart';
import 'package:modish/core/theme/app_theme.dart';
import 'package:modish/features/catalog/brands_screen.dart';
import 'package:modish/features/outfits/outfits_screen.dart';

import 'app_regressions_test.dart' show FakeApi, product;

Map<String, dynamic> _status(String value) => {
  'status': value,
  'scope': 'global',
  'personalized': false,
};

class _CatalogApi extends FakeApi {
  int statusCalls = 0;
  int brandsCalls = 0;
  String status = 'refresh_required';
  bool statusFails = false;
  bool brandsHaveItems = false;
  Completer<Map<String, dynamic>>? statusGate;

  @override
  Future<Map<String, dynamic>> catalogStatus() {
    statusCalls++;
    if (statusFails) return Future.error(Exception('diagnostic unavailable'));
    return statusGate?.future ?? Future.value(_status(status));
  }

  @override
  Future<List<Map<String, dynamic>>> productsBrands({int limit = 80}) async {
    brandsCalls++;
    return brandsHaveItems
        ? [
            {'brand': 'BAON', 'count': 3},
          ]
        : [];
  }

  @override
  Future<void> logoutSession() async {}
}

AppController _controller(_CatalogApi api, {bool supported = true}) =>
    AppController(apiClient: api)
      ..activationStatus = {
        'recommendation_features': [if (supported) 'catalog_status_v1'],
      };

Future<void> _screen(WidgetTester tester, Widget child) async {
  tester.view.physicalSize = const Size(360, 640);
  tester.view.devicePixelRatio = 1;
  tester.view.padding = const FakeViewPadding(top: 24, bottom: 48);
  tester.view.viewPadding = const FakeViewPadding(top: 24, bottom: 48);
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPadding);
  addTearDown(tester.view.resetViewPadding);
  await tester.pumpWidget(MaterialApp(theme: AppTheme.dark, home: child));
  await tester.pumpAndSettle();
}

void main() {
  test('Global diagnostics cannot promise a personal match', () {
    expect(
      CatalogAvailability.fromApi(_status('ready')),
      CatalogAvailability.ready,
    );
    expect(CatalogAvailability.ready.blocksSelection, isFalse);
    for (final value in [
      {'status': 'refresh_required'},
      {..._status('empty'), 'personalized': true},
      {..._status('empty'), 'scope': 'user'},
      _status('unexpected'),
    ]) {
      expect(CatalogAvailability.fromApi(value), CatalogAvailability.unknown);
    }
  });

  test('Legacy capability never makes a request to the new endpoint', () async {
    final api = _CatalogApi();
    final controller = _controller(api, supported: false);
    addTearDown(controller.dispose);
    final result = await controller.refreshFeedFromUser();
    expect(api.statusCalls, 0);
    expect(controller.productFeedError, isNull);
    expect(controller.catalogAvailability, CatalogAvailability.unknown);
    expect(result, isNot(contains('просмотрели')));
  });

  test('Empty feed keeps profile and reads the global reason', () async {
    final api = _CatalogApi();
    final controller = _controller(api)
      ..fitProfile = {
        'height': 166,
        'clothing_size': 'L',
        'shoe_size': 'EU 38',
        'budget_max': 100000,
      };
    addTearDown(controller.dispose);
    final message = await controller.refreshFeedFromUser();
    expect(api.statusCalls, 1);
    expect(controller.catalogAvailability, CatalogAvailability.refreshRequired);
    expect(controller.productFeedError, isNull);
    expect(message, contains('данные магазинов'));
    expect(controller.fitProfile['clothing_size'], 'L');
    expect(controller.fitProfile['budget_max'], 100000);
  });

  test(
    'Diagnostic failure stays neutral and is not a feed network error',
    () async {
      final api = _CatalogApi()..statusFails = true;
      final controller = _controller(api);
      addTearDown(controller.dispose);
      final message = await controller.refreshFeedFromUser();
      expect(controller.catalogAvailability, CatalogAvailability.unknown);
      expect(controller.productFeedError, isNull);
      expect(message, isNot(contains('обновить данные магазинов')));
    },
  );

  test(
    'Pending status is coalesced and cannot cross account boundaries',
    () async {
      final firstGate = Completer<Map<String, dynamic>>();
      final secondGate = Completer<Map<String, dynamic>>();
      final api = _CatalogApi()..statusGate = firstGate;
      final controller = _controller(api);
      addTearDown(controller.dispose);
      final old = controller.refreshCatalogAvailability();
      final coalesced = controller.refreshCatalogAvailability();
      expect(api.statusCalls, 1);
      await controller.logout();
      controller.activationStatus = {
        'recommendation_features': ['catalog_status_v1'],
      };
      api.statusGate = secondGate;
      final current = controller.refreshCatalogAvailability();
      expect(api.statusCalls, 2);
      firstGate.complete(_status('refresh_required'));
      await Future.wait([old, coalesced]);
      expect(controller.catalogAvailability, CatalogAvailability.unknown);
      final alsoCurrent = controller.refreshCatalogAvailability();
      expect(api.statusCalls, 2);
      secondGate.complete(_status('ready'));
      await Future.wait([current, alsoCurrent]);
      expect(controller.catalogAvailability, CatalogAvailability.ready);
    },
  );

  test(
    'Fresh feed supersedes an outage and a late negative diagnostic',
    () async {
      final api = _CatalogApi();
      final controller = _controller(api);
      addTearDown(controller.dispose);
      await controller.refreshFeedFromUser();
      expect(
        controller.catalogAvailability,
        CatalogAvailability.refreshRequired,
      );

      final gate = Completer<Map<String, dynamic>>();
      api.statusGate = gate;
      final late = controller.refreshCatalogAvailability();
      api.rows = [
        {'product': product('fresh-offer')},
      ];
      await controller.refreshFeedFromUser();
      expect(controller.productFeed, isNotEmpty);
      expect(controller.catalogAvailability, CatalogAvailability.ready);
      gate.complete(_status('empty'));
      await late;
      expect(controller.catalogAvailability, CatalogAvailability.ready);
    },
  );

  testWidgets('Unavailable catalogue does not tell the user to reset filters', (
    tester,
  ) async {
    final controller = _controller(_CatalogApi());
    controller.activationStatus = {
      'recommendation_features': [
        'catalog_status_v1',
        'catalogue_discovery_v1',
      ],
    };
    controller.token = 'SYNTHETIC_ACCOUNT';
    await controller.applyFeedFilters(categories: ['Обувь']);
    await _screen(tester, HomeScreen(controller: controller));
    expect(find.text('Подборка временно недоступна'), findsOneWidget);
    expect(find.text('Сбросить фильтры'), findsNothing);
    expect(
      find.byKey(const ValueKey('empty-feed-open-catalogue')),
      findsNothing,
    );
    expect(find.textContaining('Вы просмотрели'), findsNothing);
    expect(find.text('Обновить').hitTestable(), findsOneWidget);
    expect(controller.feedFilterCategories, ['Обувь']);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });

  testWidgets('Network retry retains chosen filters instead of clearing them', (
    tester,
  ) async {
    final api = _CatalogApi()..status = 'ready';
    final controller = _controller(api);
    await controller.applyFeedFilters(categories: ['Обувь']);
    controller.productFeedError = 'Проверьте интернет и повторите загрузку.';
    await _screen(tester, HomeScreen(controller: controller));
    expect(find.text('Повторить'), findsOneWidget);
    expect(find.text('Сбросить фильтры'), findsNothing);
    final before = api.feedCalls;
    await tester.tap(find.text('Повторить'));
    await tester.pumpAndSettle();
    expect(api.feedCalls, before + 1);
    expect(controller.feedFilterCategories, ['Обувь']);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });

  testWidgets('Ready catalogue keeps a neutral personal empty state', (
    tester,
  ) async {
    final controller = _controller(_CatalogApi()..status = 'ready');
    await controller.refreshFeedFromUser();
    await _screen(tester, HomeScreen(controller: controller));
    expect(find.text('Подходящих вещей пока нет'), findsOneWidget);
    expect(find.textContaining('Вы просмотрели'), findsNothing);
    expect(find.text('Подборка временно недоступна'), findsNothing);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });

  testWidgets('Brands distinguish global availability and retry real brands', (
    tester,
  ) async {
    final api = _CatalogApi();
    final controller = _controller(api);
    await _screen(tester, BrandsScreen(controller: controller));
    expect(find.text('Подборка временно недоступна'), findsOneWidget);
    expect(find.textContaining('параметры подборки сохранены'), findsOneWidget);
    expect(api.statusCalls, 1);
    api.brandsHaveItems = true;
    await tester.tap(find.text('Повторить'));
    await tester.pumpAndSettle();
    expect(api.brandsCalls, 2);
    expect(find.text('BAON'), findsOneWidget);
    expect(find.text('3 товаров'), findsOneWidget);
    expect(find.text('Подборка временно недоступна'), findsNothing);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });

  testWidgets(
    'Empty outfits show source availability rather than saved claims',
    (tester) async {
      final controller = _controller(_CatalogApi());
      await controller.refreshCatalogAvailability();
      await _screen(tester, OutfitsScreen(controller: controller));
      expect(find.text('Подборка временно недоступна'), findsOneWidget);
      expect(find.textContaining('Текущие образы сохранены'), findsNothing);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );
}
