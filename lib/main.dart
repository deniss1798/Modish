import 'package:flutter/material.dart';
import 'app.dart';
import 'core/theme/app_text_styles.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  AppFonts.registerLicenses();
  runApp(const ModishBootstrap());
}
