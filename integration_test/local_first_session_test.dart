// Explicit local Android/desktop integration run, never a production smoke.
// flutter test integration_test/local_first_session_test.dart -d <device>
//   --dart-define=MODISH_API_BASE_URL=http://10.0.2.2:8773
//   --dart-define=MODISH_IMAGE_PROXY=true
// Use flutter drive --driver=integration_test/local_driver.dart --target=<this>
// with the same flags to retain screenshots on the host after app cleanup.
// The isolated fixture must start with a fresh DB for every run.
import 'dart:convert';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/network/api_client.dart';
import 'package:modish/core/widgets/modish_widgets.dart';
import 'package:modish/core/widgets/product_image.dart';
import 'package:modish/features/catalog/discovery_screen.dart';
import 'package:modish/features/catalog/search_screen.dart';
import 'package:modish/features/onboarding/onboarding_v2_screen.dart';
import 'package:modish/features/products/product_detail_screen.dart';
import 'package:modish/features/profile/edit_profile_screen.dart';
import 'package:modish/features/profile/profile_screen.dart';
import 'package:modish/features/recommendations/saved_screen.dart';

const _email = 'device-alpha@example.com';
const _password = 'SYNTHETIC_DEVICE_PASSWORD_1';
const _unknownShoe = 'catalogue-unknown-shoe';
const _base = String.fromEnvironment('MODISH_API_BASE_URL');

void _requireFixtureRequest(Uri uri, Uri origin) {
  if (uri.scheme != origin.scheme ||
      uri.host != origin.host ||
      uri.port != origin.port ||
      uri.userInfo.isNotEmpty) {
    throw StateError('Only the explicit local fixture origin is allowed.');
  }
}

class _LocalFixtureHttpOverrides extends HttpOverrides {
  _LocalFixtureHttpOverrides(this.origin);

  final Uri origin;

  @override
  HttpClient createHttpClient(SecurityContext? context) {
    // Keep the real dart:io transport. Reject an image CDN fallback before
    // proxy resolution can proceed to DNS or socket creation.
    return super.createHttpClient(context)
      ..findProxy = (uri) {
        _requireFixtureRequest(uri, origin);
        return 'DIRECT';
      };
  }
}

Uri _requireLocalFixture() {
  final uri = Uri.tryParse(_base);
  if (uri == null ||
      uri.scheme != 'http' ||
      !{'127.0.0.1', '10.0.2.2'}.contains(uri.host) ||
      uri.port != 8773 ||
      uri.userInfo.isNotEmpty ||
      !{'', '/'}.contains(uri.path) ||
      uri.hasQuery ||
      uri.hasFragment ||
      const String.fromEnvironment('MODISH_IMAGE_PROXY') != 'true') {
    throw StateError(
      'This test requires an explicit local fixture at port 8773 and local image proxying.',
    );
  }
  return uri;
}

Future<void> _wait(
  WidgetTester tester,
  bool Function() done,
  String phase, {
  Duration timeout = const Duration(seconds: 30),
}) async {
  final deadline = DateTime.now().add(timeout);
  while (!done() && DateTime.now().isBefore(deadline)) {
    await tester.pump(const Duration(milliseconds: 100));
  }
  await tester.pump(const Duration(milliseconds: 50));
  expect(done(), isTrue, reason: 'Timed out waiting for local phase: $phase');
  expect(tester.takeException(), isNull, reason: phase);
}

Future<void> _tap(WidgetTester tester, Finder finder) async {
  expect(finder, findsOneWidget);
  await tester.ensureVisible(finder);
  // A real Android frame must commit the scroll before coordinates are read.
  // A built list child can exist outside the viewport or under system chrome.
  await tester.pump(const Duration(milliseconds: 200));
  expect(
    finder.hitTestable(),
    findsOneWidget,
    reason: 'The target must be physically reachable before tapping.',
  );
  await tester.tap(finder.hitTestable());
  await tester.pump(const Duration(milliseconds: 200));
}

