import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/theme/app_theme.dart';
import 'package:modish/features/catalog/discovery_screen.dart';
import 'package:modish/features/onboarding/onboarding_v2_screen.dart';
import 'package:modish/features/recommendations/feed_screen.dart';

import 'app_regressions_test.dart' show FakeApi;

class _CatalogueEntryApi extends FakeApi {
  final categories = <String?>[];
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
    categories.add(category);
    return {
      'mode': 'catalogue_discovery',
      'personalized': false,
      'size_verification': 'not_verified',
      'products': <Map<String, dynamic>>[],
      'has_more': false,
      'next_cursor': null,
    };
  }

  @override
  Future<Map<String, dynamic>> styleSwipeDeck() async => {
    'products': <Map<String, dynamic>>[],
    'rated': 0,
    'minimum': 15,
    'target': 20,
    'can_continue': false,
  };
}

Future<void> _phone(WidgetTester tester, Widget screen) async {
  tester.view.physicalSize = const Size(360, 640);
  tester.view.devicePixelRatio = 1;
  tester.view.padding = const FakeViewPadding(top: 24, bottom: 48);
  tester.view.viewPadding = const FakeViewPadding(top: 24, bottom: 48);
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPadding);
  addTearDown(tester.view.resetViewPadding);
  await tester.pumpWidget(
    MaterialApp(
      theme: AppTheme.dark,
      builder: (context, child) => MediaQuery(
        data: MediaQuery.of(
          context,
        ).copyWith(textScaler: const TextScaler.linear(1.3)),
        child: child!,
      ),
      home: Scaffold(body: screen),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('Legacy API hides catalogue entry in an empty feed', (
    tester,
  ) async {
    final api = _CatalogueEntryApi();
    final controller = AppController(apiClient: api)..token = 'fixture';
    await _phone(tester, FeedScreen(controller: controller));
    expect(
      find.byKey(const ValueKey('empty-feed-open-catalogue')),
      findsNothing,
    );
    expect(api.categories, isEmpty);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });

  testWidgets('Empty filtered feed opens the same catalogue category', (
    tester,
  ) async {
    final api = _CatalogueEntryApi();
    final controller = AppController(apiClient: api)
      ..token = 'fixture'
      ..stage = AppStage.home
      ..activationStatus = {
        'recommendation_features': ['catalogue_discovery_v1'],
      };
    await controller.applyFeedFilters(categories: ['Верхняя одежда']);
    await _phone(tester, FeedScreen(controller: controller));
    final entry = find.byKey(const ValueKey('empty-feed-open-catalogue'));
    await tester.ensureVisible(entry);
    await tester.tap(entry);
    await tester.pumpAndSettle();
    expect(find.byType(DiscoveryScreen), findsOneWidget);
    expect(api.categories, ['outerwear']);
    expect(controller.feedFilterCategories, ['Верхняя одежда']);
    expect(api.eventCalls, 0);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });

  testWidgets('Cold swipe catalogue browse preserves the unfinished funnel', (
    tester,
  ) async {
    final api = _CatalogueEntryApi();
    final controller = AppController(apiClient: api)
      ..token = 'fixture'
      ..stage = AppStage.onboardingV2
      ..onboardingV2SubStep = 1
      ..activationStatus = {
        'recommendation_features': ['catalogue_discovery_v1'],
      };
    await _phone(tester, OnboardingV2Screen(controller: controller));
    final entry = find.byKey(const ValueKey('cold-swipe-open-catalogue'));
    await tester.ensureVisible(entry);
    await tester.tap(entry);
    await tester.pumpAndSettle();
    expect(find.byType(DiscoveryScreen), findsOneWidget);
    expect(controller.stage, AppStage.onboardingV2);
    expect(controller.onboardingV2SubStep, 1);
    expect(controller.styleSwipeRated, 0);
    expect(controller.styleSwipeCanContinue, false);
    expect(api.feedCalls, 0);
    expect(api.eventCalls, 0);
    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(find.byType(OnboardingV2Screen), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });
}
