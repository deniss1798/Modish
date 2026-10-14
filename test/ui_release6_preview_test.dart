// Offline diagnostic previews. Historical release screenshots stay immutable.
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/theme/app_theme.dart';
import 'package:modish/features/onboarding/welcome_screen.dart';
import 'package:modish/features/auth/auth_screen.dart';

import 'app_regressions_test.dart' show FakeApi, product;
import 'support/load_app_fonts.dart';

class _NoPreviewHttp extends HttpOverrides {
  int attempts = 0;
  @override
  HttpClient createHttpClient(SecurityContext? context) {
    attempts++;
    throw StateError('Preview tests must not create an HTTP client');
  }
}

/// Shared by the two old previews. Generic product placeholders are deliberately
/// not fetched; these captures check layout, not catalogue photography.
Future<void> renderOfflinePhonePreviews(
  WidgetTester tester, {
  required AppController controller,
  required Map<String, Widget> screens,
  required String output,
}) async {
  final oldHttp = HttpOverrides.current;
  final noHttp = _NoPreviewHttp();
  const storageChannel = MethodChannel(
    'plugins.it_nomads.com/flutter_secure_storage',
  );
  final messenger =
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
  messenger.setMockMethodCallHandler(storageChannel, (_) async => null);
  addTearDown(() => messenger.setMockMethodCallHandler(storageChannel, null));
  var disposed = false;
  void disposeController() {
    if (disposed) return;
    disposed = true;
    controller.dispose();
    controller.api.dispose();
  }

  addTearDown(disposeController);
  addTearDown(() => HttpOverrides.global = oldHttp);
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPadding);
  addTearDown(tester.view.resetViewPadding);
  addTearDown(tester.view.resetViewInsets);
  HttpOverrides.global = noHttp;
  try {
    await tester.runAsync(
      () => loadAppFonts().timeout(const Duration(seconds: 10)),
    );
    // Prime before any screen creates AssetImage streams under the fake clock.
    // Precache after pumpWidget(screen) can await that clock indefinitely.
    await tester.pumpWidget(
      MaterialApp(theme: AppTheme.dark, home: const SizedBox()),
    );
    await tester.runAsync(() async {
      final context = tester.element(find.byType(SizedBox).last);
      for (final path in ['assets/images/back.png', 'assets/images/logo.png']) {
        await precacheImage(
          AssetImage(path),
          context,
        ).timeout(const Duration(seconds: 5));
      }
    });
    await tester.runAsync(
      () =>
          controller.refreshFeedFromUser().timeout(const Duration(seconds: 5)),
    );
    for (final size in [const Size(390, 844), const Size(360, 740)]) {
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1;
      tester.view.padding = const FakeViewPadding(top: 30, bottom: 24);
      tester.view.viewPadding = const FakeViewPadding(top: 30, bottom: 24);
      tester.view.viewInsets = const FakeViewPadding();
      for (final entry in screens.entries) {
        final key = GlobalKey();
        await tester.pumpWidget(
          RepaintBoundary(
            key: key,
            child: MaterialApp(
              debugShowCheckedModeBanner: false,
              theme: AppTheme.dark,
              home: entry.value,
            ),
          ),
        );
        // Give image decoding a real-clock turn without waiting for continuous
        // loading/visibility animations to settle indefinitely.
        await tester.pump(const Duration(milliseconds: 200));
        await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 80)),
        );
        await tester.pump(const Duration(milliseconds: 200));
        expect(tester.takeException(), isNull, reason: '${entry.key} at $size');
        expect(
          noHttp.attempts,
          0,
          reason: 'All preview reads must be fake API calls',
        );
        await tester.runAsync(() async {
          final boundary =
              key.currentContext!.findRenderObject() as RenderRepaintBoundary;
          final image = await boundary
              .toImage(pixelRatio: 1)
              .timeout(const Duration(seconds: 5));
          try {
            final bytes = await image
                .toByteData(format: ui.ImageByteFormat.png)
                .timeout(const Duration(seconds: 5));
            expect(bytes, isNotNull);
            final file = File('$output/${entry.key}-${size.width.toInt()}.png');
            await file.parent
                .create(recursive: true)
                .timeout(const Duration(seconds: 5));
            await file
                .writeAsBytes(bytes!.buffer.asUint8List())
                .timeout(const Duration(seconds: 5));
          } finally {
            image.dispose();
          }
        });
        await tester.pumpWidget(const SizedBox());
        await tester.pump();
      }
    }
    expect(noHttp.attempts, 0);
  } finally {
    try {
      await tester.pumpWidget(const SizedBox());
      await tester.pump();
    } finally {
      disposeController();
      HttpOverrides.global = oldHttp;
    }
  }
}

void main() {
  testWidgets(
    'Release 6 phone layouts fit and render',
    (tester) async {
      final api = FakeApi()
        ..rows = [
          {
            'product': {
              ...product('shirt'),
              'title': 'Рубашка из фактурного хлопка',
              'brand': 'BAON',
              'price': 4599,
              'old_price': 5999,
              'available_sizes': ['M', 'L', 'XL'],
            },
            'reasons': ['Подходит вашему стилю'],
          },
        ];
      final controller = AppController(apiClient: api)
        ..email = 'style@example.test';
      await renderOfflinePhonePreviews(
        tester,
        controller: controller,
        output: const String.fromEnvironment(
          'PREVIEW6_OUTPUT',
          defaultValue: '.tmp/alpha02-preview6',
        ),
        screens: {
          'welcome': WelcomeScreen(controller: controller),
          'feed': HomeScreen(controller: controller),
          'auth': AuthScreen(controller: controller),
        },
      );
    },
    timeout: const Timeout(Duration(minutes: 2)),
  );
}
