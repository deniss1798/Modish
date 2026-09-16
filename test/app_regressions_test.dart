import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/network/api_client.dart';
import 'package:modish/core/widgets/modish_widgets.dart';
import 'package:modish/core/widgets/product_image.dart';
import 'package:modish/features/onboarding/welcome_screen.dart';
import 'package:modish/features/onboarding/onboarding_v2_screen.dart';
import 'package:modish/features/onboarding/wow_outfit.dart';
import 'package:modish/features/outfits/outfits_screen.dart';
import 'package:modish/features/outfits/outfit_detail_screen.dart';
import 'package:modish/features/catalog/filters_screen.dart';
import 'package:modish/features/settings/settings_screen.dart';

Map<String, dynamic> product(String id, {String? image}) => {
  'id': id,
  'title': 'Вещь $id',
  'brand': 'Modish',
  'category': 'рубашки',
  'price': 1000,
  'image_url': 'https://placehold.co/${image ?? id}.png',
};

class FakeApi extends ApiClient {
  List<Map<String, dynamic>> complements = [];
  Completer<List<Map<String, dynamic>>>? relatedGate;
  int relatedCalls = 0;
  @override
  Future<List<Map<String, dynamic>>> productComplements(String id) {
    relatedCalls++;
    return relatedGate?.future ?? Future.value(complements);
  }

  List<Map<String, dynamic>> rows = [];
  Completer<List<Map<String, dynamic>>>? nextFeed;
  Completer<Map<String, dynamic>>? eventGate;
  int feedCalls = 0;
  Map<String, dynamic> lastFilters = {};
  int eventCalls = 0;
  @override
  Future<List<Map<String, dynamic>>> productFeed({
    int limit = 30,
    String? source,
    int? minPrice,
    int? maxPrice,
    List<String> categories = const [],
    List<String> sizes = const [],
    List<String> colors = const [],
    List<String> excludeIds = const [],
  }) {
    feedCalls++;
    lastFilters = {
      'min': minPrice,
      'max': maxPrice,
      'categories': categories,
      'sizes': sizes,
      'colors': colors,
      'excludeIds': excludeIds,
    };
    final pending = nextFeed;
    nextFeed = null;
    return pending?.future ?? Future.value(rows);
  }

  @override
  Future<Map<String, dynamic>> recordRecommendationEvent({
    required String eventType,
    String? productId,
    String? outfitId,
    Map<String, dynamic>? meta,
  }) {
    eventCalls++;
    return eventGate?.future ?? Future.value({});
  }

  @override
  Future<void> metricsEvent(String name, {Map<String, dynamic>? meta}) async {}
  @override
  Future<Map<String, dynamic>> summary() async => {};
  @override
  Future<Map<String, dynamic>> fitProfileMe() async => {};
  @override
  Future<Map<String, dynamic>> tasteProfileMe() async => {};
  @override
  Future<Map<String, dynamic>> billingStatus() async => {};
  @override
  Future<List<Map<String, dynamic>>> savedRecommendations() async => [];
  @override
  Future<List<Map<String, dynamic>>> savedProducts() async => [];
  @override
  Future<List<Map<String, dynamic>>> outfitsList({
    String? scenario,
    bool savedOnly = false,
  }) async => [];
}

