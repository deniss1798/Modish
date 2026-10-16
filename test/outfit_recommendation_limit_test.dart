import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/network/api_client.dart';
import 'package:modish/core/presentation/outfit_recommendations.dart';
import 'package:modish/features/onboarding/onboarding_v2_screen.dart';
import 'package:modish/features/onboarding/wow_outfit.dart';
import 'package:modish/features/outfits/outfits_screen.dart';
import 'package:modish/features/recommendations/saved_screen.dart';
import 'package:modish/features/shopping/shopping_task_screen.dart';

import 'app_regressions_test.dart' show FakeApi;

Map<String, dynamic> _outfit(String id, {String scenario = 'daily'}) => {
  'id': id,
  'title': 'Синтетический образ $id',
  'style_direction': scenario,
  'total_price': 3000,
  'reason': 'Проверенные условия $id',
  'is_saved': false,
  'items': {
    for (final slot in ['top', 'bottom', 'shoes']) slot: '$id-$slot',
  },
  'products': {
    for (final slot in ['top', 'bottom', 'shoes'])
      slot: {
        'id': '$id-$slot',
        'title': 'Вещь $id-$slot',
        'price': 1000,
        'image_url': '',
      },
  },
};

Map<String, dynamic> _intent({
  String state = 'ready',
  String type = 'outfit',
  List<Map<String, dynamic>> outfits = const [],
  List<Map<String, dynamic>> products = const [],
  String? message,
}) => {
  'id': 'cap-task',
  'version': 1,
  'state': state,
  'text': 'Собрать образ до 6000',
  'constraints': {'intent_type': type, 'budget_max': 6000},
  'outfits': outfits,
  'products': products,
  'message': ?message,
};

class _LimitApi extends FakeApi {
  final calls = <(String, int)>[];
  final metrics = <Map<String, dynamic>>[];
  final current = <Map<String, dynamic>>[];
  final saved = <Map<String, dynamic>>[];
  int? acceptedCount;

  @override
  Future<List<Map<String, dynamic>>> outfitsGenerate({
    int count = 3,
    String scenario = 'daily',
  }) async {
    calls.add((scenario, count));
    final rows = [
      for (var index = 0; index < (acceptedCount ?? count); index++)
        _outfit('$scenario-${calls.length}-$index', scenario: scenario),
    ];
    current.addAll(rows);
    return rows;
  }

  @override
  Future<List<Map<String, dynamic>>> outfitsList({
    String? scenario,
    bool savedOnly = false,
  }) async => List.of(savedOnly ? saved : current);

  @override
  Future<void> metricsEvent(String name, {Map<String, dynamic>? meta}) async {
    metrics.add({'name': name, ...?meta});
  }
}