Future<void> _scrollToHitTarget(
  WidgetTester tester,
  Finder target,
  Finder scrollable,
) async {
  // Actual drags exercise scrolling instead of treating an already-built,
  // off-screen child as visible. The bound also catches a broken scroll view.
  for (var attempt = 0; attempt < 12; attempt++) {
    await tester.pump(const Duration(milliseconds: 150));
    if (target.hitTestable().evaluate().isNotEmpty) return;
    await tester.drag(scrollable, const Offset(0, -250));
  }
  await tester.pump(const Duration(milliseconds: 200));
  expect(target.hitTestable(), findsOneWidget);
}

Future<void> _hideKeyboard(WidgetTester tester) async {
  FocusManager.instance.primaryFocus?.unfocus();
  await SystemChannels.textInput.invokeMethod<void>('TextInput.hide');
  await tester.pump(const Duration(milliseconds: 300));
}

Future<void> _dropdown(WidgetTester tester, String label, String value) async {
  final field = find.widgetWithText(DropdownButtonFormField<String>, label);
  await tester.scrollUntilVisible(
    field,
    200,
    scrollable: find.byType(Scrollable).first,
  );
  await _tap(tester, field);
  final choice = find.text(value).last;
  await tester.ensureVisible(choice);
  await tester.pump(const Duration(milliseconds: 200));
  await tester.tap(choice);
  await tester.pump(const Duration(milliseconds: 300));
}

Future<void> _nav(WidgetTester tester, String label) => _tap(
  tester,
  find.descendant(of: find.byType(ModishBottomNav), matching: find.text(label)),
);

Future<void> _back(WidgetTester tester) async {
  await tester.binding.handlePopRoute();
  await tester.pump(const Duration(milliseconds: 350));
}

