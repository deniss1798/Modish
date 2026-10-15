import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/features/onboarding/onboarding_v2_screen.dart';
import 'package:modish/features/recommendations/feed_screen.dart';
import 'app_regressions_test.dart' show FakeApi, product;

class ActivationApi extends FakeApi {
  int rated = 0;
  int deckCalls = 0;
  int completeCalls = 0;
  int swipeCalls = 0;
  bool feedbackFails = false;
  bool hasLikes = true;
  Completer<void>? swipeGate;
  final views = <String>[];
  @override
  Future<void> activationPreferences({
    required String genderTarget,
    required String ageGroup,
    required String clothingSize,
    required String shoeSize,
    required List<String> styles,
    required String priceSegment,
    int? height,
  }) async {}
  @override
  Future<Map<String, dynamic>> styleSwipeDeck() async {
    deckCalls++;
    return {
      'products': [for (var i = rated; i < 20; i++) product('swipe-$i')],
      'rated': rated,
      'minimum': 15,
      'target': 20,
      'can_continue': rated >= 15 && hasLikes,
    };
  }

  @override
  Future<Map<String, dynamic>> styleSwipeFeedback(
    String id,
    String reaction,
  ) async {
    swipeCalls++;
    await swipeGate?.future;
    if (feedbackFails) throw Exception('network unavailable');
    if (reaction != 'unavailable') rated++;
    if (reaction == 'like') hasLikes = true;
    return {'rated': rated, 'can_continue': rated >= 15 && hasLikes};
  }

  @override
  Future<Map<String, dynamic>> onboardingComplete({int count = 4}) async {
    completeCalls++;
    return {'completed': true, 'outfits': []};
  }

  @override
  Future<Map<String, dynamic>> recordRecommendationEvent({
    required String eventType,
    String? productId,
    String? outfitId,
    Map<String, dynamic>? meta,
  }) async {
    if (eventType == 'view') {
      views.add(productId!);
      expect(meta?['surface'], 'feed');
    }
    return {};
  }
}

