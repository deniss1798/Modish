import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/widgets/modish_widgets.dart';
import 'package:modish/features/outfits/outfit_detail_screen.dart';
import 'package:modish/features/products/product_detail_screen.dart';

import 'app_regressions_test.dart' show FakeApi;

const _lookId = 'outfit-action-look';
const _merchantRetry =
    'Не удалось открыть магазины. Попробуйте ещё раз или откройте вещи по отдельности.';

Map<String, dynamic> _outfit(bool saved) => {
  'id': _lookId,
  'is_saved': saved,
  'style_direction': 'daily',
  'reason': 'Сочетание вещей для прогулки',
  'total_price': 3000,
  'products': {
    'top': {'id': 'blouse', 'title': 'Хлопковая блузка', 'price': 3000},
  },
};

DioException _serverFailure(String path) {
  final request = RequestOptions(path: path);
  return DioException(
    requestOptions: request,
    type: DioExceptionType.badResponse,
    response: Response(
      requestOptions: request,
      statusCode: 500,
      data: {'detail': 'SQL IntegrityError: private_internal_trace'},
    ),
  );
}

class _OutfitActionApi extends FakeApi {
  bool saved = false;
  bool failSave = false;
  bool failUnsave = false;
  bool failLists = false;
  int saveCalls = 0;
  int unsaveCalls = 0;
  int merchantCalls = 0;
  Completer<void>? saveGate;
  Completer<void>? unsaveGate;

  @override
  Future<void> outfitsSave(String outfitId) async {
    expect(outfitId, _lookId);
    saveCalls++;
    await saveGate?.future;
    if (failSave) throw _serverFailure('/outfits/save');
    saved = true;
  }

  @override
  Future<void> outfitsUnsave(String outfitId) async {
    expect(outfitId, _lookId);
    unsaveCalls++;
    await unsaveGate?.future;
    if (failUnsave) throw _serverFailure('/outfits/unsave');
    saved = false;
  }

  @override
  Future<List<Map<String, dynamic>>> outfitsList({
    String? scenario,
    bool savedOnly = false,
  }) async {
    if (failLists) throw _serverFailure('/outfits');
    return savedOnly && !saved ? [] : [_outfit(saved)];
  }

  @override
  Future<Map<String, dynamic>> productById(String id) async => {
    'id': id,
    'title': 'Хлопковая блузка',
    'category': 'рубашки',
    'price': 3000,
    'image_url': '',
    'available_sizes': ['M'],
  };

  @override
  Future<Map<String, dynamic>> affiliateClick(String productId) async {
    expect(productId, 'blouse');
    merchantCalls++;
    throw _serverFailure('/affiliate/click');
  }
}

Future<AppController> _showDetail(
  WidgetTester tester,
  _OutfitActionApi api, {
  bool saved = false,
  bool emptyProducts = false,
}) async {
  api.saved = saved;
  final controller = AppController(apiClient: api);
  addTearDown(controller.dispose);
  addTearDown(api.dispose);
  final outfit = _outfit(saved);
  if (emptyProducts) outfit['products'] = <String, dynamic>{};
  await tester.pumpWidget(
    MaterialApp(
      home: Builder(
        builder: (context) => Scaffold(
          body: TextButton(
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute<void>(
                builder: (_) =>
                    OutfitDetailScreen(outfit: outfit, controller: controller),
              ),
            ),
            child: const Text('Открыть образ'),
          ),
        ),
      ),
    ),
  );
  await tester.tap(find.text('Открыть образ'));
  await tester.pumpAndSettle();
  return controller;
}

void _expectSafeError(AppController controller) {
  expect(controller.error, isNotNull);
  expect(find.text(controller.error!), findsOneWidget);
  expect(controller.error, isNot(contains('SQL')));
  expect(controller.error, isNot(contains('private_internal_trace')));
  expect(
    controller.error,
    matches(RegExp('Попробуйте|Повторите|Проверьте', caseSensitive: false)),
  );
}

