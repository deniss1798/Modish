import 'dart:async';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/features/shopping/shopping_task_controller.dart';
import 'package:modish/features/shopping/shopping_task_screen.dart';
import 'package:modish/features/recommendations/feed_screen.dart';
import 'package:modish/features/catalog/discovery_screen.dart';
import 'package:modish/core/theme/app_theme.dart';
import 'app_regressions_test.dart' show FakeApi;

Map<String, dynamic> response({int version = 1, int maximum = 7000}) => {
  'id': 'task-id',
  'text': 'Брюки в офис до 7000',
  'version': version,
  'state': 'ready',
  'constraints': {
    'requested_categories': ['брюки'],
    'occasion': 'office',
    'budget_max': maximum,
  },
  'products': [
    {
      'product': {
        'id': 'p1',
        'title': 'Брюки прямые',
        'price': 5000,
        'image_url': '',
      },
      'reasons': ['Размер RU 48 указан в наличии', 'Цена в пределах 7 000 ₽'],
    },
  ],
};

class TaskApi extends FakeApi {
  final keys = <String>[];
  final refinements = <String>[];
  final recordedEvents = <String>[];
  final recordedMeta = <Map<String, dynamic>>[];
  @override
  Future<Map<String, dynamic>> recordRecommendationEvent({
    required String eventType,
    String? productId,
    String? outfitId,
    Map<String, dynamic>? meta,
  }) async {
    recordedEvents.add(eventType);
    recordedMeta.add({...?meta});
    return {};
  }

  Completer<Map<String, dynamic>>? pending;
  bool fail = false;
  bool createPending = false;
  bool conflict = false;
  bool expired = false;
  final closures = <String>[];
  @override
  Future<void> closeShoppingIntent(
    String id,
    int version,
    String outcome,
  ) async {
    closures.add(outcome);
  }

  @override
  Future<Map<String, dynamic>> createShoppingIntent(
    String text,
    String requestId,
  ) async {
    keys.add(requestId);
    if (fail) throw Exception('offline');
    if (createPending) {
      final request = RequestOptions(path: '/shopping-intents');
      throw DioException(
        requestOptions: request,
        type: DioExceptionType.badResponse,
        response: Response(
          requestOptions: request,
          statusCode: 409,
          data: {
            'detail': 'Подборка ещё готовится. Повторите запрос чуть позже.',
          },
        ),
      );
    }
    return pending?.future ?? response();
  }

  @override
  Future<Map<String, dynamic>> refineShoppingIntent(
    String id,
    String text,
    int version, {
    String? referenceProductId,
    String? referenceOutfitId,
  }) async {
    refinements.add(text);
    expect(id, 'task-id');
    expect(version, 1);
    if (conflict || expired) {
      final request = RequestOptions(path: '/shopping-intents/task-id/refine');
      throw DioException(
        requestOptions: request,
        response: Response(
          requestOptions: request,
          statusCode: expired ? 410 : 409,
        ),
      );
    }
    return response(version: 2, maximum: 5600);
  }

  @override
  Future<Map<String, dynamic>> shoppingIntent(String id) async =>
      response(version: 2, maximum: 5600);
}

class OutfitTaskApi extends TaskApi {
  String? replacedSlot;
  int saves = 0;
  Map<String, dynamic> outfitResult(int version) => {
    'id': 'task-id',
    'version': version,
    'state': 'ready',
    'text': 'Образ до 20000 без каблуков',
    'constraints': {
      'intent_type': 'outfit',
      'budget_max': 20000,
      'excluded_features': {
        'heel': ['heels'],
      },
    },
    'products': <Map<String, dynamic>>[],
    'outfits': [
      {
        'id': 'look-$version',
        'total_price': 15000,
        'reason': 'Размеры указаны в наличии',
        'is_saved': false,
        'products': {
          'top': {
            'id': 'top',
            'title': 'Рубашка',
            'price': 5000,
            'image_url': '',
          },
          'bottom': {
            'id': 'bottom',
            'title': 'Брюки',
            'price': 6000,
            'image_url': '',
          },
          'shoes': {
            'id': 'shoes-$version',
            'title': 'Лоферы',
            'price': 4000,
            'image_url': '',
          },
        },
      },
    ],
  };
  @override
  Future<Map<String, dynamic>> createShoppingIntent(
    String text,
    String requestId,
  ) async => outfitResult(1);
  @override
  Future<Map<String, dynamic>> replaceIntentOutfitItem(
    String intentId,
    String outfitId,
    String slot,
    int version,
  ) async {
    expect(version, 1);
    expect(outfitId, 'look-1');
    replacedSlot = slot;
    return outfitResult(2);
  }

