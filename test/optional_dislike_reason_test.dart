import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/network/api_client.dart';
import 'package:modish/features/recommendations/feed_screen.dart';
import 'package:modish/features/shopping/shopping_task_screen.dart';

import 'app_regressions_test.dart' show FakeApi, product;
import 'shopping_task_test.dart' show TaskApi, response;

class RefusalApi extends FakeApi {
  final refusals = <Map<String, dynamic>>[];

  @override
  Future<Map<String, dynamic>> recordRecommendationEvent({
    required String eventType,
    String? productId,
    String? outfitId,
    Map<String, dynamic>? meta,
  }) async {
    if (eventType == 'dislike') {
      refusals.add({'product_id': productId, ...?meta});
    }
    return {};
  }
}

class TaskRefusalApi extends TaskApi {
  final refusals = <Map<String, dynamic>>[];

  @override
  Future<Map<String, dynamic>> giveIntentFeedback(
    String intentId,
    String productId,
    String? reason,
    int version,
  ) async {
    refusals.add({
      'intent_id': intentId,
      'product_id': productId,
      'reason': reason,
      'expected_version': version,
    });
    return {...response(version: 2), 'products': <Map<String, dynamic>>[]};
  }
}

class FeedbackAdapter implements HttpClientAdapter {
  final requests = <Map<String, dynamic>>[];

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    requests.add(Map<String, dynamic>.from(options.data as Map));
    return ResponseBody.fromString(
      '{}',
      200,
      headers: {
        Headers.contentTypeHeader: [Headers.jsonContentType],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

void main() {
  test(
    'Intent HTTP payload omits unspecified reason and preserves explicit reasons',
    () async {
      final adapter = FeedbackAdapter();
      final dio = Dio(BaseOptions(baseUrl: 'https://example.test'))
        ..httpClientAdapter = adapter;
      final api = ApiClient(dio: dio);
      await api.giveIntentFeedback('task', 'product', null, 1);
      await api.giveIntentFeedback('task', 'product', 'too_expensive', 2);
      expect(adapter.requests, [
        {'product_id': 'product', 'expected_version': 1},
        {
          'product_id': 'product',
          'expected_version': 2,
          'reason': 'too_expensive',
        },
      ]);
      dio.close();
    },
  );

  for (final selection in <String?>[null, 'Не нравится цвет']) {
    testWidgets(
      'Discovery ${selection == null ? 'closing reasons' : 'choosing color'} sends one dislike',
      (tester) async {
        final api = RefusalApi()
          ..rows = [
            for (var i = 0; i < 3; i++)
              {
                'product': {...product('refusal-$i'), 'image_url': ''},
              },
          ];
        final controller = AppController(apiClient: api);
        await controller.refreshFeedFromUser();
        await tester.pumpWidget(
          MaterialApp(
            home: Scaffold(body: FeedScreen(controller: controller)),
          ),
        );
        await tester.pumpAndSettle();
        await tester.tap(find.text('Не моё'));
        await tester.pumpAndSettle();
        expect(find.text('Причина необязательна.'), findsOneWidget);
        if (selection == null) {
          await tester.binding.handlePopRoute();
        } else {
          await tester.ensureVisible(find.text(selection));
          await tester.pumpAndSettle();
          await tester.tap(find.text(selection));
        }
        await tester.pumpAndSettle();
        expect(api.refusals, hasLength(1));
        expect(api.refusals.single['product_id'], 'refusal-0');
        expect(api.refusals.single['surface'], 'feed');
        expect(api.refusals.single.containsKey('reason'), selection != null);
        if (selection != null) {
          expect(api.refusals.single['reason'], 'dont_like_color');
        }
        expect(
          controller.productFeed.map((card) => card.product.id),
          isNot(contains('refusal-0')),
        );
        expect(tester.takeException(), isNull);
        await tester.pumpWidget(const SizedBox());
        controller.dispose();
      },
    );
  }

  for (final selection in <String?>[
    null,
    'Без уточнения',
    'Слишком дорого',
    'Не подходит для этой задачи',
  ]) {
    testWidgets(
      'Intent refusal ${selection ?? 'closing reasons'} keeps task context',
      (tester) async {
        final api = TaskRefusalApi();
        final controller = AppController(apiClient: api);
        await controller.shoppingTask.submit('Брюки до 7000');
        await tester.pumpWidget(
          MaterialApp(home: ShoppingTaskScreen(controller: controller)),
        );
        await tester.pumpAndSettle();
        await tester.ensureVisible(find.text('Не подходит'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('Не подходит'));
        await tester.pumpAndSettle();
        if (selection == null) {
          await tester.binding.handlePopRoute();
        } else {
          await tester.ensureVisible(find.text(selection));
          await tester.pumpAndSettle();
          await tester.tap(find.text(selection));
        }
        await tester.pumpAndSettle();
        expect(api.refusals, [
          {
            'intent_id': 'task-id',
            'product_id': 'p1',
            'expected_version': 1,
            'reason': switch (selection) {
              'Слишком дорого' => 'too_expensive',
              'Не подходит для этой задачи' => 'not_for_context',
              _ => null,
            },
          },
        ]);
        expect(controller.shoppingTask.result!['version'], 2);
        expect(controller.shoppingTask.result!['products'], isEmpty);
        expect(api.recordedEvents, isNot(contains('dislike')));
        expect(tester.takeException(), isNull);
        await tester.pumpWidget(const SizedBox());
        controller.dispose();
      },
    );
  }
}
