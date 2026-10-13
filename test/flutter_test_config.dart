import 'dart:async';

import 'package:flutter_test/flutter_test.dart';

import 'support/load_app_fonts.dart';

/// Every widget suite uses the same local font binaries as the application.
Future<void> testExecutable(FutureOr<void> Function() testMain) async {
  TestWidgetsFlutterBinding.ensureInitialized();
  await loadAppFonts();
  await testMain();
}
