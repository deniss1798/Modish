import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/features/products/models.dart' as prod;
import 'package:modish/features/products/product_detail_screen.dart';
import 'package:modish/features/shopping/shopping_task_controller.dart';

import 'app_regressions_test.dart' show FakeApi, product;
import 'feed_history_test.dart' show MemoryHistory;

Map<String, dynamic> _product(String id) => {...product(id), 'image_url': ''};
Map<String, dynamic> _row(String id) => {'product': _product(id)};
Map<String, dynamic> _intent([int version = 1]) => {
  'id': 'reaction-task',
  'version': version,
  'state': 'ready',
  'products': [_row('task-product')],
  'outfits': <Map<String, dynamic>>[],
};

DioException _error(String path, int status) {
  final request = RequestOptions(path: path);
  return DioException(
    requestOptions: request,
    response: Response(requestOptions: request, statusCode: status),
    type: DioExceptionType.badResponse,
  );
}

class _ReactionApi extends FakeApi {
  final events = <(String, String?)>[];
  Map<String, dynamic> eventResult = {};
  bool failEvent = false;
  bool failSaved = false;
  bool failFeed = false;
  int savedReads = 0;
  int feedbackCalls = 0;
  int reloadCalls = 0;
  bool feedbackConflict = false;
  Completer<void>? metricGate;
  Completer<Map<String, dynamic>>? eventResponse;
  Completer<Map<String, dynamic>>? feedbackResponse;
  final savedResponses = <Future<List<Map<String, dynamic>>>>[];

  @override
  Future<Map<String, dynamic>> recordRecommendationEvent({
    required String eventType,
    String? productId,
    String? outfitId,
    Map<String, dynamic>? meta,
  }) async {
    events.add((eventType, productId));
    if (failEvent) throw _error('/recommendations/events', 500);
    return eventResponse?.future ?? eventResult;
  }

  @override
  Future<void> metricsEvent(String name, {Map<String, dynamic>? meta}) async {
    await metricGate?.future;
  }

  @override
  Future<List<Map<String, dynamic>>> savedProducts() async {
    savedReads++;
    if (failSaved) throw _error('/saved-products', 500);
    return savedResponses.isEmpty ? [] : savedResponses.removeAt(0);
  }

  @override
  Future<List<Map<String, dynamic>>> productFeed({
    int limit = 30,
    String scenario = 'daily',
    String? source,
    int? minPrice,
    int? maxPrice,
    List<String> categories = const [],
    List<String> sizes = const [],
    List<String> colors = const [],
    List<String> excludeIds = const [],
  }) {
    if (failFeed) {
      feedCalls++;
      return Future.error(_error('/feed', 503));
    }
    return super.productFeed(
      limit: limit,
      scenario: scenario,
      source: source,
      minPrice: minPrice,
      maxPrice: maxPrice,
      categories: categories,
      sizes: sizes,
      colors: colors,
      excludeIds: excludeIds,
    );
  }

  @override
  Future<Map<String, dynamic>> productById(String id) async => _product(id);

  @override
  Future<Map<String, dynamic>> giveIntentFeedback(
    String intentId,
    String productId,
    String? reason,
    int version,
  ) async {
    feedbackCalls++;
    if (feedbackConflict) {
      throw _error('/shopping-intents/$intentId/feedback', 409);
    }
    return feedbackResponse?.future ?? _intent(version + 1);
  }

  @override
  Future<Map<String, dynamic>> shoppingIntent(String id) async {
    reloadCalls++;
    return _intent(2);
  }
}

AppController _controller(_ReactionApi api, {bool disposeOnTearDown = true}) {
  api.rows = [for (var index = 0; index < 20; index++) _row('fresh-$index')];
  addTearDown(api.dispose);
  final controller = AppController(apiClient: api, feedHistory: MemoryHistory())
    ..stage = AppStage.home
    ..productFeed = [
      for (var index = 0; index < 60; index++)
        prod.FeedCard.fromApi(_row('old-$index')),
    ];
  if (disposeOnTearDown) addTearDown(controller.dispose);
  return controller;
}

