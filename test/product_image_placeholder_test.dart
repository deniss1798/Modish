import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/core/theme/app_theme.dart';
import 'package:modish/core/widgets/product_image.dart';

void main() {
  for (final size in [
    const Size(76, 96),
    const Size(64, 64),
    const Size(24, 24),
  ]) {
    testWidgets('Missing image fits a $size thumbnail at 200 percent text', (
      tester,
    ) async {
      final semantics = tester.ensureSemantics();
      var opened = 0;
      var unavailable = 0;
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.dark,
          builder: (context, child) => MediaQuery(
            data: MediaQuery.of(
              context,
            ).copyWith(textScaler: const TextScaler.linear(2)),
            child: child!,
          ),
          home: Scaffold(
            body: Center(
              child: InkWell(
                onTap: () => opened++,
                child: SizedBox.fromSize(
                  size: size,
                  child: ProductFillImage(
                    imageUrl: '',
                    showRetry: false,
                    onUnavailable: () => unavailable++,
                  ),
                ),
              ),
            ),
          ),
        ),
      );
      await tester.pump();
      expect(find.bySemanticsLabel('Фото недоступно'), findsOneWidget);
      expect(find.text('Фото недоступно'), findsNothing);
      expect(find.text('Повторить'), findsNothing);
      expect(tester.getSize(find.byType(ProductFillImage)), size);
      await tester.tap(find.byType(ProductFillImage));
      await tester.pump();
      expect(opened, 1);
      expect(unavailable, 1);
      semantics.dispose();
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets(
    'Large missing photo retains its explanation without a useless retry',
    (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.dark,
          builder: (context, child) => MediaQuery(
            data: MediaQuery.of(
              context,
            ).copyWith(textScaler: const TextScaler.linear(2)),
            child: child!,
          ),
          home: const Scaffold(
            body: Center(
              child: SizedBox(
                width: 360,
                height: 400,
                child: ProductFillImage(imageUrl: ''),
              ),
            ),
          ),
        ),
      );
      expect(find.text('Фото недоступно'), findsOneWidget);
      expect(find.text('Повторить'), findsNothing);
      expect(tester.takeException(), isNull);
    },
  );
}