void main() {
  final binding = IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  WidgetController.hitTestWarningShouldBeFatal = true;

  testWidgets(
    'Real first session against an isolated local API',
    (tester) async {
      // Validate before constructing an API client, clearing device test storage,
      // loading an image or starting any network request.
      final origin = _requireLocalFixture();
      final previousHttpOverrides = HttpOverrides.current;
      final fixtureHttpOverrides = _LocalFixtureHttpOverrides(origin);
      HttpOverrides.global = fixtureHttpOverrides;
      addTearDown(() => HttpOverrides.global = previousHttpOverrides);
      expect(identical(HttpOverrides.current, fixtureHttpOverrides), isTrue);
      _requireFixtureRequest(origin.resolve('/__fixture__/info'), origin);
      for (final denied in [
        Uri.parse('https://baon.ru/images/synthetic-denied.jpg'),
        origin.replace(port: 8774),
        origin.replace(host: 'example.invalid'),
      ]) {
        expect(() => _requireFixtureRequest(denied, origin), throwsStateError);
      }
      final requests = <Map<String, String>>[];
      final searchRows = <Map<String, dynamic>>[];
      final catalogueRows = <Map<String, dynamic>>[];
      final refreshedProducts = <String>{};
      final refreshedCatalogueProducts = <String>{};
      final patchedFitHeights = <Object?>[];
      final readFitHeights = <Object?>[];
      Map<String, dynamic> catalogueEnvelope = {};
      final timings = <String, int>{};
      final screenshots = <String>[];
      var convertedSurface = false;
      binding.reportData = {
        'scope': 'isolated_local_fixture_real_flutter_http',
        'network_policy': 'fixture_origin_only_real_http',
        'timings_ms': timings,
        'screenshots_on_device': screenshots,
      };

      final dio = Dio(
        BaseOptions(
          baseUrl: _base,
          connectTimeout: const Duration(seconds: 10),
          sendTimeout: const Duration(seconds: 20),
          receiveTimeout: const Duration(seconds: 20),
          followRedirects: false,
        ),
      );
      dio.interceptors.add(
        InterceptorsWrapper(
          onRequest: (options, handler) {
            if (options.uri.scheme != origin.scheme ||
                options.uri.host != origin.host ||
                options.uri.port != origin.port) {
              handler.reject(
                DioException(
                  requestOptions: options,
                  type: DioExceptionType.cancel,
                  message: 'Local integration origin fence',
                ),
              );
              return;
            }
            requests.add({'method': options.method, 'path': options.path});
            handler.next(options);
          },
          onResponse: (response, handler) {
            if (response.requestOptions.path == '/fit-profile/me' &&
                response.statusCode == 200 &&
                response.data is Map) {
              final height = (response.data as Map)['height'];
              if (response.requestOptions.method == 'PATCH') {
                patchedFitHeights.add(height);
              } else if (response.requestOptions.method == 'GET') {
                readFitHeights.add(height);
              }
            }
            if (response.requestOptions.path == '/products' &&
                response.data is List) {
              searchRows
                ..clear()
                ..addAll(
                  (response.data as List).whereType<Map>().map(
                    (row) => Map<String, dynamic>.from(row),
                  ),
                );
            }
            if (response.requestOptions.path == '/catalog/discovery' &&
                response.data is Map) {
              final body = response.data as Map;
              catalogueEnvelope = Map<String, dynamic>.from(body);
              catalogueRows
                ..clear()
                ..addAll(
                  (body['products'] is List
                          ? body['products'] as List
                          : const [])
                      .whereType<Map>()
                      .map((row) => Map<String, dynamic>.from(row)),
                );
            }
            final path = response.requestOptions.path.split('/');
            if (path.length == 3 && path[1] == 'products') {
              refreshedProducts.add(path[2]);
            }
            if (path.length == 4 &&
                path[1] == 'catalog' &&
                path[2] == 'discovery') {
              refreshedCatalogueProducts.add(path[3]);
            }
            handler.next(response);
          },
        ),
      );
      final api = ApiClient(dio: dio);
      final controller = AppController(apiClient: api);
      addTearDown(() {
        controller.dispose();
        api.dispose();
      });
      final fixtureResponse = await dio.get('/__fixture__/info');
      expect(fixtureResponse.statusCode, 200);
      final fixture = fixtureResponse.data;
      expect(fixture, isA<Map>());
      expect(fixture['fixture'], 'modish-device-local');
      expect(fixture['run_id'], matches(RegExp(r'^[0-9a-f]{32}$')));
      expect(fixture['known_size_products'], 64);
      expect(fixture['unknown_size_products'], 1);
      expect(fixture['host'], '127.0.0.1');
      expect(fixture['port'], 8773);
      binding.reportData!['fixture_run_id'] = fixture['run_id'];
      await const FlutterSecureStorage(
        aOptions: AndroidOptions(encryptedSharedPreferences: true),
      ).deleteAll();
      controller.goToAuth(registerMode: true);
      await tester.pumpWidget(
        AppScope(notifier: controller, child: const ModishApp()),
      );
      await tester.pump(const Duration(milliseconds: 300));

      Future<void> capture(String name) async {
        try {
          // Let the route start and then finish its transition on real frames.
          // A mounted page or completed API call alone is not visual readiness.
          await tester.pump(const Duration(milliseconds: 350));
          await tester.pump(const Duration(milliseconds: 700));
          if (name != 'catalogue-consent' && name != 'profile-parameters') {
            await _wait(
              tester,
              () {
                final visibleImages = find
                    .byType(ProductFillImage)
                    .hitTestable()
                    .evaluate()
                    .toList();
                return visibleImages.isNotEmpty &&
                    visibleImages.every((element) {
                      final image = find.byElementPredicate(
                        (candidate) => identical(candidate, element),
                      );
                      return find
                          .descendant(
                            of: image,
                            matching: find.byType(RawImage),
                          )
                          .evaluate()
                          .any(
                            (child) => (child.widget as RawImage).image != null,
                          );
                    });
              },
              'decoded product photos for $name screenshot',
              timeout: const Duration(seconds: 10),
            );
          }
          await _wait(
            tester,
            () => binding.transientCallbackCount == 0,
            'finished animations for $name screenshot',
            timeout: const Duration(seconds: 5),
          );
          await tester.pump(const Duration(milliseconds: 100));
          if (Platform.isAndroid && !convertedSurface) {
            await binding.convertFlutterSurfaceToImage();
            convertedSurface = true;
            await tester.pump();
          }
          final bytes = await binding.takeScreenshot(name);
          expect(bytes, isNotEmpty);
          final file = File(
            '${Directory.systemTemp.path}/modish-local-$name.png',
          );
          await file.writeAsBytes(bytes, flush: true);
          screenshots.add(file.path);
          // The SDK driver transfers reportData after the test. Keep the PNG
          // bytes there because app cleanup can remove this device cache file.
        } on MissingPluginException {
          binding.reportData!['screenshot_support'] = 'plugin_unavailable';
        } on PlatformException {
          binding.reportData!['screenshot_support'] = 'platform_unavailable';
        }
      }

      final watch = Stopwatch()..start();
      await tester.enterText(find.byType(TextField).first, _email);
      await tester.enterText(find.byType(TextField).last, _password);
      await _hideKeyboard(tester);
      await _tap(tester, find.text('Создать аккаунт'));
      await _wait(
        tester,
        () =>
            controller.stage == AppStage.onboardingV2 && !controller.isLoading,
        'registration and fresh onboarding',
      );
      timings['registration_to_preferences'] = watch.elapsedMilliseconds;
      expect(find.byType(OnboardingV2Screen), findsOneWidget);
      expect(controller.token, isNotEmpty);
      expect(controller.supportsCatalogueDiscovery, isTrue);
      expect(controller.supportsShoppingIntent, isTrue);
      expect(controller.styleSwipeRated, 0);
      expect(requests.where((row) => row['path'] == '/feed'), isEmpty);

      await _tap(
        tester,
        find.byKey(const ValueKey('onboarding-gender-womenswear')),
      );
      await tester.enterText(
        find.byKey(const ValueKey('onboarding-height')),
        '170',
      );
      await _hideKeyboard(tester);
      await _dropdown(tester, 'Возраст', '25-34');
      await _dropdown(tester, 'Одежда (буквы / RU)', 'M');
      await _dropdown(tester, 'Обувь (EU)', '38');
      await tester.scrollUntilVisible(
        find.text('Повседневный'),
        250,
        scrollable: find.byType(Scrollable).first,
      );
      await _tap(tester, find.text('Повседневный'));
      await _tap(tester, find.text('Минимализм'));
      await tester.scrollUntilVisible(
        find.text('Настроить мой вкус'),
        200,
        scrollable: find.byType(Scrollable).first,
      );
      watch.reset();
      await _tap(tester, find.text('Настроить мой вкус'));
      await _wait(
        tester,
        () =>
            controller.onboardingV2SubStep == 1 &&
            !controller.isLoading &&
            controller.styleSwipeCards.isNotEmpty,
        'preferences to real swipe deck',
      );
      timings['preferences_to_swipe_deck'] = watch.elapsedMilliseconds;
      expect(controller.supportsCatalogueDiscovery, isTrue);
      expect(controller.supportsShoppingIntent, isTrue);
      expect(controller.styleSwipeCards.length, greaterThanOrEqualTo(15));
      expect(
        controller.styleSwipeCards.map((product) => product.id),
        isNot(contains(_unknownShoe)),
      );

      watch.reset();
      for (var count = 1; count <= 15; count++) {
        await _wait(
          tester,
          () =>
              !controller.isLoading &&
              find.text('Нравится').evaluate().isNotEmpty,
          'ready to rate style card $count',
        );
        await _tap(tester, find.text('Нравится'));
        await _wait(
          tester,
          () => controller.styleSwipeRated == count && !controller.isLoading,
          'durable style reaction $count',
        );
        expect(controller.error, isNull);
      }
      timings['fifteen_style_reactions'] = watch.elapsedMilliseconds;
      expect(controller.styleSwipeCanContinue, isTrue);
      expect(requests.where((row) => row['path'] == '/feed'), isEmpty);
      watch.reset();
      await _tap(tester, find.text('Показать мои образы'));
      await _wait(
        tester,
        () => controller.onboardingV2SubStep == 5 && !controller.isLoading,
        'real WOW outfits',
      );
      timings['wow_generation'] = watch.elapsedMilliseconds;
      expect(controller.wowOutfits.length, inInclusiveRange(1, 3));
      expect(controller.supportsCatalogueDiscovery, isTrue);
      expect(controller.supportsShoppingIntent, isTrue);
      expect(controller.error, isNull);
      await capture('wow');
      await tester.scrollUntilVisible(
        find.text('Открыть мою ленту'),
        300,
        scrollable: find.byType(Scrollable).first,
      );
      watch.reset();
      await _tap(tester, find.text('Открыть мою ленту'));
      await _wait(
        tester,
        () =>
            controller.stage == AppStage.home &&
            !controller.feedRefreshing &&
            controller.currentProduct != null,
        'first personal feed',
      );
      timings['first_personal_feed'] = watch.elapsedMilliseconds;
      expect(controller.productFeedError, isNull);
      expect(
        controller.productFeed.map((card) => card.product.id),
        isNot(contains(_unknownShoe)),
      );
      await capture('feed');

      final savedId = controller.currentProduct!.product.id;
      final savedTitle = controller.currentProduct!.product.title;
      watch.reset();
      await _tap(tester, find.byTooltip('Сохранить'));
      await _wait(
        tester,
        () => controller.savedProductRows.any(
          (row) => row['product']['id'] == savedId,
        ),
        'durable save and refreshed saved collection',
      );
      timings['save_to_collection'] = watch.elapsedMilliseconds;
      await _nav(tester, 'Сохранено');
      expect(find.byType(SavedScreen), findsOneWidget);
      expect(find.text(savedTitle), findsOneWidget);
      await _nav(tester, 'Лента');
      await _tap(tester, find.byTooltip('Поиск'));
      expect(find.byType(SearchScreen), findsOneWidget);
      watch.reset();
      await tester.enterText(find.byType(TextField), 'Рубашка');
      await _hideKeyboard(tester);
      await _wait(
        tester,
        () =>
            searchRows.isNotEmpty &&
            find
                .text(searchRows.first['title'] as String)
                .evaluate()
                .isNotEmpty,
        'live catalogue search',
      );
      timings['search_response'] = watch.elapsedMilliseconds;
      expect(searchRows.map((row) => row['id']), isNot(contains(_unknownShoe)));
      final searchTitle = searchRows.first['title'] as String;
      await _tap(tester, find.text(searchTitle));
      await _wait(
        tester,
        () =>
            find.byType(ProductDetailScreen).evaluate().isNotEmpty &&
            refreshedProducts.contains(searchRows.first['id']),
        'searched product detail',
      );
      await _back(tester);
      await _back(tester);
      await _nav(tester, 'Профиль');
      await _scrollToHitTarget(
        tester,
        find.text('Каталог магазинов'),
        find
            .descendant(
              of: find.byType(ProfileScreen),
              matching: find.byType(Scrollable),
            )
            .first,
      );
      expect(
        requests.where((row) => row['path'] == '/catalog/discovery'),
        isEmpty,
      );
      await _tap(tester, find.text('Каталог магазинов'));
      expect(find.byType(DiscoveryScreen), findsOneWidget);
      await tester.pump(const Duration(milliseconds: 600));
      expect(find.text('Размер не проверен'), findsOneWidget);
      expect(find.byKey(const ValueKey('catalogue-query')), findsNothing);
      expect(
        requests.where((row) => row['path'] == '/catalog/discovery'),
        isEmpty,
      );
      await capture('catalogue-consent');
      await tester.scrollUntilVisible(
        find.byKey(const ValueKey('catalogue-consent-accept')),
        250,
        scrollable: find.byType(Scrollable).first,
      );
      watch.reset();
      await _tap(
        tester,
        find.byKey(const ValueKey('catalogue-consent-accept')),
      );
      await _wait(
        tester,
        () =>
            catalogueRows.isNotEmpty &&
            find.byKey(const ValueKey('catalogue-query')).evaluate().isNotEmpty,
        'consented catalogue',
      );
      timings['consent_to_catalogue'] = watch.elapsedMilliseconds;
      expect(catalogueEnvelope['mode'], 'catalogue_discovery');
      expect(catalogueEnvelope['personalized'], isFalse);
      expect(catalogueEnvelope['size_verification'], 'not_verified');
      await _tap(
        tester,
        find.byKey(const ValueKey('catalogue-category-shoes')),
      );
      await _wait(
        tester,
        () => find
            .byKey(const ValueKey('catalogue-product-$_unknownShoe'))
            .evaluate()
            .isNotEmpty,
        'unknown-size shoe in the explicitly accepted catalogue',
      );
      final unknown = catalogueRows.singleWhere(
        (row) => row['id'] == _unknownShoe,
      );
      expect(unknown['size_verification'], 'not_verified');
      expect(
        (unknown['merchant_sizes'] as List).any(
          (size) => size['size_system'] == 'UNKNOWN',
        ),
        isTrue,
      );
      await _tap(
        tester,
        find.byKey(const ValueKey('catalogue-product-$_unknownShoe')),
      );
      await _wait(
        tester,
        () =>
            find.byType(ProductDetailScreen).evaluate().isNotEmpty &&
            refreshedCatalogueProducts.contains(_unknownShoe),
        'unknown-size catalogue detail',
      );
      expect(find.text('Размер нужно уточнить у магазина'), findsOneWidget);
      expect(find.byIcon(Icons.favorite_border), findsNothing);
      expect(controller.styleSwipeRated, 15);
      expect(controller.error, isNull);
      expect(tester.takeException(), isNull);
      await capture('catalogue-unknown-size');

      await _back(tester);
      await _back(tester);
      expect(find.byType(ProfileScreen), findsOneWidget);
      await _tap(tester, find.text('Параметры подборки'));
      await _wait(
        tester,
        () => find.byType(EditProfileScreen).evaluate().isNotEmpty,
        'profile parameters opened through the menu',
      );
      final heightField = find.byWidgetPredicate(
        (widget) =>
            widget is TextField && widget.decoration?.labelText == 'Рост (см)',
      );
      expect(heightField, findsOneWidget);
      expect(tester.widget<TextField>(heightField).controller!.text, '170');
      await _tap(tester, heightField);
      await tester.enterText(heightField, '171');
      await _hideKeyboard(tester);
      await _wait(
        tester,
        () => tester.view.viewInsets.bottom == 0,
        'keyboard hidden before saving profile parameters',
      );
      final saveProfile = find.byKey(const ValueKey('save-profile'));
      expect(saveProfile.hitTestable(), findsOneWidget);
      final logicalHeight =
          tester.view.physicalSize.height / tester.view.devicePixelRatio;
      final navigationInset =
          tester.view.viewPadding.bottom / tester.view.devicePixelRatio;
      expect(navigationInset, greaterThan(0));
      expect(
        tester.getRect(saveProfile).bottom,
        lessThanOrEqualTo(logicalHeight - navigationInset),
        reason: 'Save must stay above the actual Android navigation inset.',
      );
      await capture('profile-parameters');
      final patchCountBeforeSave = patchedFitHeights.length;
      final readCountBeforeSave = readFitHeights.length;
      watch.reset();
      await _tap(tester, saveProfile);
      await _wait(
        tester,
        () =>
            patchedFitHeights.length == patchCountBeforeSave + 1 &&
            patchedFitHeights.last == 171 &&
            readFitHeights.length > readCountBeforeSave &&
            readFitHeights.last == 171 &&
            controller.fitProfile['height'] == 171 &&
            !controller.isLoading &&
            find.byType(EditProfileScreen).evaluate().isEmpty,
        'profile PATCH and subsequent authoritative GET confirm the saved height',
      );
      timings['profile_parameters_save'] = watch.elapsedMilliseconds;
      expect(controller.error, isNull);
      expect(find.byType(ProfileScreen), findsOneWidget);
      await _tap(tester, find.text('Параметры подборки'));
      await _wait(
        tester,
        () => find.byType(EditProfileScreen).evaluate().isNotEmpty,
        'saved profile parameters reopened through the menu',
      );
      expect(tester.widget<TextField>(heightField).controller!.text, '171');
      expect(find.text('171'), findsOneWidget);
      expect(readFitHeights.last, 171);
      expect(controller.fitProfile['height'], 171);
      expect(tester.takeException(), isNull);
      binding.reportData!['profile_parameters_saved_and_reopened'] = true;
      binding.reportData!['profile_save_navigation_inset'] = navigationInset;
      binding.reportData!['request_counts'] = {
        for (final path in requests.map((row) => row['path']!).toSet())
          path: requests.where((row) => row['path'] == path).length,
      };
      binding.reportData!['result'] = 'passed';
      // Timings are one controlled emulator journey, not production p95 or a
      // real merchant/network performance measurement.
      final summary = Map<String, dynamic>.from(binding.reportData!)
        ..remove('screenshots');
      debugPrint('MODISH_LOCAL_JOURNEY ${jsonEncode(summary)}');
      await tester.pumpWidget(const SizedBox());
    },
    timeout: const Timeout(Duration(minutes: 5)),
  );
}