void main() {
  testWidgets(
    'Refill preserves the remaining cards while swipes are in flight',
    (tester) async {
      final api = FakeApi()
        ..rows = [
          for (var i = 0; i < 10; i++) {'product': product('p$i')},
        ];
      final c = AppController(apiClient: api);
      late BuildContext context;
      await tester.pumpWidget(
        MaterialApp(
          home: Builder(
            builder: (ctx) {
              context = ctx;
              return const SizedBox();
            },
          ),
        ),
      );
      await c.refreshFeedFromUser();
      final refill = Completer<List<Map<String, dynamic>>>();
      api.nextFeed = refill;
      await c.sendProductEvent(context, 'p0', 'skip');
      await c.sendProductEvent(context, 'p1', 'skip');
      expect(c.feedRefreshing, isTrue);
      expect(c.currentProduct!.product.id, 'p2');
      expect(api.lastFilters['excludeIds'], containsAll(['p0', 'p1', 'p2']));
      await c.sendProductEvent(context, 'p2', 'skip');
      expect(c.currentProduct!.product.id, 'p3');
      refill.complete([
        {'product': product('new')},
        {'product': product('p2')},
      ]);
      await tester.pump();
      expect(c.productFeed.map((c) => c.product.id), [
        'p3',
        'p4',
        'p5',
        'p6',
        'p7',
        'p8',
        'p9',
        'new',
      ]);
      expect(c.feedRefreshing, isFalse);
      expect(api.feedCalls, 2);
    },
  );

  test('Changing filters discards a previous slow feed response', () async {
    final api = FakeApi();
    final c = AppController(apiClient: api);
    final old = Completer<List<Map<String, dynamic>>>();
    api.nextFeed = old;
    final first = c.refreshFeedFromUser();
    api.rows = [
      {'product': product('filtered')},
    ];
    await c.applyFeedFilters(categories: ['bottoms']);
    old.complete([
      {'product': product('old')},
    ]);
    await first;
    expect(c.productFeed.map((c) => c.product.id), ['filtered']);
  });
  test(
    'Complements use catalogue results and never pad from the feed',
    () async {
      final api = FakeApi()
        ..rows = [
          {'product': product('shirt')},
        ];
      final c = AppController(apiClient: api);
      await c.applyFeedFilters();
      expect(c.relatedProducts('shirt'), isEmpty);
      api.complements = [
        {...product('trousers'), 'outfit_slot': 'bottom'},
        {...product('shoe'), 'outfit_slot': 'shoes'},
      ];
      await c.loadRelatedProducts('shirt');
      expect(c.relatedProducts('shirt').map((c) => c.product.id), [
        'trousers',
        'shoe',
      ]);
      await c.loadRelatedProducts('shirt');
      expect(api.relatedCalls, 1);
      api.complements = [];
      await c.loadRelatedProducts('another-shirt');
      expect(c.relatedProducts('another-shirt'), isEmpty);
    },
  );

  test('Concurrent complement requests are coalesced per garment', () async {
    final api = FakeApi()
      ..relatedGate = Completer<List<Map<String, dynamic>>>();
    final c = AppController(apiClient: api);
    final first = c.loadRelatedProducts('anchor');
    await c.loadRelatedProducts('anchor');
    expect(api.relatedCalls, 1);
    api.relatedGate!.complete([product('bottom')]);
    await first;
    expect(c.relatedProducts('anchor').length, 1);
  });
  testWidgets('Filters query the server and keep selections on reopening', (
    tester,
  ) async {
    final api = FakeApi()
      ..rows = [
        {'product': product('fresh')},
      ];
    final c = AppController(apiClient: api);
    await c.applyFeedFilters(
      categories: ['shoes'],
      sizes: ['42'],
      colors: ['Черный'],
    );
    expect(api.lastFilters['categories'], ['shoes']);
    expect(api.lastFilters['colors'], ['Черный']);
    expect(api.lastFilters['min'], isNull);
    expect(c.currentProduct!.product.id, 'fresh');
    await tester.pumpWidget(MaterialApp(home: FiltersScreen(controller: c)));
    await tester.pumpAndSettle();
    expect(
      tester.widget<ChoiceChip>(find.widgetWithText(ChoiceChip, '42')).selected,
      isTrue,
    );
    await tester.tap(find.text('Сбросить'));
    await tester.pump();
    await tester.tap(find.text('Показать товары'));
    await tester.pumpAndSettle();
    expect(api.lastFilters['categories'], isEmpty);
    expect(api.lastFilters['sizes'], isEmpty);
    expect(api.lastFilters['colors'], isEmpty);
    expect(api.lastFilters['max'], isNull);
  });

  testWidgets('Settings has working help and does not offer dead sections', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: SettingsScreen(controller: AppController(apiClient: FakeApi())),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Уведомления'), findsNothing);
    expect(find.text('Безопасность'), findsNothing);
    expect(find.text('Светлая'), findsNothing);
    await tester.tap(find.text('Как пользоваться Modish'));
    await tester.pumpAndSettle();
    expect(find.byType(HelpScreen), findsOneWidget);
    expect(find.text('Как устроена лента'), findsOneWidget);
  });

  testWidgets('Home content stays below the system status bar', (tester) async {
    tester.view.physicalSize = const Size(430, 932);
    tester.view.devicePixelRatio = 1;
    tester.view.padding = const FakeViewPadding(top: 32, bottom: 24);
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.view.resetPadding);
    final c = AppController(apiClient: FakeApi())..tab = 1;
    await tester.pumpWidget(MaterialApp(home: HomeScreen(controller: c)));
    await tester.pumpAndSettle();
    final title = find.text('Образы').first;
    expect(tester.getTopLeft(title).dy, greaterThanOrEqualTo(32));
    expect(tester.takeException(), isNull);
  });
  testWidgets('Welcome background fills the entire phone width', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(430, 932);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        home: WelcomeScreen(controller: AppController(apiClient: FakeApi())),
      ),
    );
    await tester.pumpAndSettle();
    final bounds = tester.getRect(find.byType(HeroFashionBackdrop));
    expect(bounds, const Rect.fromLTWH(0, 0, 430, 932));
    expect(tester.takeException(), isNull);
  });

  testWidgets('Wow shows actual product slots and opens the outfit', (
    tester,
  ) async {
    final outfit = <String, dynamic>{
      'id': 'look1',
      'style_direction': 'daily',
      'total_price': 3000,
      'products': {
        'top': product('shirt'),
        'bottom': product('trousers'),
        'shoes': product('shoes'),
      },
    };
    final c = AppController(apiClient: FakeApi())
      ..onboardingV2SubStep = 5
      ..wowOutfits = [WowOutfit.fromApi(outfit)];
    await tester.pumpWidget(
      MaterialApp(home: OnboardingV2Screen(controller: c)),
    );
    await tester.pumpAndSettle();
    expect(find.byType(ProductFillImage), findsNWidgets(3));
    expect(find.text('Каждый день'), findsOneWidget);
    expect(find.text('Аксессуар'), findsNothing);
    expect(find.text('daily'), findsNothing);
    await tester.tap(find.byType(OutfitCard));
    await tester.pumpAndSettle();
    expect(find.byType(OutfitDetailScreen), findsOneWidget);
    expect(find.text('Вещь shirt'), findsOneWidget);
  });

  testWidgets(
    'Dress outfit displays two slots without empty top/bottom/accessory',
    (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: OutfitCard(
              controller: AppController(apiClient: FakeApi()),
              outfit: {
                'style_direction': 'evening',
                'products': {
                  'one_piece': product('dress'),
                  'shoes': product('shoes'),
                },
              },
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.byType(ProductFillImage), findsNWidgets(2));
      expect(find.text('Платье / комплект'), findsOneWidget);
      expect(find.text('Верх'), findsNothing);
      expect(find.text('Аксессуар'), findsNothing);
    },
  );

  testWidgets(
    'Swipe removes aliases immediately and a late refresh cannot restore them',
    (tester) async {
      final api = FakeApi()
        ..rows = [
          {'product': product('a')},
          {'product': product('a-size-l', image: 'a')},
          for (var i = 1; i <= 8; i++) {'product': product('other$i')},
        ];
      final c = AppController(apiClient: api);
      late BuildContext context;
      await tester.pumpWidget(
        MaterialApp(
          home: Builder(
            builder: (ctx) {
              context = ctx;
              return const SizedBox();
            },
          ),
        ),
      );
      await c.refreshFeedFromUser();
      expect(c.productFeed.length, 9);
      final refresh = Completer<List<Map<String, dynamic>>>();
      api.nextFeed = refresh;
      final updating = c.refreshFeedFromUser();
      api.eventGate = Completer<Map<String, dynamic>>();
      final swiping = c.sendProductEvent(context, 'a', 'skip');
      expect(c.currentProduct!.product.id, 'other1');
      await c.sendProductEvent(context, 'a', 'skip');
      expect(api.eventCalls, 1);
      api.eventGate!.complete({});
      await swiping;
      refresh.complete(api.rows);
      await updating;
      expect(c.currentProduct!.product.id, 'other1');
      expect(c.productFeed.any((c) => c.product.id.startsWith('a')), isFalse);
      expect(api.feedCalls, 2);
      api.eventGate = null;
      await c.sendProductEvent(context, 'other1', 'open_product');
      expect(c.currentProduct!.product.id, 'other1');
      expect(api.feedCalls, 2);
    },
  );
}
