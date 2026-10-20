import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/theme/app_theme.dart';
import 'package:modish/core/widgets/modish_widgets.dart';
import 'package:modish/features/auth/auth_screen.dart';
import 'package:modish/features/onboarding/welcome_screen.dart';

import 'app_regressions_test.dart' show FakeApi;
import 'support/load_app_fonts.dart';

const _captureDirectory = String.fromEnvironment('WELCOME_PREVIEW_OUTPUT');

typedef _Scenario = ({Size size, double scale, double keyboard});

void _setView(WidgetTester tester, _Scenario scenario) {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = scenario.size;
  final landscape = scenario.size.width > scenario.size.height;
  final insets = FakeViewPadding(
    top: 24,
    right: landscape ? 48 : 0,
    bottom: landscape ? 0 : 48,
  );
  tester.view.padding = insets;
  tester.view.viewPadding = insets;
  tester.view.viewInsets = FakeViewPadding(bottom: scenario.keyboard);
}

Widget _app(AppController controller, GlobalKey captureKey, double scale) =>
    RepaintBoundary(
      key: captureKey,
      child: AppScope(
        notifier: controller,
        child: MaterialApp(
          debugShowCheckedModeBanner: false,
          theme: AppTheme.dark,
          builder: (context, child) => MediaQuery(
            data: MediaQuery.of(
              context,
            ).copyWith(textScaler: TextScaler.linear(scale)),
            child: child!,
          ),
          home: const RootScreen(),
        ),
      ),
    );

Future<void> _decodeAssets(WidgetTester tester) async {
  await tester.runAsync(() async {
    await loadAppFonts();
    final context = tester.element(find.byType(WelcomeScreen));
    for (final asset in ['assets/images/back.png', 'assets/images/logo.png']) {
      await precacheImage(
        AssetImage(asset),
        context,
      ).timeout(const Duration(seconds: 5));
    }
  });
  await tester.pumpAndSettle();
}

Future<void> _capture(
  WidgetTester tester,
  GlobalKey key,
  String filename,
) async {
  if (_captureDirectory.isEmpty) return;
  await tester.runAsync(() async {
    final image =
        await (key.currentContext!.findRenderObject() as RenderRepaintBoundary)
            .toImage(pixelRatio: 1);
    try {
      final data = await image.toByteData(format: ui.ImageByteFormat.png);
      final file = File('$_captureDirectory/$filename.png');
      await file.parent.create(recursive: true);
      await file.writeAsBytes(data!.buffer.asUint8List());
    } finally {
      image.dispose();
    }
  });
}

Future<void> _checkNavigation(
  WidgetTester tester,
  AppController controller,
  _Scenario scenario,
) async {
  for (final action in [
    (label: 'Войти', registration: false, type: FilledButton),
    (label: 'Создать аккаунт', registration: true, type: OutlinedButton),
  ]) {
    controller.goToWelcome();
    await tester.pumpAndSettle();
    final button = find.ancestor(
      of: find.text(action.label),
      matching: find.byType(action.type),
    );
    await tester.ensureVisible(button);
    await tester.pumpAndSettle();
    expect(button.hitTestable(), findsOneWidget);
    final rectangle = tester.getRect(button);
    final rightInset = scenario.size.width > scenario.size.height ? 48 : 0;
    expect(rectangle.left, greaterThanOrEqualTo(0));
    expect(
      rectangle.right,
      lessThanOrEqualTo(scenario.size.width - rightInset),
    );
    expect(rectangle.top, greaterThanOrEqualTo(24));
    expect(
      rectangle.bottom,
      lessThanOrEqualTo(scenario.size.height - scenario.keyboard),
    );
    await tester.tap(button);
    await tester.pumpAndSettle();
    expect(controller.stage, AppStage.auth);
    expect(controller.authRegisterMode, action.registration);
    expect(find.byType(AuthScreen), findsOneWidget);
    expect(tester.takeException(), isNull);
  }
}

