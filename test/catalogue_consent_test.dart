import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/theme/app_theme.dart';
import 'package:modish/features/catalog/discovery_screen.dart';

import 'app_regressions_test.dart' show FakeApi;

const _acceptKey = ValueKey('catalogue-consent-accept');
const _cancelKey = ValueKey('catalogue-consent-cancel');
const _queryKey = ValueKey('catalogue-query');
const _changedMessage = 'Вы сменили аккаунт. Откройте каталог заново.';

Map<String, dynamic> _page([String id = 'shoe']) => {
  'mode': 'catalogue_discovery',
  'personalized': false,
  'size_verification': 'not_verified',
  'products': [
    {
      'id': id,
      'title': 'Обувь $id',
      'price': 3000,
      'image_url': '',
      'catalogue_discovery': true,
      'size_verification': 'not_verified',
      'merchant_sizes': [
        {
          'raw_size': '39',
          'size_system': 'UNKNOWN',
          'availability': 'available',
        },
      ],
    },
  ],
  'has_more': false,
  'next_cursor': null,
};

class _Request {
  _Request(this.category, this.query, this.cursor);
  final String? category;
  final String? query;
  final String? cursor;
  final result = Completer<Map<String, dynamic>>();
}

class _ConsentApi extends FakeApi {
  final requests = <_Request>[];
  bool deferred = false;

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
    final request = _Request(category, query, cursor);
    requests.add(request);
    if (!deferred) request.result.complete(_page());
    return request.result.future;
  }
}

AppController _controller(_ConsentApi api) {
  final controller = AppController(apiClient: api)
    ..activationStatus = {
      'recommendation_features': ['catalogue_discovery_v1'],
    };
  addTearDown(api.dispose);
  addTearDown(controller.dispose);
  return controller;
}

Future<void> _show(
  WidgetTester tester,
  AppController controller, {
  String category = '',
  String query = '',
}) async {
  await tester.pumpWidget(
    MaterialApp(
      theme: AppTheme.dark,
      home: DiscoveryScreen(
        controller: controller,
        initialCategory: category,
        initialQuery: query,
      ),
    ),
  );
  await tester.pump();
}

Future<void> _accept(WidgetTester tester) async {
  await tester.ensureVisible(find.byKey(_acceptKey));
  await tester.tap(find.byKey(_acceptKey));
  await tester.pump();
}

Future<void> _showEntry(WidgetTester tester, AppController controller) async {
  await tester.pumpWidget(
    MaterialApp(
      theme: AppTheme.dark,
      home: Scaffold(
        body: Builder(
          builder: (context) => TextButton(
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute(
                builder: (_) => DiscoveryScreen(controller: controller),
              ),
            ),
            child: const Text('Открыть каталог'),
          ),
        ),
      ),
    ),
  );
  await tester.tap(find.text('Открыть каталог'));
  await tester.pumpAndSettle();
}