class _CountAdapter implements HttpClientAdapter {
  final requests = <RequestOptions>[];

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    requests.add(options);
    return ResponseBody.fromString(
      jsonEncode(options.path == '/onboarding/complete' ? {'outfits': []} : []),
      200,
      headers: {
        Headers.contentTypeHeader: [Headers.jsonContentType],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

AppController _controller(_LimitApi api) {
  addTearDown(api.dispose);
  final controller = AppController(apiClient: api);
  addTearDown(controller.dispose);
  return controller;
}

Future<void> _mount(WidgetTester tester, Widget screen) async {
  // Build every synthetic card, including an uncapped implementation's tail.
  // Otherwise a short viewport could make lazy ListView children hide a bug.
  tester.view.physicalSize = const Size(420, 8000);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(() async => tester.pumpWidget(const SizedBox.shrink()));
  await tester.pumpWidget(MaterialApp(home: screen));
  await tester.pumpAndSettle();
}

void main() {
  test(
    'The shared cap keeps order and accepts zero, one or two without filling',
    () {
      expect(maxOutfitRecommendations, 3);
      for (var size = 0; size <= 10; size++) {
        final input = List<int>.generate(size, (index) => index);
        final before = List.of(input);
        final result = limitOutfitRecommendations(input);
        expect(
          result,
          List<int>.generate(size < 3 ? size : 3, (index) => index),
        );
        expect(input, before);
        expect(identical(result, input), isFalse);
      }
    },
  );

  test('The cap stops consuming a lazy recommendation source after three', () {
    var consumed = 0;
    Iterable<int> source() sync* {
      for (var index = 0; index < 10; index++) {
        consumed++;
        yield index;
      }
    }

    expect(limitOutfitRecommendations(source()), [0, 1, 2]);
    expect(consumed, 3);
  });

  test(
    'All scenarios request one outfit each and count three actual results',
    () async {
      final api = _LimitApi();
      final controller = _controller(api);
      final created = await controller.generateOutfitsV2(
        count: 3,
        scenario: 'all',
      );
      expect(api.calls, [('daily', 1), ('office', 1), ('evening', 1)]);
      expect(api.calls.fold<int>(0, (total, call) => total + call.$2), 3);
      expect(created, 3);
      expect(controller.outfits, hasLength(3));
      expect(api.metrics.single, {
        'name': 'outfit_generated',
        'count': 3,
        'scenario': 'all',
      });
    },
  );

  test(
    'A smaller all-scenario request stays within its total budget',
    () async {
      final api = _LimitApi();
      final controller = _controller(api);
      expect(await controller.generateOutfitsV2(count: 2, scenario: 'all'), 2);
      expect(api.calls, [('daily', 1), ('office', 1)]);
      expect(controller.outfits, hasLength(2));
    },
  );

  for (final accepted in [0, 1, 2]) {
    test(
      'A $accepted-result generation is accepted without synthetic fill',
      () async {
        final api = _LimitApi()..acceptedCount = accepted;
        final controller = _controller(api);
        final created = await controller.generateOutfitsV2(
          count: 3,
          scenario: 'daily',
        );
        expect(api.calls, [('daily', 3)]);
        expect(created, accepted);
        expect(controller.outfits, hasLength(accepted));
        expect(api.metrics.single['count'], accepted);
        expect(controller.error, isNull);
      },
    );
  }

  test(
    'Legacy generation and onboarding completion default to three over transport',
    () async {
      final adapter = _CountAdapter();
      final api = ApiClient(
        dio: Dio(BaseOptions(baseUrl: 'https://modish.test'))
          ..httpClientAdapter = adapter,
      );
      addTearDown(api.dispose);
      await api.generateOutfits();
      await api.onboardingComplete();
      expect(adapter.requests, hasLength(2));
      expect(adapter.requests[0].path, '/recommendations/generate');
      expect(adapter.requests[0].data['count'], 3);
      expect(adapter.requests[1].path, '/onboarding/complete');
      expect(adapter.requests[1].queryParameters['count'], 3);
    },
  );

  testWidgets(
    'Current all and scenario views cap after filtering, without dropping stored rows',
    (tester) async {
      final controller = _controller(_LimitApi())
        ..outfits = [
          for (var index = 0; index < 10; index++)
            _outfit('current-$index', scenario: index < 5 ? 'office' : 'daily'),
        ];
      await _mount(
        tester,
        Scaffold(body: OutfitsScreen(controller: controller)),
      );
      expect(find.byType(OutfitCard), findsNWidgets(3));
      for (var index = 0; index < 10; index++) {
        expect(
          find.text('Синтетический образ current-$index'),
          index < 3 ? findsOneWidget : findsNothing,
        );
      }
      await tester.tap(find.text('Каждый день'));
      await tester.pumpAndSettle();
      expect(find.byType(OutfitCard), findsNWidgets(3));
      for (var index = 0; index < 10; index++) {
        expect(
          find.text('Синтетический образ current-$index'),
          index >= 5 && index < 8 ? findsOneWidget : findsNothing,
        );
      }
      expect(controller.outfits, hasLength(10));
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('Historical WOW with five outfits shows its first three only', (
    tester,
  ) async {
    final controller = _controller(_LimitApi())
      ..onboardingV2SubStep = 5
      ..wowOutfits = [
        for (var index = 0; index < 5; index++)
          WowOutfit.fromApi(_outfit('historical-wow-$index')),
      ];
    await _mount(tester, OnboardingV2Screen(controller: controller));
    expect(find.byType(OutfitCard), findsNWidgets(3));
    for (var index = 0; index < 5; index++) {
      expect(
        find.text('Синтетический образ historical-wow-$index'),
        index < 3 ? findsOneWidget : findsNothing,
      );
    }
    expect(
      controller.wowOutfits,
      hasLength(5),
      reason: 'Rendering must not mutate historical input',
    );
    expect(tester.takeException(), isNull);
  });

  for (final accepted in [0, 1, 2]) {
    testWidgets(
      'WOW accepts $accepted outfits and keeps the empty state honest',
      (tester) async {
        final controller = _controller(_LimitApi())
          ..onboardingV2SubStep = 5
          ..wowOutfits = [
            for (var index = 0; index < accepted; index++)
              WowOutfit.fromApi(_outfit('short-wow-$index')),
          ];
        await _mount(tester, OnboardingV2Screen(controller: controller));
        expect(find.byType(OutfitCard), findsNWidgets(accepted));
        expect(find.text('Открыть мою ленту'), findsOneWidget);
        if (accepted == 0) {
          expect(
            find.text('Подходящие образы пока не найдены'),
            findsOneWidget,
          );
          expect(find.text('Уже больше похоже на вас'), findsNothing);
          expect(
            find.textContaining(noOutfitRecommendationsMessage),
            findsOneWidget,
          );
        } else {
          expect(find.text('Уже больше похоже на вас'), findsOneWidget);
          expect(find.text('Подходящие образы пока не найдены'), findsNothing);
        }
        expect(tester.takeException(), isNull);
      },
    );
  }

  testWidgets(
    'Saved library renders all five outfits and keeps its full counter',
    (tester) async {
      final controller = _controller(_LimitApi())
        ..savedOutfits = [
          for (var index = 0; index < 5; index++)
            {..._outfit('saved-$index'), 'is_saved': true},
        ];
      await _mount(tester, Scaffold(body: SavedScreen(controller: controller)));
      expect(find.text('Образы · 5'), findsOneWidget);
      expect(find.byType(OutfitCard), findsNWidgets(5));
      for (var index = 0; index < 5; index++) {
        expect(find.text('Синтетический образ saved-$index'), findsOneWidget);
      }
      expect(controller.savedOutfits, hasLength(5));
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'Intent with eight outfits renders three and reports only that displayed count',
    (tester) async {
      final controller = _controller(_LimitApi());
      controller.shoppingTask.result = _intent(
        outfits: [
          for (var index = 0; index < 8; index++) _outfit('intent-$index'),
        ],
      );
      await _mount(tester, ShoppingTaskScreen(controller: controller));
      expect(find.text('3 полных образа'), findsOneWidget);
      expect(find.text('8 полных образов'), findsNothing);
      for (var index = 0; index < 8; index++) {
        expect(
          find.text('Проверенные условия intent-$index'),
          index < 3 ? findsOneWidget : findsNothing,
        );
        expect(
          find.byKey(ValueKey('detail-outfit-intent-$index-top')),
          index < 3 ? findsOneWidget : findsNothing,
        );
      }
      expect(controller.shoppingTask.result!['outfits'], hasLength(8));
      expect(tester.takeException(), isNull);
    },
  );

  for (final type in ['outfit', 'single_item']) {
    testWidgets(
      'Ready empty $type Intent supplies honest fallback without server message',
      (tester) async {
        final controller = _controller(_LimitApi());
        controller.shoppingTask.result = _intent(type: type);
        await _mount(tester, ShoppingTaskScreen(controller: controller));
        expect(
          find.text(
            type == 'outfit'
                ? noOutfitRecommendationsMessage
                : 'Пока нет вещей, которые проходят условия вашего запроса.',
          ),
          findsOneWidget,
        );
        expect(find.text('Уточнить запрос'), findsOneWidget);
        expect(find.textContaining('полных образа'), findsNothing);
        expect(find.textContaining('Итого'), findsNothing);
        expect(tester.takeException(), isNull);
      },
    );
  }

  testWidgets(
    'Pending Intent hides cached outfits, products and stale success message',
    (tester) async {
      final controller = _controller(_LimitApi());
      controller.shoppingTask.result = _intent(
        state: 'pending',
        message: 'STALE_SUCCESS_MESSAGE',
        outfits: [_outfit('pending-stale')],
        products: [
          {
            'product': {
              'id': 'pending-item',
              'title': 'STALE_PRODUCT_TITLE',
              'price': 1000,
              'image_url': '',
            },
            'reasons': <String>[],
          },
        ],
      );
      await _mount(tester, ShoppingTaskScreen(controller: controller));
      expect(
        find.text(
          'Подборка ещё готовится. Подождите немного и повторите тот же запрос.',
        ),
        findsOneWidget,
      );
      expect(find.text('STALE_SUCCESS_MESSAGE'), findsNothing);
      expect(find.text('STALE_PRODUCT_TITLE'), findsNothing);
      expect(find.text('Проверенные условия pending-stale'), findsNothing);
      expect(
        find.byKey(const ValueKey('detail-outfit-pending-stale-top')),
        findsNothing,
      );
      expect(find.text(noOutfitRecommendationsMessage), findsNothing);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'Zero generation with no current outfits never claims current outfits were saved',
    (tester) async {
      final api = _LimitApi()..acceptedCount = 0;
      final controller = _controller(api);
      await _mount(
        tester,
        Scaffold(body: OutfitsScreen(controller: controller)),
      );
      await tester.tap(find.text('Собрать образы'));
      await tester.pumpAndSettle();
      expect(find.byType(OutfitCard), findsNothing);
      expect(find.text(noOutfitRecommendationsMessage), findsWidgets);
      expect(
        find.text('Новых сочетаний пока нет. Текущие образы сохранены.'),
        findsNothing,
      );
      expect(controller.outfits, isEmpty);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'Empty selected scenario does not claim other-scenario outfits were saved',
    (tester) async {
      final api = _LimitApi()
        ..acceptedCount = 0
        ..current.add(_outfit('office-only', scenario: 'office'));
      final controller = _controller(api)..outfits = List.of(api.current);
      await _mount(
        tester,
        Scaffold(body: OutfitsScreen(controller: controller)),
      );
      await tester.tap(find.text('Каждый день'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Собрать образы'));
      await tester.pumpAndSettle();
      expect(api.calls, [('daily', 3)]);
      expect(find.byType(OutfitCard), findsNothing);
      expect(find.text(noOutfitRecommendationsMessage), findsWidgets);
      expect(
        find.text('Новых сочетаний пока нет. Текущие образы сохранены.'),
        findsNothing,
      );
      expect(controller.outfits.single['id'], 'office-only');
      expect(tester.takeException(), isNull);
    },
  );
}