  @override
  Future<void> saveIntentOutfit(
    String intentId,
    String outfitId,
    int version,
  ) async {
    expect(version, 2);
    expect(outfitId, 'look-2');
    saves++;
  }
}

class ClarificationTaskApi extends TaskApi {
  final texts = <String>[];
  static const unsupported = 'Уточните неподдержанные условия: туфли';
  static const missingItem = 'Укажите вещь: например, брюки, платье или куртка';

  @override
  Future<Map<String, dynamic>> createShoppingIntent(
    String text,
    String requestId,
  ) async {
    texts.add(text);
    if (texts.length > 1) return response();
    return {
      'id': 'task-id',
      'version': 1,
      'text': text,
      'state': 'needs_clarification',
      'constraints': {
        'colors': ['blue'],
        'unresolved': [unsupported, missingItem, unsupported],
      },
      'effective_budget_max': 25000,
      'message': 'Уточните запрос: $unsupported; $missingItem',
      'products': <Map<String, dynamic>>[],
      'outfits': <Map<String, dynamic>>[],
    };
  }
}

void main() {
  test('Concrete footwear constraints use human-readable labels', () {
    expect(
      constraintLabels({
        'requested_categories': ['обувь'],
        'required_features': {
          'footwear_type': ['pumps'],
        },
        'excluded_features': {
          'footwear_type': ['sandals'],
        },
      }),
      ['обувь', 'Нужно: туфли', 'Без: сандалии'],
    );
  });

  testWidgets(
    'Empty verified results offer a separate catalogue without silently changing size rules',
    (tester) async {
      final controller = AppController(apiClient: TaskApi())
        ..activationStatus = {
          'recommendation_features': ['catalogue_discovery_v1'],
        };
      await controller.shoppingTask.submit('синие туфли');
      controller.shoppingTask.result = {
        ...response(),
        'text': 'синие туфли',
        'products': <Map<String, dynamic>>[],
        'constraints': {
          'requested_categories': ['обувь'],
          'colors': ['blue'],
          'required_features': {
            'footwear_type': ['pumps'],
          },
        },
        'message': 'Нет вещей с подтверждённым размером и всеми условиями.',
      };
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.dark,
          home: ShoppingTaskScreen(controller: controller),
        ),
      );
      expect(find.text('Нужно: туфли'), findsOneWidget);
      expect(
        find.text('Нет вещей с подтверждённым размером и всеми условиями.'),
        findsOneWidget,
      );
      final browse = find.byKey(
        const ValueKey('shopping-request-open-catalogue'),
      );
      await tester.ensureVisible(browse);
      await tester.tap(browse);
      await tester.pumpAndSettle();
      final catalogue = tester.widget<DiscoveryScreen>(
        find.byType(DiscoveryScreen),
      );
      expect(catalogue.initialQuery, 'синие туфли');
      expect(
        find.byKey(const ValueKey('catalogue-consent-accept')),
        findsOneWidget,
      );
      expect(controller.shoppingTask.result!['products'], isEmpty);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets(
    'Unrecognized requests separate conditions from the problem and edit the full request',
    (tester) async {
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      tester.view.padding = const FakeViewPadding(top: 24, bottom: 48);
      tester.view.viewPadding = const FakeViewPadding(top: 24, bottom: 48);
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      addTearDown(tester.view.resetPadding);
      addTearDown(tester.view.resetViewPadding);
      final api = ClarificationTaskApi();
      final controller = AppController(apiClient: api);
      await controller.shoppingTask.submit('синие туфли');
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.dark,
          home: ShoppingTaskScreen(controller: controller),
        ),
      );
      expect(find.text(ClarificationTaskApi.unsupported), findsOneWidget);
      expect(find.text(ClarificationTaskApi.missingItem), findsOneWidget);
      expect(find.textContaining('Уточните запрос:'), findsNothing);
      expect(
        find.byKey(const ValueKey('refine-shopping-request')),
        findsNothing,
      );
      final budget = find.text('Бюджет профиля: до 25000 ₽');
      final status = find.byKey(const ValueKey('shopping-request-status'));
      expect(
        tester.getRect(budget).bottom,
        lessThan(tester.getRect(status).top),
      );

      final edit = find.text('Изменить запрос');
      await tester.ensureVisible(edit);
      await tester.tap(edit);
      await tester.pumpAndSettle();
      expect(find.text('Нужно уточнение'), findsNothing);
      final request = find.byKey(const ValueKey('shopping-request'));
      expect(tester.widget<TextField>(request).controller!.text, 'синие туфли');
      await tester.enterText(request, 'Брюки в офис до 7000');
      await tester.ensureVisible(find.text('Найти подходящее'));
      await tester.tap(find.text('Найти подходящее'));
      await tester.pumpAndSettle();
      expect(api.texts, ['синие туфли', 'Брюки в офис до 7000']);
      expect(api.refinements, isEmpty);
      expect(find.text('Нужно уточнение'), findsNothing);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  for (final size in [const Size(390, 844), const Size(720, 400)]) {
    testWidgets(
      'Request correction remains reachable at $size with 200 percent text and keyboard',
      (tester) async {
        final landscape = size.width > size.height;
        tester.view.physicalSize = size;
        tester.view.devicePixelRatio = 1;
        tester.view.padding = FakeViewPadding(
          top: 24,
          right: landscape ? 48 : 0,
          bottom: landscape ? 0 : 48,
        );
        tester.view.viewPadding = tester.view.padding;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        addTearDown(tester.view.resetPadding);
        addTearDown(tester.view.resetViewPadding);
        addTearDown(tester.view.resetViewInsets);
        final controller = AppController(apiClient: ClarificationTaskApi());
        await controller.shoppingTask.submit('синие туфли');
        await tester.pumpWidget(
          MaterialApp(
            theme: AppTheme.dark,
            builder: (context, child) => MediaQuery(
              data: MediaQuery.of(
                context,
              ).copyWith(textScaler: TextScaler.linear(2)),
              child: child!,
            ),
            home: ShoppingTaskScreen(controller: controller),
          ),
        );
        final edit = find.text('Изменить запрос');
        await tester.scrollUntilVisible(
          edit,
          180,
          scrollable: find.byType(Scrollable).first,
        );
        await tester.ensureVisible(edit);
        await tester.pumpAndSettle();
        expect(tester.getRect(edit).bottom, lessThanOrEqualTo(size.height));
        expect(
          tester.getRect(edit).right,
          lessThanOrEqualTo(size.width - (landscape ? 48 : 0)),
        );
        await tester.tap(edit);
        await tester.pumpAndSettle();
        final request = find.byKey(const ValueKey('shopping-request'));
        await tester.enterText(request, 'синие туфли до 8000');
        tester.view.viewInsets = const FakeViewPadding(bottom: 160);
        await tester.pumpAndSettle();
        final submit = find.text('Найти подходящее');
        await tester.scrollUntilVisible(
          submit,
          120,
          scrollable: find.byType(Scrollable).first,
        );
        await tester.ensureVisible(submit);
        await tester.pumpAndSettle();
        expect(
          tester.getRect(submit).bottom,
          lessThanOrEqualTo(size.height - 160),
        );
        expect(tester.takeException(), isNull);
        await tester.pumpWidget(const SizedBox());
        controller.dispose();
      },
    );
  }

  test(
    'outfit views and actions retain the selected outfit without attributing every look',
    () async {
      final api = OutfitTaskApi();
      final task = ShoppingTaskController(api);
      await task.submit('Образ в офис');
      await task.recordVisibleProduct('top', outfitId: 'look-1');
      await task.recordVisibleProduct('top', outfitId: 'look-1');
      await task.recordVisibleProduct('top', outfitId: 'another-look');
      await task.react('top', 'like', outfitId: 'look-1');
      await task.recordOpenProduct('top', outfitId: 'look-1');
      expect(api.recordedEvents, ['view', 'view', 'like', 'open_product']);
      expect(api.recordedMeta.map((m) => m['intent_outfit_id']), [
        'look-1',
        'another-look',
        'look-1',
        'look-1',
      ]);
      expect(task.eventMetaFor(outfitId: 'look-1')['intent_id'], 'task-id');
      expect(task.eventMeta.containsKey('intent_outfit_id'), isFalse);
      task.dispose();
    },
  );

  test(
    'Completion is explicit and local results clear only after acknowledgement',
    () async {
      final api = TaskApi();
      final task = ShoppingTaskController(api);
      await task.submit('Брюки до 7000');
      expect(api.closures, isEmpty);
      expect(await task.close('completed'), isTrue);
      expect(api.closures, ['completed']);
      expect(task.result, isNull);
      task.dispose();
    },
  );

  testWidgets(
    'Outfit replacement and save fit above system navigation on a small phone',
    (tester) async {
      tester.view.physicalSize = const Size(360, 640);
      tester.view.devicePixelRatio = 1;
      tester.view.padding = const FakeViewPadding(top: 24, bottom: 48);
      tester.view.viewPadding = const FakeViewPadding(top: 24, bottom: 48);
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      addTearDown(tester.view.resetPadding);
      addTearDown(tester.view.resetViewPadding);
      final api = OutfitTaskApi();
      final controller = AppController(apiClient: api);
      await controller.shoppingTask.submit('Образ до 20000 без каблуков');
      await tester.pumpWidget(
        MaterialApp(home: ShoppingTaskScreen(controller: controller)),
      );
      await tester.scrollUntilVisible(
        find.text('Обувь'),
        300,
        scrollable: find.byType(Scrollable).first,
      );
      final replacement = find.byKey(
        const ValueKey('replace-outfit-look-1-shoes'),
      );
      await tester.ensureVisible(replacement);
      await tester.pumpAndSettle();
      await tester.tap(replacement);
      await tester.pumpAndSettle();
      expect(api.replacedSlot, 'shoes');
      expect(
        controller.shoppingTask.result!['outfits'][0]['products']['top']['id'],
        'top',
      );
      final save = find.text('Сохранить образ');
      await tester.ensureVisible(save);
      await tester.pumpAndSettle();
      expect(tester.getRect(save).bottom, lessThanOrEqualTo(592));
      await tester.tap(save);
      await tester.pumpAndSettle();
      expect(api.saves, 1);
      expect(find.text('Образ сохранён'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  test(
    'Failed create retries same key; new task ignores old response',
    () async {
      final api = TaskApi()..fail = true;
      final task = ShoppingTaskController(api);
      await task.submit('Брюки в офис до 7000');
      expect(task.error, isNotNull);
      api.fail = false;
      await task.submit('Брюки в офис до 7000');
      expect(api.keys[0], api.keys[1]);
      api.pending = Completer();
      final pending = task.submit('Платье до 8000');
      task.clear();
      api.pending!.complete(response());
      await pending;
      expect(task.result, isNull);
      expect(task.busy, isFalse);
      task.dispose();
    },
  );

  test(
    'Pending generation preserves the request key and retries once on user action',
    () async {
      final api = TaskApi()..createPending = true;
      final task = ShoppingTaskController(api);
      await task.submit('Брюки до 7000');
      expect(task.result, isNull);
      expect(task.busy, isFalse);
      expect(
        task.error,
        'Подборка ещё готовится. Подождите немного и повторите тот же запрос.',
      );
      expect(api.keys, hasLength(1));
      api.createPending = false;
      await task.submit('Брюки до 7000');
      expect(api.keys, hasLength(2));
      expect(api.keys[0], api.keys[1]);
      expect(task.error, isNull);
      expect(task.result!['id'], 'task-id');
      task.dispose();
    },
  );

  test(
    'Version conflict reloads task without applying refinement twice',
    () async {
      final api = TaskApi()..conflict = true;
      final task = ShoppingTaskController(api);
      await task.submit('Брюки в офис до 7000');
      await task.submit('Дешевле', refine: true);
      expect(task.result!['version'], 2);
      expect(task.result!['constraints']['budget_max'], 5600);
      expect(api.refinements, ['Дешевле']);
      expect(api.recordedEvents, isNot(contains('dislike')));
      task.dispose();
    },
  );

  test(
    'Explicit positive task actions retain context and suppress repeat taps',
    () async {
      final api = TaskApi();
      final task = ShoppingTaskController(api);
      await task.submit('Брюки до 7000');
      await task.react('p1', 'like');
      await task.react('p1', 'like');
      await task.react('p1', 'save');
      expect(api.recordedEvents, ['like', 'save']);
      expect(task.eventMeta['intent_id'], 'task-id');
      expect(task.hasReacted('p1', 'save'), isTrue);
      expect(task.result!['constraints']['budget_max'], 7000);
      task.clear();
      expect(task.hasReacted('p1', 'save'), isFalse);
      task.dispose();
    },
  );

  test(
    'Expired tasks clear stale results and can be recreated with a fresh key',
    () async {
      final api = TaskApi();
      final task = ShoppingTaskController(api);
      await task.submit('Брюки в офис до 7000');
      api.expired = true;
      await task.submit('Дешевле', refine: true);
      expect(task.result, isNull);
      expect(task.error, isNotNull);
      api.expired = false;
      await task.submit('Брюки в офис до 7000');
      expect(api.keys[0], isNot(api.keys[1]));
      task.result!['expires_at'] = DateTime.utc(2026, 1, 1).toIso8601String();
      expect(task.discardExpired(now: DateTime.utc(2026, 10, 8)), isTrue);
      expect(task.result, isNull);
      task.dispose();
    },
  );

  testWidgets(
    'Task screen shows honest constraints and refines without a dislike',
    (tester) async {
      final api = TaskApi();
      final controller = AppController(apiClient: api);
      await controller.shoppingTask.submit('Брюки в офис до 7000');
      await tester.pumpWidget(
        MaterialApp(home: ShoppingTaskScreen(controller: controller)),
      );
      expect(find.text('До 7000 ₽'), findsOneWidget);
      await tester.ensureVisible(find.text('Дешевле'));
      await tester.tap(find.text('Дешевле'));
      await tester.pumpAndSettle();
      expect(find.text('До 5600 ₽'), findsOneWidget);
      expect(api.recordedEvents, isNot(contains('dislike')));
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets(
    'Home advertises shopping requests only when server supports them',
    (tester) async {
      final controller = AppController(apiClient: TaskApi());
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(body: FeedScreen(controller: controller)),
        ),
      );
      expect(find.byKey(const ValueKey('open-shopping-intent')), findsNothing);
      controller.activationStatus = {
        'recommendation_features': ['shopping_intent_v1'],
      };
      controller.setTab(0);
      await tester.pump();
      expect(
        find.byKey(const ValueKey('open-shopping-intent')),
        findsOneWidget,
      );
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );
}
