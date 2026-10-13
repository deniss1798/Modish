import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/widgets/modish_widgets.dart';
import 'package:modish/features/catalog/brands_screen.dart';
import 'package:modish/features/onboarding/result_screen.dart';
import 'package:modish/features/outfits/outfit_detail_screen.dart';
import 'package:modish/features/settings/settings_screen.dart';

import 'app_regressions_test.dart' show FakeApi;

class _ScreenApi extends FakeApi {
  final savedOutfitIds = <String>[];
  bool brandsEmpty = false;
  bool briefFails = false;

  @override
  Future<void> outfitsSave(String outfitId) async {
    savedOutfitIds.add(outfitId);
  }

  @override
  Future<List<Map<String, dynamic>>> productsBrands({int limit = 80}) async =>
      brandsEmpty
      ? []
      : [
          for (var index = 0; index < 24; index++)
            {'brand': 'Brand $index', 'count': index + 1},
        ];

  @override
  Future<Map<String, dynamic>> profileBrief() async {
    if (briefFails) throw StateError('Offline fixture');
    return {'stats': <String, dynamic>{}};
  }
}

const _outfit = {
  'id': 'safe-area-look',
  'style_direction': 'daily',
  'total_price': 12000,
  'products': {
    'top': {'id': 'top', 'title': 'Хлопковая блузка', 'price': 4000},
    'bottom': {'id': 'bottom', 'title': 'Брюки прямого кроя', 'price': 4000},
    'shoes': {'id': 'shoes', 'title': 'Лоферы', 'price': 4000},
  },
};

Future<void> _pumpScreen(
  WidgetTester tester,
  Widget screen, {
  double textScale = 1,
}) async {
  tester.view.physicalSize = const Size(360, 640);
  tester.view.devicePixelRatio = 1;
  tester.view.padding = const FakeViewPadding(top: 24, bottom: 48, left: 12);
  tester.view.viewPadding = const FakeViewPadding(
    top: 24,
    bottom: 48,
    left: 12,
  );
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPadding);
  addTearDown(tester.view.resetViewPadding);
  await tester.pumpWidget(
    MaterialApp(
      builder: (context, child) => MediaQuery(
        data: MediaQuery.of(
          context,
        ).copyWith(textScaler: TextScaler.linear(textScale)),
        child: child!,
      ),
      home: screen,
    ),
  );
  await tester.pumpAndSettle();
}

Future<void> _scrollToBottom(WidgetTester tester) async {
  final scrollable = find.byType(Scrollable).hitTestable().first;
  for (var index = 0; index < 10; index++) {
    await tester.drag(scrollable, const Offset(0, -400));
    await tester.pumpAndSettle();
  }
}

void _expectAboveSystemNavigation(WidgetTester tester, Finder target) {
  expect(target.hitTestable(), findsOneWidget);
  final bounds = tester.getRect(target);
  expect(bounds.bottom, lessThanOrEqualTo(592));
  expect(bounds.left, greaterThanOrEqualTo(12));
  expect(bounds.right, lessThanOrEqualTo(360));
  expect(tester.takeException(), isNull);
}

void main() {
  for (final textScale in [1.0, 1.3]) {
    testWidgets(
      'Outfit Save is tappable above a 48px system inset at text scale $textScale',
      (tester) async {
        final api = _ScreenApi();
        final controller = AppController(apiClient: api);
        addTearDown(controller.dispose);
        await _pumpScreen(
          tester,
          OutfitDetailScreen(controller: controller, outfit: _outfit),
          textScale: textScale,
        );
        await _scrollToBottom(tester);
        final save = find.widgetWithText(SecondaryButton, 'Сохранить образ');
        _expectAboveSystemNavigation(tester, save);
        await tester.tap(save);
        await tester.pumpAndSettle();
        expect(api.savedOutfitIds, ['safe-area-look']);
      },
    );

    testWidgets(
      'Settings last action clears the navigation inset at text scale $textScale',
      (tester) async {
        final controller = AppController(apiClient: _ScreenApi())
          ..email = 'anna@example.test';
        addTearDown(controller.dispose);
        await _pumpScreen(
          tester,
          SettingsScreen(controller: controller),
          textScale: textScale,
        );
        await _scrollToBottom(tester);
        _expectAboveSystemNavigation(
          tester,
          find.widgetWithText(PrimaryButton, 'Выйти из аккаунта'),
        );
        final help = find.text('Как пользоваться Modish');
        await tester.ensureVisible(help);
        await tester.pumpAndSettle();
        await tester.tap(help);
        await tester.pumpAndSettle();
        expect(find.byType(HelpScreen), findsOneWidget);
        expect(tester.takeException(), isNull);
      },
    );

    testWidgets(
      'Help final paragraph clears the navigation inset at text scale $textScale',
      (tester) async {
        await _pumpScreen(tester, const HelpScreen(), textScale: textScale);
        await _scrollToBottom(tester);
        _expectAboveSystemNavigation(
          tester,
          find.textContaining('Если магазин удалил фото или товар'),
        );
      },
    );

    testWidgets(
      'Brands final row clears the navigation inset at text scale $textScale',
      (tester) async {
        final controller = AppController(apiClient: _ScreenApi());
        addTearDown(controller.dispose);
        await _pumpScreen(
          tester,
          BrandsScreen(controller: controller),
          textScale: textScale,
        );
        await _scrollToBottom(tester);
        _expectAboveSystemNavigation(
          tester,
          find.widgetWithText(ListTile, 'Brand 23'),
        );
      },
    );
  }

  testWidgets('Empty brands offers a safe, actionable retry', (tester) async {
    final api = _ScreenApi()..brandsEmpty = true;
    final controller = AppController(apiClient: api);
    addTearDown(controller.dispose);
    await _pumpScreen(tester, BrandsScreen(controller: controller));
    _expectAboveSystemNavigation(
      tester,
      find.widgetWithText(TextButton, 'Повторить'),
    );
    api.brandsEmpty = false;
    await tester.tap(find.text('Повторить'));
    await tester.pumpAndSettle();
    expect(find.text('Brand 0'), findsOneWidget);
    expect(find.text('Бренды не найдены в каталоге'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  for (final briefFails in [false, true]) {
    testWidgets(
      'Photo result does not invent completed outfits when brief fails=$briefFails',
      (tester) async {
        final api = _ScreenApi()..briefFails = briefFails;
        final controller = AppController(apiClient: api);
        addTearDown(controller.dispose);
        await _pumpScreen(
          tester,
          ResultScreen(controller: controller),
          textScale: 1.3,
        );
        expect(controller.outfits, isEmpty);
        expect(find.textContaining('мы собрали 3 комплекта'), findsNothing);
        expect(
          find.textContaining('Их наличие зависит от размеров'),
          findsOneWidget,
        );
        await _scrollToBottom(tester);
        _expectAboveSystemNavigation(
          tester,
          find.widgetWithText(PrimaryButton, 'Перейти к образам'),
        );
      },
    );
  }
}