void main() {
  setUp(() {
    const storage = MethodChannel(
      'plugins.it_nomads.com/flutter_secure_storage',
    );
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(storage, (_) async => null);
  });

  tearDown(() {
    const storage = MethodChannel(
      'plugins.it_nomads.com/flutter_secure_storage',
    );
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(storage, null);
  });

  testWidgets('No catalogue reads or controls exist before explicit consent', (
    tester,
  ) async {
    final api = _ConsentApi();
    final controller = _controller(api);
    await _show(tester, controller, category: 'shoes', query: 'ботинки');
    controller.setStyleTarget('womenswear');
    await tester.pump(const Duration(seconds: 3));
    expect(find.text('Размер не проверен'), findsOneWidget);
    expect(find.byKey(_acceptKey), findsOneWidget);
    expect(find.byKey(_queryKey), findsNothing);
    expect(find.byType(ChoiceChip), findsNothing);
    expect(find.byType(RefreshIndicator), findsNothing);
    expect(find.byKey(const ValueKey('catalogue-more')), findsNothing);
    expect(find.byKey(const ValueKey('catalogue-product-shoe')), findsNothing);
    expect(api.requests, isEmpty);
    expect(api.feedCalls, 0);
    expect(api.eventCalls, 0);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('Acceptance loads the original query and category exactly once', (
    tester,
  ) async {
    final api = _ConsentApi()..deferred = true;
    final controller = _controller(api);
    await _show(tester, controller, category: 'outerwear', query: '  пальто  ');
    final button = tester.widget<FilledButton>(find.byKey(_acceptKey));
    await _accept(tester);
    // Even a queued second tap on the old button cannot dispatch a second read.
    button.onPressed!();
    await tester.pump();
    expect(api.requests, hasLength(1));
    expect(api.requests.single.category, 'outerwear');
    expect(api.requests.single.query, 'пальто');
    expect(api.requests.single.cursor, isNull);
    expect(find.byKey(_acceptKey), findsNothing);
    api.requests.single.result.complete(_page());
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('catalogue-product-shoe')),
      findsOneWidget,
    );
    expect(find.text('Размер нужно уточнить у магазина'), findsOneWidget);
    expect(api.eventCalls, 0);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('Cancel returns to the entry without loading the catalogue', (
    tester,
  ) async {
    final api = _ConsentApi();
    final controller = _controller(api);
    await _showEntry(tester, controller);
    await tester.ensureVisible(find.byKey(_cancelKey));
    await tester.tap(find.byKey(_cancelKey));
    await tester.pumpAndSettle();
    expect(find.text('Открыть каталог'), findsOneWidget);
    expect(find.byType(DiscoveryScreen), findsNothing);
    expect(api.requests, isEmpty);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('System back returns without loading the catalogue', (
    tester,
  ) async {
    final api = _ConsentApi();
    final controller = _controller(api);
    await _showEntry(tester, controller);
    await tester.binding.handlePopRoute();
    await tester.pumpAndSettle();
    expect(find.byType(DiscoveryScreen), findsNothing);
    expect(api.requests, isEmpty);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('Reopening a route asks again, including for the same account', (
    tester,
  ) async {
    final api = _ConsentApi();
    final controller = _controller(api);
    await _showEntry(tester, controller);
    await _accept(tester);
    await tester.pumpAndSettle();
    expect(api.requests, hasLength(1));
    await tester.pageBack();
    await tester.pumpAndSettle();
    await tester.tap(find.text('Открыть каталог'));
    await tester.pumpAndSettle();
    expect(find.byKey(_acceptKey), findsOneWidget);
    expect(api.requests, hasLength(1));
    await _accept(tester);
    await tester.pumpAndSettle();
    expect(api.requests, hasLength(2));
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('Account change before acceptance disables the old route', (
    tester,
  ) async {
    final api = _ConsentApi();
    final controller = _controller(api);
    await _show(tester, controller);
    final button = tester.widget<FilledButton>(find.byKey(_acceptKey));
    api.onUnauthorized!.call();
    button.onPressed!();
    await tester.pumpAndSettle();
    expect(find.text(_changedMessage), findsOneWidget);
    expect(find.byKey(_acceptKey), findsNothing);
    expect(find.byKey(_queryKey), findsNothing);
    expect(api.requests, isEmpty);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets(
    'Account change removes accepted rows and all fetching controls',
    (tester) async {
      final api = _ConsentApi();
      final controller = _controller(api);
      await _show(tester, controller);
      await _accept(tester);
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('catalogue-product-shoe')),
        findsOneWidget,
      );
      api.onUnauthorized!.call();
      await tester.pumpAndSettle();
      expect(find.text(_changedMessage), findsOneWidget);
      expect(
        find.byKey(const ValueKey('catalogue-product-shoe')),
        findsNothing,
      );
      expect(find.byType(RefreshIndicator), findsNothing);
      expect(find.byKey(_queryKey), findsNothing);
      expect(find.text('Повторить'), findsNothing);
      expect(api.requests, hasLength(1));
      await tester.pumpWidget(const SizedBox());
    },
  );

  testWidgets('An old account response cannot publish after account change', (
    tester,
  ) async {
    final api = _ConsentApi()..deferred = true;
    final controller = _controller(api);
    await _show(tester, controller);
    await _accept(tester);
    api.onUnauthorized!.call();
    api.requests.single.result.complete(_page('old-account'));
    await tester.pumpAndSettle();
    expect(find.text(_changedMessage), findsOneWidget);
    expect(
      find.byKey(const ValueKey('catalogue-product-old-account')),
      findsNothing,
    );
    expect(api.requests, hasLength(1));
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('Account change cancels a scheduled query before dispatch', (
    tester,
  ) async {
    final api = _ConsentApi();
    final controller = _controller(api);
    await _show(tester, controller);
    await _accept(tester);
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(_queryKey), 'куртка');
    api.onUnauthorized!.call();
    await tester.pump(const Duration(seconds: 1));
    expect(api.requests, hasLength(1));
    expect(find.text(_changedMessage), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('Closing a pending route does not publish its eventual result', (
    tester,
  ) async {
    final api = _ConsentApi()..deferred = true;
    final controller = _controller(api);
    await _showEntry(tester, controller);
    await _accept(tester);
    await tester.pageBack();
    // A live spinner belongs only to the outgoing route during its transition.
    await tester.pump(const Duration(seconds: 1));
    api.requests.single.result.complete(_page('closed-route'));
    await tester.pumpAndSettle();
    expect(find.text('Открыть каталог'), findsOneWidget);
    expect(find.byType(DiscoveryScreen), findsNothing);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('A replacement controller cannot inherit route consent', (
    tester,
  ) async {
    final oldApi = _ConsentApi()..deferred = true;
    final oldController = _controller(oldApi);
    await _show(tester, oldController);
    await _accept(tester);
    final newApi = _ConsentApi();
    final newController = _controller(newApi);
    await _show(tester, newController);
    oldApi.requests.single.result.complete(_page('old-controller'));
    await tester.pumpAndSettle();
    expect(find.text(_changedMessage), findsOneWidget);
    expect(newApi.requests, isEmpty);
    expect(
      find.byKey(const ValueKey('catalogue-product-old-controller')),
      findsNothing,
    );
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('Consent remains readable and usable above system navigation', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(360, 640);
    tester.view.devicePixelRatio = 1;
    tester.view.padding = const FakeViewPadding(top: 24, bottom: 48);
    tester.view.viewPadding = const FakeViewPadding(top: 24, bottom: 48);
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.view.resetPadding);
    addTearDown(tester.view.resetViewPadding);
    final api = _ConsentApi();
    final controller = _controller(api);
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.dark,
        builder: (context, child) => MediaQuery(
          data: MediaQuery.of(
            context,
          ).copyWith(textScaler: const TextScaler.linear(1.6)),
          child: child!,
        ),
        home: DiscoveryScreen(controller: controller),
      ),
    );
    await tester.scrollUntilVisible(
      find.byKey(_acceptKey),
      200,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.ensureVisible(find.byKey(_acceptKey));
    await tester.pumpAndSettle();
    final label = tester.widget<Text>(find.text('Понимаю, показать каталог'));
    expect(label.maxLines, isNull);
    expect(label.overflow, isNull);
    final bounds = tester.getRect(find.byKey(_acceptKey));
    expect(bounds.top, greaterThanOrEqualTo(80));
    expect(bounds.bottom, lessThanOrEqualTo(640 - 48));
    expect(api.requests, isEmpty);
    expect(tester.takeException(), isNull);
    await tester.tap(find.byKey(_acceptKey));
    await tester.pumpAndSettle();
    expect(api.requests, hasLength(1));
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });
}
