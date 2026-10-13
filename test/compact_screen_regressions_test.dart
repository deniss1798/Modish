import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/theme/app_theme.dart';
import 'package:modish/features/products/models.dart';
import 'package:modish/features/shopping/shopping_task_screen.dart';

import 'app_regressions_test.dart' show FakeApi;
import 'shopping_task_test.dart' show TaskApi, OutfitTaskApi, response;
import 'support/load_app_fonts.dart';

class _PendingCloseApi extends TaskApi {
  final closeGate = Completer<void>();
  @override
  Future<void> closeShoppingIntent(String id, int version, String outcome) =>
      closeGate.future;
}

Future<void> _phone(
  WidgetTester tester,
  Widget screen, {
  double scale = 1,
}) async {
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
        ).copyWith(textScaler: TextScaler.linear(scale)),
        child: child!,
      ),
      home: screen,
    ),
  );
  await tester.pump();
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(loadAppFonts);
  testWidgets(
    'Shopping request error stays visible before examples at large text',
    (tester) async {
      const message =
          'Не удалось подключиться. Проверьте интернет и попробуйте снова.';
      final controller = AppController(apiClient: TaskApi());
      controller.shoppingTask.error = message;
      await _phone(
        tester,
        ShoppingTaskScreen(controller: controller),
        scale: 1.3,
      );
      final error = find.text(message);
      expect(error.hitTestable(), findsOneWidget);
      expect(tester.getRect(error).bottom, lessThanOrEqualTo(592));
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets(
    'Clarification exposes an input and refines the existing request',
    (tester) async {
      final api = TaskApi();
      final controller = AppController(apiClient: api);
      controller.shoppingTask.result = {
        ...response(),
        'text': 'Что-то красивое',
        'state': 'needs_clarification',
        'constraints': <String, dynamic>{},
        'products': <Map<String, dynamic>>[],
        'message': 'Уточните, какую вещь или образ вы ищете.',
      };
      await _phone(
        tester,
        ShoppingTaskScreen(controller: controller),
        scale: 1.3,
      );
      final input = find.byKey(const ValueKey('refine-shopping-request'));
      expect(input.hitTestable(), findsOneWidget);
      await tester.enterText(input, 'Платье до 8000');
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pumpAndSettle();
      expect(api.refinements, ['Платье до 8000']);
      expect(api.keys, isEmpty);
      expect(controller.shoppingTask.result!['id'], 'task-id');
      expect(controller.shoppingTask.result!['version'], 2);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets(
    'Empty feed stays readable above system navigation at 130% text',
    (tester) async {
      final controller = AppController(apiClient: FakeApi())
        ..activationStatus = {
          'recommendation_features': ['shopping_intent_v1'],
        };
      await _phone(tester, HomeScreen(controller: controller), scale: 1.3);
      expect(tester.takeException(), isNull);
      expect(find.text('Подходящих вещей пока нет'), findsOneWidget);
      final refresh = find.text('Обновить');
      expect(refresh.hitTestable(), findsOneWidget);
      expect(tester.getRect(refresh).bottom, lessThanOrEqualTo(592));
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets(
    'Short-phone feed prioritizes photo while retaining all actions',
    (tester) async {
      final controller = AppController(apiClient: FakeApi())
        ..activationStatus = {
          'recommendation_features': ['shopping_intent_v1'],
        }
        ..productFeed = [
          FeedCard(
            product: Product.fromApi({
              'id': 'compact-product',
              'title': 'Блузка из воздушного хлопка с объёмными рукавами',
              'brand': 'BAON',
              'price': 4599,
              'image_url': '',
            }),
            reason: '',
            reasons: ['Цена в вашем бюджете'],
          ),
        ];
      await _phone(tester, HomeScreen(controller: controller));
      expect(
        tester.getSize(find.byKey(const ValueKey('feed-product-photo'))).height,
        greaterThanOrEqualTo(192),
      );
      for (final action in [
        'Пропуск',
        'Не моё',
        'Сохранить',
        'Детали',
        'Нравится',
      ]) {
        final button = find.byTooltip(action);
        expect(button.hitTestable(), findsOneWidget);
        expect(tester.getRect(button).height, greaterThanOrEqualTo(48));
        expect(tester.getRect(button).bottom, lessThanOrEqualTo(592));
      }
      expect(find.byTooltip('Ваш запрос').hitTestable(), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets(
    'Compact task summary preserves conditions through editing and refinement',
    (tester) async {
      final api = TaskApi();
      final controller = AppController(apiClient: api);
      await controller.shoppingTask.submit('Брюки в офис до 7000');
      await _phone(
        tester,
        ShoppingTaskScreen(controller: controller),
        scale: 1.3,
      );
      expect(find.byKey(const ValueKey('shopping-request')), findsNothing);
      expect(
        find.byKey(const ValueKey('refine-shopping-request')),
        findsNothing,
      );
      expect(find.text('До 7000 ₽'), findsOneWidget);
      expect(find.text('1 подходящая вещь').hitTestable(), findsOneWidget);
      expect(find.text('Брюки прямые').hitTestable(), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('edit-shopping-request')));
      await tester.pump();
      await tester.enterText(
        find.byKey(const ValueKey('shopping-request')),
        'Платье до 8000',
      );
      await tester.scrollUntilVisible(
        find.text('Отменить изменение'),
        180,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.tap(find.text('Отменить изменение'));
      await tester.pump();
      expect(
        controller.shoppingTask.result!['constraints']['budget_max'],
        7000,
      );
      expect(api.keys, hasLength(1));
      await tester.ensureVisible(
        find.byKey(const ValueKey('expand-shopping-refinement')),
      );
      await tester.tap(
        find.byKey(const ValueKey('expand-shopping-refinement')),
      );
      await tester.pump();
      await tester.enterText(
        find.byKey(const ValueKey('refine-shopping-request')),
        'Дешевле',
      );
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pumpAndSettle();
      expect(api.refinements, ['Дешевле']);
      expect(
        controller.shoppingTask.result!['constraints']['budget_max'],
        5600,
      );
      expect(
        controller.shoppingTask.result!['constraints']['occasion'],
        'office',
      );
      expect(
        find.byKey(const ValueKey('refine-shopping-request')),
        findsNothing,
      );
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets(
    'Wide outfit action row keeps explicit reaction attribution at large text',
    (tester) async {
      final api = OutfitTaskApi();
      final controller = AppController(apiClient: api);
      await controller.shoppingTask.submit('Образ до 20000 без каблуков');
      await _phone(
        tester,
        ShoppingTaskScreen(controller: controller),
        scale: 1.3,
      );
      final like = find.byKey(const ValueKey('outfit-look-1-top-like'));
      await tester.ensureVisible(like);
      await tester.pump();
      await tester.tap(like);
      await tester.pumpAndSettle();
      expect(api.recordedEvents, contains('like'));
      final metadata = api.recordedMeta.lastWhere(
        (meta) => meta['intent_outfit_id'] == 'look-1',
      );
      expect(metadata['intent_id'], 'task-id');
      expect(
        find.byKey(const ValueKey('replace-outfit-look-1-top')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('outfit-look-1-top-feedback')),
        findsOneWidget,
      );
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );
  testWidgets(
    'Closing a task after leaving its screen never edits disposed fields',
    (tester) async {
      final api = _PendingCloseApi();
      final controller = AppController(apiClient: api);
      await controller.shoppingTask.submit('Брюки в офис до 7000');
      await _phone(tester, ShoppingTaskScreen(controller: controller));
      await tester.tap(find.text('Готово'));
      await tester.pump();
      await tester.pumpWidget(const SizedBox());
      api.closeGate.complete();
      await tester.pump();
      expect(tester.takeException(), isNull);
      expect(controller.shoppingTask.result, isNull);
      controller.dispose();
    },
  );
}