Future<BuildContext> _mount(WidgetTester tester) async {
  late BuildContext context;
  await tester.pumpWidget(
    MaterialApp(
      home: Builder(
        builder: (value) {
          context = value;
          return const Scaffold(body: SizedBox());
        },
      ),
    ),
  );
  addTearDown(() => tester.pumpWidget(const SizedBox()));
  return context;
}

Future<void> _react(
  AppController controller,
  BuildContext context,
  int count, {
  String event = 'skip',
  String prefix = 'reaction',
}) async {
  for (var index = 0; index < count; index++) {
    await controller.sendProductEvent(context, '$prefix-$index', event);
  }
}

void main() {
  testWidgets(
    'Mixed meaningful reactions rerank at ten; passive events never count',
    (tester) async {
      final api = _ReactionApi();
      final controller = _controller(api);
      final context = await _mount(tester);
      for (final event in [
        'view',
        'open_product',
        'affiliate_click',
        'unsave',
      ]) {
        await _react(controller, context, 3, event: event, prefix: event);
      }
      for (var index = 0; index < 9; index++) {
        await controller.sendProductEvent(
          context,
          'mixed-$index',
          ['like', 'save', 'dislike', 'skip'][index % 4],
        );
      }
      expect(api.feedCalls, 0);
      await controller.sendProductEvent(context, 'tenth', 'save');
      await tester.pump();
      expect(api.feedCalls, 1);
      expect(controller.currentProduct!.product.id, 'old-0');
      expect(controller.productFeed.skip(1).first.product.id, 'fresh-0');
      expect(api.events, hasLength(22));
    },
  );

  testWidgets(
    'Confirmed tenth save counts even when its optional collection GET fails',
    (tester) async {
      final api = _ReactionApi();
      final controller = _controller(api)
        ..savedProductRows = [
          {'product': _product('confirmed-old-save')},
        ];
      final context = await _mount(tester);
      await _react(controller, context, 9);
      api.failSaved = true;
      await controller.sendProductEvent(context, 'tenth-save', 'save');
      await tester.pump();
      expect(api.feedCalls, 1);
      expect(api.savedReads, 1);
      expect(
        controller.savedProductRows.single['product']['id'],
        'confirmed-old-save',
      );
      expect(controller.error, isNull);
    },
  );

  testWidgets(
    'The tenth acknowledgement starts ranking before optional metrics completes',
    (tester) async {
      final api = _ReactionApi();
      final controller = _controller(api);
      final context = await _mount(tester);
      await _react(controller, context, 9);
      api.metricGate = Completer<void>();
      var completed = false;
      final pending = controller
          .sendProductEvent(context, 'tenth-delayed-metric', 'like')
          .then((_) => completed = true);
      await tester.pump();
      expect(api.feedCalls, 1);
      expect(completed, isTrue);
      expect(controller.isLoading, isFalse);
      api.metricGate!.complete();
      await pending;
      expect(controller.error, isNull);
    },
  );

  testWidgets('A pending saved GET never holds the confirmed tenth save busy', (
    tester,
  ) async {
    final api = _ReactionApi();
    final controller = _controller(api);
    final context = await _mount(tester);
    await _react(controller, context, 9);
    final saved = Completer<List<Map<String, dynamic>>>();
    api.savedResponses.add(saved.future);
    var completed = false;
    final pending = controller
        .sendProductEvent(context, 'tenth-pending-save', 'save')
        .then((_) => completed = true);
    await tester.pump();
    expect(api.feedCalls, 1);
    expect(api.savedReads, 1);
    expect(completed, isTrue);
    expect(controller.isLoading, isFalse);
    saved.complete([
      {'product': _product('saved-after-ack')},
    ]);
    await pending;
    await tester.pump();
    expect(
      controller.savedProductRows.single['product']['id'],
      'saved-after-ack',
    );
    expect(controller.error, isNull);
  });

  testWidgets(
    'Rejected events and idempotent saves do not advance the ten-reaction boundary',
    (tester) async {
      final api = _ReactionApi();
      final controller = _controller(api);
      final context = await _mount(tester);
      await _react(controller, context, 9);
      api.failEvent = true;
      await controller.sendProductEvent(context, 'rejected', 'like');
      api.failEvent = false;
      api.eventResult = {'idempotent': true};
      await controller.sendProductEvent(context, 'already-saved', 'save');
      expect(api.feedCalls, 0);
      api.eventResult = {};
      await controller.sendProductEvent(context, 'new-accepted', 'like');
      await tester.pump();
      expect(api.feedCalls, 1);
      expect(api.events, hasLength(12));
    },
  );

  testWidgets(
    'A late saved read cannot overwrite a newer accepted collection',
    (tester) async {
      final api = _ReactionApi();
      final controller = _controller(api);
      final context = await _mount(tester);
      final olderRead = Completer<List<Map<String, dynamic>>>();
      api.savedResponses.addAll([
        olderRead.future,
        Future.value([
          {'product': _product('newest-save')},
        ]),
      ]);
      final older = controller.sendProductEvent(context, 'old-save', 'save');
      await tester.pump();
      expect(api.savedReads, 1);
      await controller.sendProductEvent(context, 'new-save', 'save');
      await tester.pump();
      expect(
        controller.savedProductRows.single['product']['id'],
        'newest-save',
      );
      olderRead.complete([
        {'product': _product('stale-save')},
      ]);
      await older;
      await tester.pump();
      expect(
        controller.savedProductRows.single['product']['id'],
        'newest-save',
      );
    },
  );

  for (final dispose in [false, true]) {
    testWidgets(
      'An optional saved GET after ${dispose ? 'dispose' : 'logout'} cannot publish or notify',
      (tester) async {
        final api = _ReactionApi();
        final controller = _controller(api, disposeOnTearDown: !dispose);
        final context = await _mount(tester);
        final saved = Completer<List<Map<String, dynamic>>>();
        api.savedResponses.add(saved.future);
        await controller.sendProductEvent(context, 'accepted-save', 'save');
        expect(api.savedReads, 1);
        expect(controller.isLoading, isFalse);

        if (dispose) {
          controller.dispose();
        } else {
          await tester.runAsync(controller.logout);
        }
        controller.savedProductRows = [
          {'product': _product('current-owner-save')},
        ];
        var notifications = 0;
        if (!dispose) controller.addListener(() => notifications++);
        saved.complete([
          {'product': _product('old-owner-save')},
        ]);
        await tester.pump();
        expect(
          controller.savedProductRows.single['product']['id'],
          'current-owner-save',
        );
        expect(notifications, 0);
        expect(tester.takeException(), isNull);
      },
    );
  }

  testWidgets('A failed rerank preserves the usable current queue', (
    tester,
  ) async {
    final api = _ReactionApi();
    final controller = _controller(api);
    final context = await _mount(tester);
    api.failFeed = true;
    await _react(controller, context, 10);
    await tester.pump();
    expect(api.feedCalls, 1);
    expect(controller.productFeed, hasLength(60));
    expect(controller.currentProduct!.product.id, 'old-0');
    expect(controller.error, isNull);
    expect(controller.feedRefreshing, isFalse);
  });

  testWidgets(
    'A twentieth-reaction response wins over an older tenth-reaction rank',
    (tester) async {
      final api = _ReactionApi();
      final controller = _controller(api);
      final context = await _mount(tester);
      final older = Completer<List<Map<String, dynamic>>>();
      api.nextFeed = older;
      await _react(controller, context, 10);
      expect(api.feedCalls, 1);
      final newer = Completer<List<Map<String, dynamic>>>();
      api.nextFeed = newer;
      await _react(controller, context, 10, prefix: 'next-ten');
      expect(api.feedCalls, 2);
      newer.complete([_row('newest-ranked')]);
      await tester.pump();
      expect(controller.productFeed.map((card) => card.product.id), [
        'old-0',
        'newest-ranked',
      ]);
      older.complete([_row('obsolete-ranked')]);
      await tester.pump();
      expect(controller.productFeed.map((card) => card.product.id), [
        'old-0',
        'newest-ranked',
      ]);
      expect(controller.feedRefreshing, isFalse);
    },
  );

  testWidgets(
    'A rerank keeps the response-time head and cannot restore an intervening dismissal',
    (tester) async {
      final api = _ReactionApi();
      final controller = _controller(api);
      final context = await _mount(tester);
      final ranking = Completer<List<Map<String, dynamic>>>();
      api.nextFeed = ranking;
      await _react(controller, context, 10);
      await controller.sendProductEvent(context, 'old-0', 'skip');
      expect(controller.currentProduct!.product.id, 'old-1');
      ranking.complete([_row('old-0'), _row('old-1'), _row('fresh-live')]);
      await tester.pump();
      expect(controller.productFeed.map((card) => card.product.id), [
        'old-1',
        'fresh-live',
      ]);
    },
  );

  for (final dispose in [false, true]) {
    testWidgets(
      'A late event after ${dispose ? 'dispose' : 'logout'} cannot count against the next session',
      (tester) async {
        final api = _ReactionApi();
        final controller = _controller(api, disposeOnTearDown: !dispose);
        final context = await _mount(tester);
        await _react(controller, context, 9);
        api.eventResponse = Completer<Map<String, dynamic>>();
        final pending = controller.sendProductEvent(
          context,
          'late-tenth',
          'like',
        );
        await tester.pump();
        if (dispose) {
          controller.dispose();
        } else {
          await tester.runAsync(controller.logout);
          controller.productFeed = [
            for (var index = 0; index < 60; index++)
              prod.FeedCard.fromApi(_row('replacement-$index')),
          ];
        }
        api.eventResponse!.complete({});
        await pending;
        expect(api.feedCalls, 0);
        if (!dispose) {
          api.eventResponse = null;
          await _react(controller, context, 9, prefix: 'replacement-action');
          expect(api.feedCalls, 0);
          await controller.sendProductEvent(
            context,
            'replacement-tenth',
            'skip',
          );
          expect(api.feedCalls, 1);
        }
      },
    );
  }

  testWidgets('A real product-detail like joins nine prior feed reactions', (
    tester,
  ) async {
    final api = _ReactionApi();
    final controller = _controller(api);
    final context = await _mount(tester);
    await _react(controller, context, 9);
    await tester.pumpWidget(
      MaterialApp(
        home: ProductDetailScreen(
          controller: controller,
          card: prod.FeedCard.fromApi(_row('detail-like')),
          eventMeta: const {'surface': 'product_detail'},
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byIcon(Icons.favorite_border));
    await tester.pumpAndSettle();
    expect(api.events.last, ('like', 'detail-like'));
    expect(api.feedCalls, 1);
    expect(tester.takeException(), isNull);
  });

  for (final event in ['like', 'save']) {
    testWidgets(
      'Intent $event uses the same boundary without another product POST',
      (tester) async {
        final api = _ReactionApi();
        final controller = _controller(api);
        final context = await _mount(tester);
        await _react(controller, context, 9);
        controller.shoppingTask.result = _intent();
        await controller.shoppingTask.react('task-product', event);
        await tester.pump();
        expect(api.feedCalls, 1);
        expect(api.events, hasLength(10));
        expect(api.events.last, (event, 'task-product'));
        await controller.shoppingTask.react('task-product', event);
        expect(api.events, hasLength(10));
        expect(api.feedCalls, 1);
      },
    );
  }

  testWidgets(
    'Intent permanent dislike counts once without reposting its server-side event',
    (tester) async {
      final api = _ReactionApi();
      final controller = _controller(api);
      final context = await _mount(tester);
      await _react(controller, context, 9);
      controller.shoppingTask.result = _intent();
      await controller.shoppingTask.feedback('task-product', 'not_my_style');
      await tester.pump();
      expect(api.feedbackCalls, 1);
      expect(api.events, hasLength(9));
      expect(api.feedCalls, 1);
    },
  );

  for (final reason in ['too_expensive', 'not_for_context']) {
    testWidgets(
      'Intent $reason stays task-local and does not count as a permanent reaction',
      (tester) async {
        final api = _ReactionApi();
        final controller = _controller(api);
        final context = await _mount(tester);
        await _react(controller, context, 9);
        controller.shoppingTask.result = _intent();
        await controller.shoppingTask.feedback('task-product', reason);
        expect(api.feedbackCalls, 1);
        expect(api.feedCalls, 0);
        await controller.sendProductEvent(context, 'actual-tenth', 'skip');
        expect(api.feedCalls, 1);
      },
    );
  }

  test(
    'Intent callbacks exclude idempotent saves and 409 reconciliation GETs',
    () async {
      final api = _ReactionApi();
      addTearDown(api.dispose);
      final accepted = <String>[];
      final task = ShoppingTaskController(
        api,
        onAcceptedProductReaction: (event, _) => accepted.add(event),
      )..result = _intent();
      addTearDown(task.dispose);
      api.eventResult = {'idempotent': true};
      await task.react('task-product', 'save');
      expect(accepted, isEmpty);
      api.feedbackConflict = true;
      await task.feedback('task-product', 'not_my_style');
      expect(api.feedbackCalls, 1);
      expect(api.reloadCalls, 1);
      expect(accepted, isEmpty);
      expect(task.result!['version'], 2);
      expect(task.error, isNull);
      api.feedbackConflict = false;
      await task.feedback('task-product');
      expect(accepted, ['dislike']);
    },
  );

  test(
    'A callback failure cannot turn a durable Intent reaction into a failed action',
    () async {
      final api = _ReactionApi();
      addTearDown(api.dispose);
      final task = ShoppingTaskController(
        api,
        onAcceptedProductReaction: (_, _) =>
            throw StateError('Optional refresh failed'),
      )..result = _intent();
      addTearDown(task.dispose);
      await task.react('task-product', 'like');
      expect(task.hasReacted('task-product', 'like'), isTrue);
      expect(task.error, isNull);
      expect(api.events, [('like', 'task-product')]);
    },
  );

  for (final event in ['like', 'dislike']) {
    for (final boundary in ['clear', 'dispose', 'session']) {
      test(
        'A late Intent $event after $boundary never invokes a current-owner callback',
        () async {
          final api = _ReactionApi();
          addTearDown(api.dispose);
          final accepted = <String>[];
          final task = ShoppingTaskController(
            api,
            onAcceptedProductReaction: (value, _) => accepted.add(value),
          )..result = _intent();
          if (boundary != 'dispose') addTearDown(task.dispose);
          final response = Completer<Map<String, dynamic>>();
          if (event == 'like') {
            api.eventResponse = response;
          } else {
            api.feedbackResponse = response;
          }
          final pending = event == 'like'
              ? task.react('task-product', 'like')
              : task.feedback('task-product', 'not_my_style');
          if (boundary == 'clear') {
            task.clear();
          } else if (boundary == 'dispose') {
            task.dispose();
          } else {
            api.clearToken();
          }
          response.complete(event == 'like' ? {} : _intent(2));
          await pending;
          expect(accepted, isEmpty);
          expect(api.events.length + api.feedbackCalls, 1);
        },
      );
    }
  }
}