void main() {
  test(
    'Only dislikes keep the user in Style Swipe until a positive signal',
    () async {
      final api = ActivationApi()
        ..rated = 19
        ..hasLikes = false;
      final c = AppController(apiClient: api);
      await c.loadStyleSwipe();
      await c.onboardingV2CompleteWow();
      expect(api.completeCalls, 0);
      await c.rateStyleSwipe('dislike');
      expect(api.deckCalls, 2);
      expect(c.styleSwipeCanContinue, isFalse);
      // A refreshed catalogue card can supply the missing positive signal.
      api.rated = 19;
      await c.loadStyleSwipe();
      await c.rateStyleSwipe('like');
      expect(c.styleSwipeCanContinue, isTrue);
      c.dispose();
    },
  );

  test('Cold user cannot skip straight into the feed or Wow', () async {
    final api = ActivationApi();
    final c = AppController(apiClient: api)..stage = AppStage.onboardingV2;
    await c.finishOnboardingV2ToHome();
    await c.onboardingV2CompleteWow();
    expect(c.stage, AppStage.onboardingV2);
    expect(api.feedCalls, 0);
    expect(api.completeCalls, 0);
    c.dispose();
  });

  test(
    'Style Swipe waits for durable response, ignores double tap and keeps failed card',
    () async {
      final api = ActivationApi()..swipeGate = Completer<void>();
      final c = AppController(apiClient: api);
      await c.startStyleSwipe(
        genderTarget: 'womenswear',
        height: 170,
        ageGroup: '25-34',
        clothingSize: 'M',
        shoeSize: '38',
        styles: ['casual', 'minimalism'],
        priceSegment: 'mass',
      );
      final first = c.styleSwipeCards.first.id;
      final pending = c.rateStyleSwipe('like');
      await c.rateStyleSwipe('dislike');
      expect(c.styleSwipeCards.first.id, first);
      expect(api.swipeCalls, 1);
      api.swipeGate!.complete();
      await pending;
      expect(c.styleSwipeRated, 1);
      expect(c.styleSwipeCards.first.id, isNot(first));
      final second = c.styleSwipeCards.first.id;
      api.feedbackFails = true;
      await c.rateStyleSwipe('like');
      expect(c.styleSwipeCards.first.id, second);
      expect(c.styleSwipeRated, 1);
      expect(c.error, isNotNull);
      expect(api.feedCalls, 0);
      c.dispose();
    },
  );

  test(
    'Restart resumes server progress; Wow comes before any feed prefetch',
    () async {
      final api = ActivationApi()..rated = 14;
      final c = AppController(apiClient: api)..stage = AppStage.onboardingV2;
      await c.loadStyleSwipe();
      expect(c.styleSwipeRated, 14);
      await c.onboardingV2CompleteWow();
      expect(api.completeCalls, 0);
      await c.rateStyleSwipe('like');
      await c.onboardingV2CompleteWow();
      expect(api.completeCalls, 1);
      expect(c.onboardingV2SubStep, 5);
      expect(api.feedCalls, 0);
      await c.finishOnboardingV2ToHome();
      expect(c.stage, AppStage.home);
      expect(api.feedCalls, 1);
      await Future<void>.delayed(Duration.zero); // let secondary refresh settle
      c.dispose();
    },
  );

  testWidgets('Hidden feed does not record first-session views', (
    tester,
  ) async {
    final api = ActivationApi()
      ..rows = [
        {'product': product('visible')},
      ];
    final c = AppController(apiClient: api)..tab = 1;
    await c.refreshFeedFromUser();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: FeedScreen(controller: c)),
      ),
    );
    await tester.pumpAndSettle();
    expect(api.views, isEmpty);
    c.setTab(0);
    await tester.pumpAndSettle();
    expect(api.views, ['visible']);
    c.setTab(1);
    c.setTab(0);
    await tester.pumpAndSettle();
    expect(api.views, ['visible']);
    await tester.pumpWidget(const SizedBox());
    c.dispose();
  });

  testWidgets(
    'Tenth feedback reranks without replacing current card or restoring swipes',
    (tester) async {
      final api = ActivationApi()
        ..rows = [
          for (var i = 0; i < 35; i++) {'product': product('p$i')},
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
      expect(api.feedCalls, 1);
      for (var i = 0; i < 9; i++) {
        await c.sendProductEvent(context, 'p$i', 'like');
      }
      expect(api.feedCalls, 1);
      api.rows = [
        {'product': product('p0')},
        {'product': product('new-favourite')},
      ];
      await c.sendProductEvent(context, 'p9', 'like');
      await tester.pump();
      expect(api.feedCalls, 2);
      expect(c.currentProduct!.product.id, 'p10');
      expect(c.productFeed.map((r) => r.product.id), ['p10', 'new-favourite']);
      c.dispose();
    },
  );

  testWidgets('Why for you opens two factual reasons', (tester) async {
    final api = ActivationApi()
      ..rows = [
        {
          'product': product('explain'),
          'reasons': [
            'Похоже на ваши лайки',
            'Есть размер M · в бюджете до 10000 ₽',
          ],
        },
      ];
    final c = AppController(apiClient: api)..tab = 1;
    await c.refreshFeedFromUser();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: FeedScreen(controller: c)),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('Почему это вам'));
    await tester.pumpAndSettle();
    expect(find.text('Похоже на ваши лайки'), findsOneWidget);
    expect(find.text('Есть размер M · в бюджете до 10000 ₽'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    c.dispose();
  });

  testWidgets(
    'Onboarding hides continue before fifteen and offers replacement for missing photo',
    (tester) async {
      final api = ActivationApi()..rated = 14;
      final c = AppController(apiClient: api)..onboardingV2SubStep = 1;
      await c.loadStyleSwipe();
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(body: OnboardingV2Screen(controller: c)),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Показать мои образы'), findsNothing);
      expect(find.text('Показать другую вещь'), findsOneWidget);
      expect(find.text('Нравится'), findsNothing);
      await tester.tap(find.text('Показать другую вещь'));
      await tester.pumpAndSettle();
      expect(c.styleSwipeRated, 14);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      c.dispose();
    },
  );
}
