import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/theme/app_theme.dart';
import 'package:modish/features/catalog/discovery_screen.dart';
import 'package:modish/features/catalog/search_screen.dart';
import 'package:modish/features/profile/edit_profile_screen.dart';
import 'package:modish/features/profile/profile_preferences_screen.dart';
import 'package:modish/features/profile/profile_screen.dart';
import 'package:modish/features/settings/settings_screen.dart';

import 'app_regressions_test.dart' show FakeApi;
import 'support/load_app_fonts.dart';

class _ProfileApi extends FakeApi {
  int catalogueCalls = 0;
  final queries = <String?>[];

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
    return [];
  }

  @override
  Future<Map<String, dynamic>> catalogueDiscovery({
    int limit = 20,
    String? category,
    String? query,
    String? source,
    int? minPrice,
    int? maxPrice,
    String? cursor,
  }) async {
    catalogueCalls++;
    return {
      'mode': 'catalogue_discovery',
      'personalized': false,
      'size_verification': 'not_verified',
      'products': [],
      'has_more': false,
      'next_cursor': null,
    };
  }
}

AppController _controller(_ProfileApi api) {
  final controller = AppController(apiClient: api)
    ..tab = 3
    ..email = 'anna@example.com'
    ..activationStatus = {
      'recommendation_features': ['catalogue_discovery_v1'],
      'style_preferences': ['minimalism'],
    }
    ..fitProfile = {
      'gender_target': 'womenswear',
      'height': 170,
      'budget_max': 10000,
      'style_scenarios': ['daily'],
      'size_profile': {
        'height_confirmed': true,
        'sizes': {
          'top': {'state': 'confirmed', 'system': 'LETTER', 'value': 'M'},
          'bottom': {'state': 'confirmed', 'system': 'LETTER', 'value': 'L'},
          'one_piece': {'state': 'confirmed', 'system': 'LETTER', 'value': 'M'},
          'shoes': {'state': 'confirmed', 'system': 'EU', 'value': '39'},
        },
      },
    }
    ..tasteProfile = {
      'weights': {
        'styles': {'casual': 2, 'romantic': 1},
        'colors': {'blue': 3, 'white': 2},
      },
    };
  addTearDown(controller.dispose);
  addTearDown(api.dispose);
  return controller;
}

Future<GlobalKey> _show(
  WidgetTester tester,
  AppController controller, {
  Size size = const Size(360, 640),
  double scale = 1.3,
  bool fullHome = false,
}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  tester.view.padding = const FakeViewPadding(top: 24, bottom: 48);
  tester.view.viewPadding = const FakeViewPadding(top: 24, bottom: 48);
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPadding);
  addTearDown(tester.view.resetViewPadding);
  final canvas = GlobalKey();
  await tester.pumpWidget(
    RepaintBoundary(
      key: canvas,
      child: MaterialApp(
        theme: AppTheme.dark,
        debugShowCheckedModeBanner: false,
        builder: (context, child) => MediaQuery(
          data: MediaQuery.of(
            context,
          ).copyWith(textScaler: TextScaler.linear(scale)),
          child: child!,
        ),
        home: fullHome
            ? HomeScreen(controller: controller)
            : Scaffold(
                body: SafeArea(child: ProfileScreen(controller: controller)),
              ),
      ),
    ),
  );
  await tester.pumpAndSettle();
  return canvas;
}

Future<void> _reveal(
  WidgetTester tester,
  Finder target, {
  bool backwards = false,
}) async {
  if (target.evaluate().isEmpty) {
    await tester.scrollUntilVisible(
      target,
      backwards ? -160 : 160,
      scrollable: find.byType(Scrollable).first,
      maxScrolls: 20,
    );
  }
  await tester.ensureVisible(target);
  await tester.pumpAndSettle();
  expect(target.hitTestable(), findsOneWidget);
}

Future<void> _tap(
  WidgetTester tester,
  Finder target, {
  bool backwards = false,
}) async {
  await _reveal(tester, target, backwards: backwards);
  await tester.tap(target);
  await tester.pumpAndSettle();
  expect(tester.takeException(), isNull);
}