void main() {
  const scenarios = <_Scenario>[
    (size: Size(844, 390), scale: 1, keyboard: 0),
    (size: Size(568, 320), scale: 1, keyboard: 0),
    (size: Size(360, 800), scale: 1, keyboard: 0),
    (size: Size(844, 390), scale: 1.3, keyboard: 0),
    (size: Size(568, 320), scale: 1.3, keyboard: 0),
    (size: Size(360, 800), scale: 1.3, keyboard: 0),
    (size: Size(844, 390), scale: 2, keyboard: 0),
    (size: Size(568, 320), scale: 2, keyboard: 0),
    (size: Size(360, 800), scale: 2, keyboard: 0),
    (size: Size(568, 320), scale: 2, keyboard: 140),
    (size: Size(360, 800), scale: 2, keyboard: 350),
    (size: Size(480, 320), scale: 2, keyboard: 0),
  ];

  for (final scenario in scenarios) {
    final name =
        '${scenario.size.width.toInt()}x${scenario.size.height.toInt()}'
        '-text${(scenario.scale * 100).round()}'
        '${scenario.keyboard > 0 ? '-keyboard' : ''}';
    testWidgets(
      'Welcome preserves brand, safe actions and navigation at $name',
      (tester) async {
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        addTearDown(tester.view.resetPadding);
        addTearDown(tester.view.resetViewPadding);
        addTearDown(tester.view.resetViewInsets);
        _setView(tester, scenario);
        final controller = AppController(apiClient: FakeApi())
          ..stage = AppStage.welcome;
        addTearDown(controller.dispose);
        final captureKey = GlobalKey();
        await tester.pumpWidget(_app(controller, captureKey, scenario.scale));
        await _decodeAssets(tester);
        expect(tester.takeException(), isNull);
        expect(find.byType(HeroFashionBackdrop), findsOneWidget);
        if (scenario.size.width > scenario.size.height) {
          expect(find.byType(Brand), findsOneWidget);
          expect(find.text('YOUR STYLE\nSMARTER'), findsOneWidget);
          final brand = tester.getRect(
            find.byKey(const ValueKey('welcome-landscape-brand')),
          );
          final login = tester.getRect(find.byType(PrimaryButton));
          expect(brand.overlaps(login), isFalse);
          expect(brand.left, greaterThanOrEqualTo(0));
          expect(brand.right, lessThanOrEqualTo(scenario.size.width - 48));
        } else {
          // Portrait retains the supplied photograph's embedded brand.
          expect(find.byType(Brand), findsNothing);
        }
        await _capture(tester, captureKey, name);
        await _checkNavigation(tester, controller, scenario);
        await tester.pumpWidget(const SizedBox());
      },
    );
  }

  testWidgets('rotating Welcome keeps controller and both auth routes usable', (
    tester,
  ) async {
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.view.resetPadding);
    addTearDown(tester.view.resetViewPadding);
    addTearDown(tester.view.resetViewInsets);
    final controller = AppController(apiClient: FakeApi())
      ..stage = AppStage.welcome
      ..email = 'retained@example.test';
    addTearDown(controller.dispose);
    final captureKey = GlobalKey();
    final portrait = scenarios[2];
    _setView(tester, portrait);
    await tester.pumpWidget(_app(controller, captureKey, 1.3));
    await _decodeAssets(tester);
    final originalWelcome = tester.element(find.byType(WelcomeScreen));
    for (final scenario in [scenarios[3], portrait]) {
      _setView(tester, scenario);
      await tester.pumpAndSettle();
      expect(tester.element(find.byType(WelcomeScreen)), same(originalWelcome));
      expect(controller.email, 'retained@example.test');
      expect(tester.takeException(), isNull);
    }
    _setView(tester, scenarios[3]);
    await tester.pumpAndSettle();
    await _checkNavigation(tester, controller, scenarios[3]);
    await tester.pumpWidget(const SizedBox());
  });
}
