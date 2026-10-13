import 'package:flutter/services.dart';

Future<void>? _loading;

/// Uses exactly the locally bundled production fonts. No network/font service.
/// Call outside the widget test's fake clock, or from tester.runAsync.
Future<void> loadAppFonts() => _loading ??= _load();

Future<void> _load() async {
  for (final font in {
    'Inter': 'assets/fonts/Inter.ttf',
    'PlayfairDisplay': 'assets/fonts/PlayfairDisplay.ttf',
    'MaterialIcons': 'fonts/MaterialIcons-Regular.otf',
  }.entries) {
    await (FontLoader(font.key)..addFont(rootBundle.load(font.value))).load();
  }
}
