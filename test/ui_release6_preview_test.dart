import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/theme/app_colors.dart';
import 'package:modish/features/onboarding/welcome_screen.dart';
import 'package:modish/features/auth/auth_screen.dart';
import 'app_regressions_test.dart' show FakeApi, product;

void main() {
  testWidgets('Release 6 phone layouts fit and render', (tester) async {
    await tester.runAsync(() async {
      await (FontLoader(
        'MaterialIcons',
      )..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf'))).load();
      for (final font in {
        'Arial': 'arial.ttf',
        'Georgia': 'georgia.ttf',
      }.entries) {
        final data = await File('C:/Windows/Fonts/${font.value}').readAsBytes();
        await (FontLoader(
          font.key,
        )..addFont(Future.value(ByteData.sublistView(data)))).load();
      }
    });
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
    await controller.refreshFeedFromUser();
    for (final size in [const Size(390, 844), const Size(360, 740)]) {
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1;
      tester.view.padding = const FakeViewPadding(top: 30, bottom: 24);
      for (final entry in <String, Widget>{
        'welcome': WelcomeScreen(controller: controller),
        'feed': HomeScreen(controller: controller),
        'auth': AuthScreen(controller: controller),
      }.entries) {
        final key = GlobalKey();
        await tester.pumpWidget(
          RepaintBoundary(
            key: key,
            child: MaterialApp(
              debugShowCheckedModeBanner: false,
              theme: ThemeData(
                useMaterial3: true,
                brightness: Brightness.dark,
                fontFamily: 'Arial',
                scaffoldBackgroundColor: AppColors.bg,
                colorScheme: const ColorScheme.dark(
                  primary: AppColors.accent,
                  surface: AppColors.card,
                  onSurface: AppColors.ink,
                ),
              ),
              home: entry.value,
            ),
          ),
        );
        await tester.runAsync(() async {
          final ctx = tester.element(find.byType(Scaffold).first);
          await precacheImage(const AssetImage('assets/images/back.png'), ctx);
          await precacheImage(const AssetImage('assets/images/logo.png'), ctx);
        });
        await tester.pumpAndSettle();
        final layoutError = tester.takeException();
        expect(layoutError, isNull, reason: '${entry.key} at $size');
        await tester.runAsync(() async {
          final boundary =
              key.currentContext!.findRenderObject() as RenderRepaintBoundary;
          final image = await boundary.toImage(pixelRatio: 1);
          final bytes = (await image.toByteData(
            format: ui.ImageByteFormat.png,
          ))!;
          final file = File(
            'docs/modish2/build6-preview/${entry.key}-${size.width.toInt()}.png',
          );
          await file.parent.create(recursive: true);
          await file.writeAsBytes(bytes.buffer.asUint8List());
          image.dispose();
        });
      }
    }
    tester.view.resetPhysicalSize();
    tester.view.resetDevicePixelRatio();
    tester.view.resetPadding();
  });
}