Future<void> _back(WidgetTester tester) async {
  final standard = find.byType(BackButton);
  await tester.tap(
    standard.evaluate().isNotEmpty ? standard : find.byIcon(Icons.arrow_back),
  );
  await tester.pumpAndSettle();
  expect(tester.takeException(), isNull);
}

Future<void> _capture(
  WidgetTester tester,
  GlobalKey canvas,
  String name,
) async {
  if (!const bool.fromEnvironment('PROFILE_NAV_CAPTURE')) return;
  await tester.pump();
  await tester.runAsync(() async {
    final image =
        await (canvas.currentContext!.findRenderObject()
                as RenderRepaintBoundary)
            .toImage();
    final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
    final file = File('.tmp/profile-navigation-20261020/$name.png');
    await file.parent.create(recursive: true);
    await file.writeAsBytes(bytes!.buffer.asUint8List());
    image.dispose();
  });
}

void main() {
  const storage = MethodChannel('plugins.it_nomads.com/flutter_secure_storage');
  setUp(
    () => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(storage, (_) async => null),
  );
  tearDown(
    () => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(storage, null),
  );

  for (final viewport in [const Size(360, 640), const Size(844, 390)]) {
    testWidgets('Profile navigation stays reachable at $viewport and text 130%', (
      tester,
    ) async {
      await tester.runAsync(loadAppFonts);
      final api = _ProfileApi();
      final controller = _controller(api);
      final canvas = await _show(
        tester,
        controller,
        size: viewport,
        fullHome: true,
      );
      final tag = viewport.width == 360 ? 'portrait' : 'landscape';
      await _capture(tester, canvas, '$tag-top');
      expect(find.text('В ленте'), findsNothing);
      expect(find.text('Мои образы'), findsNothing);
      expect(
        find.descendant(
          of: find.byType(ProfileScreen),
          matching: find.text('Сохранено'),
        ),
        findsNothing,
      );
      expect(find.byTooltip('Настройки'), findsOneWidget);
      await _tap(tester, find.byKey(const ValueKey('profile-edit-parameters')));
      expect(find.byType(EditProfileScreen), findsOneWidget);
      expect(find.text('170'), findsOneWidget);
      await _back(tester);
      await _tap(tester, find.byKey(const ValueKey('profile-preferences')));
      expect(find.byType(ProfilePreferencesScreen), findsOneWidget);
      await _reveal(tester, find.text('Синий'));
      expect(find.text('Синий'), findsOneWidget);
      expect(find.byType(Chip), findsNothing);
      await _capture(tester, canvas, '$tag-preferences');
      await _back(tester);
      // Settings moved to one accessible header action, without a duplicate row.
      await _tap(tester, find.byTooltip('Настройки'), backwards: true);
      expect(find.byType(SettingsScreen), findsOneWidget);
      await _back(tester);
      await _tap(tester, find.text('Каталог магазинов'));
      expect(find.byType(DiscoveryScreen), findsOneWidget);
      expect(find.text('Размер не проверен'), findsOneWidget);
      expect(api.catalogueCalls, 0);
      await _tap(
        tester,
        find.byKey(const ValueKey('catalogue-consent-accept')),
      );
      expect(api.catalogueCalls, 1);
      await _back(tester);
      await _tap(tester, find.text('Бренды'));
      await _tap(tester, find.text('BAON'));
      expect(find.byType(SearchScreen), findsOneWidget);
      expect(api.queries, ['BAON']);
      await _back(tester);
      final photo = find.byKey(const ValueKey('profile-photo-analysis'));
      await tester.ensureVisible(photo);
      await tester.pumpAndSettle();
      expect(photo.hitTestable(), findsOneWidget);
      await _capture(tester, canvas, '$tag-bottom');
      await tester.tap(photo);
      await tester.pumpAndSettle();
      expect(controller.stage, AppStage.upload);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
    });
  }

  testWidgets(
    'Profile summary keeps sizes separate and has no invented defaults',
    (tester) async {
      final api = _ProfileApi();
      final controller = _controller(api);
      await _show(tester, controller, scale: 1);
      expect(find.text('M'), findsNWidgets(2));
      expect(find.text('L'), findsOneWidget);
      expect(find.text('EU 39'), findsOneWidget);
      expect(find.text('до 10000 ₽'), findsOneWidget);
      controller.fitProfile = {
        'size_profile': {'sizes': {}},
        'budget_max': double.nan,
        'clothing_size': 'XL',
        'shoe_size': '42',
      };
      controller.tasteProfile = {};
      controller.activationStatus = {};
      controller.summary = {
        'suitable_colors': ['navy'],
        'style_direction_human': ['Минимализм'],
      };
      controller.setTab(3);
      await tester.pumpAndSettle();
      expect(find.text('Размеры не указаны'), findsOneWidget);
      expect(find.text('Не указан'), findsOneWidget);
      expect(find.text('XL'), findsNothing);
      expect(find.text('42'), findsNothing);
      expect(find.text('Пока мало данных'), findsOneWidget);
      expect(find.text('Каталог магазинов'), findsNothing);
      await _tap(tester, find.byKey(const ValueKey('profile-preferences')));
      expect(find.text('Пока мало данных'), findsOneWidget);
      expect(find.text('Тёмно-синий'), findsNothing);
      expect(find.text('Минимализм'), findsNothing);
      await tester.pumpWidget(const SizedBox());
    },
  );

  testWidgets(
    'Preferences show sourced labels and clear on actual account invalidation',
    (tester) async {
      final api = _ProfileApi();
      final controller = _controller(api);
      controller.tasteProfile = {
        'weights': {
          'styles': {
            'romantic': 1,
            'internal_new_code': 20,
            'casual': double.infinity,
          },
          'colors': {'blue': 3, 'white': double.nan, 'navy': -1},
        },
      };
      await _show(tester, controller, scale: 1);
      await _tap(tester, find.byKey(const ValueKey('profile-preferences')));
      expect(find.text('Вы выбрали при знакомстве'), findsOneWidget);
      expect(find.text('Минимализм'), findsOneWidget);
      expect(find.text('Романтичный'), findsOneWidget);
      expect(find.text('Синий'), findsOneWidget);
      expect(find.text('Повседневный'), findsNothing);
      expect(find.text('internal_new_code'), findsNothing);
      expect(find.text('Белый'), findsNothing);
      api.onUnauthorized!.call();
      await tester.pumpAndSettle();
      expect(find.textContaining('Аккаунт изменился'), findsOneWidget);
      expect(find.text('Романтичный'), findsNothing);
      expect(find.text('Синий'), findsNothing);
      await tester.pumpWidget(const SizedBox());
    },
  );

  testWidgets('Returning a brand from an old account cannot open search', (
    tester,
  ) async {
    final api = _ProfileApi();
    final controller = _controller(api);
    await _show(tester, controller, scale: 1);
    await _tap(tester, find.text('Бренды'));
    final brandContext = tester.element(find.text('BAON'));
    api.onUnauthorized!.call();
    Navigator.of(brandContext).pop('BAON');
    await tester.pumpAndSettle();
    expect(find.byType(SearchScreen), findsNothing);
    expect(api.queries, isEmpty);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets(
    'Optional photo action stays disabled while a request is running',
    (tester) async {
      final api = _ProfileApi();
      final controller = _controller(api)..isLoading = true;
      await _show(tester, controller);
      final photo = find.byKey(const ValueKey('profile-photo-analysis'));
      if (photo.evaluate().isEmpty) {
        await tester.scrollUntilVisible(
          photo,
          180,
          scrollable: find.byType(Scrollable).first,
        );
      }
      await tester.ensureVisible(photo);
      await tester.pumpAndSettle();
      await tester.tap(photo);
      await tester.pumpAndSettle();
      expect(controller.stage, AppStage.splash);
      expect(find.text('По желанию'), findsOneWidget);
      expect(find.textContaining('Улучшит'), findsNothing);
      await tester.pumpWidget(const SizedBox());
    },
  );
}
