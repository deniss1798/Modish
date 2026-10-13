import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/theme/app_theme.dart';
import 'package:modish/features/onboarding/onboarding_v2_screen.dart';
import 'activation_test.dart' show ActivationApi;

void main() {
  testWidgets('Activation screens fit small phones and large text', (
    tester,
  ) async {
    await tester.runAsync(() async {
      await (FontLoader(
        'MaterialIcons',
      )..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf'))).load();
      for (final font in {
        'Arial': 'arial.ttf',
        'Georgia': 'georgia.ttf',
      }.entries) {
        final bytes = await File(
          'C:/Windows/Fonts/${font.value}',
        ).readAsBytes();
        await (FontLoader(
          font.key,
        )..addFont(Future.value(ByteData.sublistView(bytes)))).load();
      }
    });
    late ThemeData appTheme;
    await tester.pumpWidget(
      Builder(
        builder: (context) {
          appTheme = AppTheme.dark;
          // Flutter's test-only fallback font does not contain Cyrillic glyphs.
          appTheme = appTheme.copyWith(
            chipTheme: appTheme.chipTheme.copyWith(
              labelStyle: appTheme.chipTheme.labelStyle?.copyWith(
                fontFamily: 'Arial',
              ),
              secondaryLabelStyle: appTheme.chipTheme.secondaryLabelStyle
                  ?.copyWith(fontFamily: 'Arial'),
            ),
          );
          return const SizedBox();
        },
      ),
    );
    for (final size in [const Size(360, 640), const Size(390, 844)]) {
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1;
      tester.view.padding = const FakeViewPadding(top: 28, bottom: 24);
      for (final step in [0, 1, 5]) {
        final c = AppController(apiClient: ActivationApi()..rated = 15)
          ..onboardingV2SubStep = step
          ..activationStatus = {
            'age_group': '25-34',
            'clothing_size': 'M',
            'shoe_size': '38',
            'style_preferences': ['casual', 'minimalism'],
            'price_segment': 'mass',
          };
        if (step == 1) await c.loadStyleSwipe();
        final key = GlobalKey();
        await tester.pumpWidget(
          RepaintBoundary(
            key: key,
            child: MaterialApp(
              debugShowCheckedModeBanner: false,
              theme: appTheme,
              builder: (context, child) => MediaQuery(
                data: MediaQuery.of(
                  context,
                ).copyWith(textScaler: const TextScaler.linear(1.15)),
                child: child!,
              ),
              home: Scaffold(body: OnboardingV2Screen(controller: c)),
            ),
          ),
        );
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull, reason: 'step $step at $size');
        await tester.runAsync(() async {
          final boundary =
              key.currentContext!.findRenderObject() as RenderRepaintBoundary;
          final image = await boundary.toImage(pixelRatio: 1);
          final bytes = (await image.toByteData(
            format: ui.ImageByteFormat.png,
          ))!;
          final file = File(
            'docs/modish2/build9-preview/activation-$step-${size.width.toInt()}.png',
          );
          await file.parent.create(recursive: true);
          await file.writeAsBytes(bytes.buffer.asUint8List());
          image.dispose();
        });
        await tester.pumpWidget(const SizedBox());
        c.dispose();
      }
    }
    tester.view.resetPhysicalSize();
    tester.view.resetDevicePixelRatio();
    tester.view.resetPadding();
  });
}
