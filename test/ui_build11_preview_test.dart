import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/theme/app_theme.dart';
import 'package:modish/features/onboarding/welcome_screen.dart';
import 'package:modish/features/profile/edit_profile_screen.dart';
import 'package:modish/features/profile/profile_screen.dart';
import 'package:modish/features/catalog/search_screen.dart';
import 'app_regressions_test.dart' show FakeApi;

// Override explicitly with --dart-define=BUILD11_PREVIEW_OUTPUT=path.
const _destination = String.fromEnvironment(
  'BUILD11_PREVIEW_OUTPUT',
  defaultValue: '.tmp/ui-build11-preview',
);

void main() {
  testWidgets('Build11 screens respect system bars and render', (tester) async {
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
      tester.view.padding = const FakeViewPadding(top: 28, bottom: 48);
      tester.view.viewPadding = const FakeViewPadding(top: 28, bottom: 48);
      final c = AppController(apiClient: FakeApi())
        ..email = 'style@example.test'
        ..fitProfile = {
          'height': 170,
          'clothing_size': 'L',
          'shoe_size': '39',
          'budget_max': 25000,
          'gender_target': 'womenswear',
          'activation_segment': 'female_casual',
        }
        ..tasteProfile = {
          'weights': {
            'styles': {'minimalism': 3, 'casual': 2},
            'colors': {'blue': 3, 'white': 2, 'beige': 1},
          },
          'liked_styles': ['minimalism', 'casual'],
          'liked_colors': ['blue', 'white', 'beige'],
        }
        ..activationStatus = {
          'style_preferences': ['minimalism', 'casual'],
        };
      for (final entry in <String, Widget>{
        'welcome': WelcomeScreen(controller: c),
        'parameters': EditProfileScreen(controller: c),
        'profile': Scaffold(
          body: SafeArea(child: ProfileScreen(controller: c)),
        ),
        'search': SearchScreen(controller: c),
        'feed': HomeScreen(controller: c),
      }.entries) {
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
              home: entry.value,
            ),
          ),
        );
        await tester.runAsync(() async {
          final ctx = tester.element(find.byType(Scaffold).first);
          await precacheImage(const AssetImage('assets/images/back.png'), ctx);
        });
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull, reason: '${entry.key} at $size');
        await tester.runAsync(() async {
          final boundary =
              key.currentContext!.findRenderObject() as RenderRepaintBoundary;
          final image = await boundary.toImage(pixelRatio: 1);
          final bytes = (await image.toByteData(
            format: ui.ImageByteFormat.png,
          ))!;
          final file = File(
            '$_destination/${entry.key}-${size.width.toInt()}.png',
          );
          await file.parent.create(recursive: true);
          await file.writeAsBytes(bytes.buffer.asUint8List());
          image.dispose();
        });
        await tester.pumpWidget(const SizedBox());
      }
      c.dispose();
    }
    tester.view.resetPhysicalSize();
    tester.view.resetDevicePixelRatio();
    tester.view.resetPadding();
    tester.view.resetViewPadding();
  });
}
