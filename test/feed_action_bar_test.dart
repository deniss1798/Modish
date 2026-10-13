import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/core/theme/app_theme.dart';
import 'package:modish/core/widgets/modish_widgets.dart';

void main() {
  for (final compact in [false, true]) {
    testWidgets(
      'Feed actions remain labelled, tappable and unclipped compact=$compact',
      (tester) async {
        tester.view.physicalSize = const Size(360, 640);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        final actions = <String>[];
        await tester.pumpWidget(
          MaterialApp(
            theme: AppTheme.dark,
            home: MediaQuery(
              data: const MediaQueryData(
                size: Size(360, 640),
                textScaler: TextScaler.linear(1.3),
              ),
              child: Scaffold(
                body: Center(
                  child: FeedActionBar(
                    compact: compact,
                    onSkip: () => actions.add('skip'),
                    onDislike: () => actions.add('dislike'),
                    onSave: () => actions.add('save'),
                    onOpen: () => actions.add('open'),
                    onLike: () => actions.add('like'),
                  ),
                ),
              ),
            ),
          ),
        );
        for (final label in [
          'Пропуск',
          'Не моё',
          'Сохранить',
          'Детали',
          'Нравится',
        ]) {
          final action = find.byTooltip(label);
          expect(action, findsOneWidget);
          final size = tester.getSize(action);
          expect(size.width, greaterThanOrEqualTo(48));
          expect(size.height, greaterThanOrEqualTo(48));
          await tester.tap(action);
          await tester.pumpAndSettle();
        }
        expect(actions, ['skip', 'dislike', 'save', 'open', 'like']);
        expect(tester.takeException(), isNull);
      },
    );
  }
}
