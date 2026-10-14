import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/theme/app_colors.dart';
import 'package:modish/core/theme/app_theme.dart';
import 'package:modish/features/onboarding/welcome_screen.dart';
import 'package:modish/features/subscription/plus_screen.dart';

AppController _controller() {
  final controller = AppController()
    ..stage = AppStage.welcome
    ..billing = {'status': 'trial', 'price': 129, 'unlimited': true};
  addTearDown(controller.dispose);
  return controller;
}

Future<void> _openPushedScreen(
  WidgetTester tester,
  AppController controller,
) async {
  await tester.pumpWidget(
    MaterialApp(
      theme: AppTheme.dark,
      home: Builder(
        builder: (context) => Scaffold(
          body: TextButton(
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute<void>(
                builder: (_) => PlusScreen(controller: controller),
              ),
            ),
            child: const Text('Исходный экран'),
          ),
        ),
      ),
    ),
  );
  await tester.tap(find.text('Исходный экран'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('Unavailable screen makes no price or feature promises', (
    tester,
  ) async {
    final controller = _controller();
    await _openPushedScreen(tester, controller);
    expect(find.text('Дополнительные возможности недоступны'), findsOneWidget);
    expect(
      find.text(
        'Вернитесь на предыдущий экран, чтобы продолжить пользоваться приложением.',
      ),
      findsOneWidget,
    );
    final visibleText = tester
        .widgetList<Text>(find.byType(Text))
        .map((text) => text.data ?? text.textSpan?.toPlainText() ?? '')
        .join('\n');
    expect(
      RegExp(
        r'129|₽|руб|цен[ауы]|оплат|оформ|подпис|без ограничений|неогранич|приоритет|в месяц|unlimited|subscribe|payment|price',
        caseSensitive: false,
      ).hasMatch(visibleText),
      isFalse,
    );
    expect(find.byType(SnackBar), findsNothing);
    final scaffold = tester.widget<Scaffold>(
      find.descendant(
        of: find.byType(PlusScreen),
        matching: find.byType(Scaffold),
      ),
    );
    expect(scaffold.backgroundColor, AppColors.bg);
    expect(tester.takeException(), isNull);
  });

  for (final closeWithIcon in [false, true]) {
    testWidgets('Return ${closeWithIcon ? 'icon' : 'button'} pops the route', (
      tester,
    ) async {
      final controller = _controller();
      await _openPushedScreen(tester, controller);
      await tester.tap(
        closeWithIcon ? find.byTooltip('Вернуться') : find.text('Вернуться'),
      );
      await tester.pumpAndSettle();
      expect(find.byType(PlusScreen), findsNothing);
      expect(find.text('Исходный экран'), findsOneWidget);
      expect(controller.stage, AppStage.welcome);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('Direct route returns through the current app stage', (
    tester,
  ) async {
    final controller = _controller();
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.dark,
        home: PlusScreen(controller: controller),
      ),
    );
    await tester.tap(find.text('Вернуться'));
    await tester.pumpAndSettle();
    expect(find.byType(PlusScreen), findsNothing);
    expect(find.byType(WelcomeScreen), findsOneWidget);
    expect(controller.stage, AppStage.welcome);
    expect(find.text('Войти'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('Small phone keeps the return action above Android navigation', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(360, 640);
    tester.view.devicePixelRatio = 1;
    tester.view.padding = const FakeViewPadding(top: 24, bottom: 48);
    tester.view.viewPadding = const FakeViewPadding(top: 24, bottom: 48);
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.view.resetPadding);
    addTearDown(tester.view.resetViewPadding);
    final controller = _controller();
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.dark,
        builder: (context, child) => MediaQuery(
          data: MediaQuery.of(
            context,
          ).copyWith(textScaler: const TextScaler.linear(1.5)),
          child: child!,
        ),
        home: PlusScreen(controller: controller),
      ),
    );
    await tester.pumpAndSettle();
    final button = find.widgetWithText(FilledButton, 'Вернуться');
    await tester.ensureVisible(button);
    expect(tester.getRect(button).bottom, lessThanOrEqualTo(592));
    expect(tester.takeException(), isNull);
  });
}