void main() {
  testWidgets('Successful save changes the action and unsave closes detail', (
    tester,
  ) async {
    final api = _OutfitActionApi();
    final controller = await _showDetail(tester, api);
    await tester.tap(find.byTooltip('Сохранить образ'));
    await tester.pumpAndSettle();
    expect(api.saveCalls, 1);
    expect(api.saved, isTrue);
    expect(controller.savedOutfits.single['id'], _lookId);
    expect(find.byType(OutfitDetailScreen), findsOneWidget);
    expect(find.byTooltip('Убрать из сохранённого'), findsOneWidget);
    expect(find.text('Образ сохранён'), findsOneWidget);

    await tester.tap(find.byTooltip('Убрать из сохранённого'));
    await tester.pumpAndSettle();
    expect(api.unsaveCalls, 1);
    expect(api.saved, isFalse);
    expect(controller.savedOutfits, isEmpty);
    expect(find.byType(OutfitDetailScreen), findsNothing);
    expect(find.text('Открыть образ'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('Failed save stays in detail, reports the error and can retry', (
    tester,
  ) async {
    final api = _OutfitActionApi()..failSave = true;
    final controller = await _showDetail(tester, api);
    await tester.tap(find.byTooltip('Сохранить образ'));
    await tester.pumpAndSettle();
    expect(api.saved, isFalse);
    expect(find.byType(OutfitDetailScreen), findsOneWidget);
    expect(find.byTooltip('Сохранить образ'), findsOneWidget);
    _expectSafeError(controller);

    api.failSave = false;
    await tester.tap(find.byTooltip('Сохранить образ'));
    await tester.pumpAndSettle();
    expect(api.saveCalls, 2);
    expect(controller.error, isNull);
    expect(find.byTooltip('Убрать из сохранённого'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'Failed unsave retains the saved detail until a successful retry',
    (tester) async {
      final api = _OutfitActionApi()..failUnsave = true;
      final controller = await _showDetail(tester, api, saved: true);
      await tester.tap(find.byTooltip('Убрать из сохранённого'));
      await tester.pumpAndSettle();
      expect(api.unsaveCalls, 1);
      expect(api.saved, isTrue);
      expect(find.byType(OutfitDetailScreen), findsOneWidget);
      expect(find.byTooltip('Убрать из сохранённого'), findsOneWidget);
      _expectSafeError(controller);

      api.failUnsave = false;
      await tester.tap(find.byTooltip('Убрать из сохранённого'));
      await tester.pumpAndSettle();
      expect(api.unsaveCalls, 2);
      expect(api.saved, isFalse);
      expect(controller.error, isNull);
      expect(find.byType(OutfitDetailScreen), findsNothing);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('Pending save disables both actions and does not submit twice', (
    tester,
  ) async {
    final api = _OutfitActionApi()..saveGate = Completer<void>();
    await _showDetail(tester, api);
    await tester.tap(find.byTooltip('Сохранить образ'));
    await tester.pump();
    final icon = tester.widget<IconButton>(
      find.widgetWithIcon(IconButton, Icons.bookmark_border),
    );
    expect(icon.onPressed, isNull);
    expect(
      tester.widget<SecondaryButton>(find.byType(SecondaryButton)).onPressed,
      isNull,
    );
    await tester.tap(find.byTooltip('Сохранить образ'));
    await tester.pump();
    expect(api.saveCalls, 1);
    api.saveGate!.complete();
    await tester.pumpAndSettle();
    expect(find.byTooltip('Убрать из сохранённого'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('Confirmed save survives a failed collection refresh', (
    tester,
  ) async {
    final api = _OutfitActionApi()..failLists = true;
    await _showDetail(tester, api);
    await tester.tap(find.byTooltip('Сохранить образ'));
    await tester.pumpAndSettle();
    expect(api.saveCalls, 1);
    expect(api.saved, isTrue);
    expect(find.byType(OutfitDetailScreen), findsOneWidget);
    expect(find.byTooltip('Убрать из сохранённого'), findsOneWidget);
    expect(
      find.text(
        'Образ сохранён, но список не удалось обновить. Проверьте интернет и откройте «Сохранённое» снова.',
      ),
      findsOneWidget,
    );
    expect(find.textContaining('private_internal_trace'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('Confirmed unsave survives a failed collection refresh', (
    tester,
  ) async {
    final api = _OutfitActionApi()..failLists = true;
    await _showDetail(tester, api, saved: true);
    await tester.tap(find.byTooltip('Убрать из сохранённого'));
    await tester.pumpAndSettle();
    expect(api.unsaveCalls, 1);
    expect(api.saved, isFalse);
    expect(find.byType(OutfitDetailScreen), findsNothing);
    expect(find.text('Открыть образ'), findsOneWidget);
    expect(
      find.text(
        'Образ убран из сохранённого, но список не удалось обновить. Проверьте интернет и откройте «Сохранённое» снова.',
      ),
      findsOneWidget,
    );
    expect(find.textContaining('private_internal_trace'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'Completing pending unsave does not close a newly opened product',
    (tester) async {
      final api = _OutfitActionApi()..unsaveGate = Completer<void>();
      await _showDetail(tester, api, saved: true);
      await tester.tap(find.byTooltip('Убрать из сохранённого'));
      await tester.pump();
      expect(api.unsaveCalls, 1);
      await tester.tap(find.text('Хлопковая блузка'));
      await tester.pumpAndSettle();
      expect(find.byType(ProductDetailScreen), findsOneWidget);
      api.unsaveGate!.complete();
      await tester.pumpAndSettle();
      expect(api.saved, isFalse);
      expect(find.byType(ProductDetailScreen), findsOneWidget);
      expect(find.text('Образ убран из сохранённого'), findsNothing);
      tester.state<NavigatorState>(find.byType(Navigator)).pop();
      await tester.pumpAndSettle();
      expect(find.byType(OutfitDetailScreen), findsOneWidget);
      expect(find.byTooltip('Сохранить образ'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'Failed merchant links show the next step without backend details',
    (tester) async {
      final api = _OutfitActionApi();
      await _showDetail(tester, api);
      await tester.tap(find.widgetWithText(PrimaryButton, 'Купить всё'));
      await tester.pumpAndSettle();
      expect(api.merchantCalls, 1);
      expect(find.text(_merchantRetry), findsOneWidget);
      expect(find.textContaining('private_internal_trace'), findsNothing);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('An outfit without links shows a useful next step', (
    tester,
  ) async {
    final api = _OutfitActionApi();
    await _showDetail(tester, api, emptyProducts: true);
    await tester.tap(find.widgetWithText(PrimaryButton, 'Купить всё'));
    await tester.pumpAndSettle();
    expect(api.merchantCalls, 0);
    expect(find.text(_merchantRetry), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
