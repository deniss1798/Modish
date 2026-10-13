import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'app_colors.dart';

class AppFonts {
  AppFonts._();
  static const bodyFamily = 'Inter';
  static const displayFamily = 'PlayfairDisplay';
  static bool _licensesRegistered = false;

  static void registerLicenses() {
    if (_licensesRegistered) return;
    _licensesRegistered = true;
    LicenseRegistry.addLicense(() async* {
      for (final entry in const {
        'Inter': 'assets/fonts/Inter-OFL.txt',
        'Playfair Display': 'assets/fonts/PlayfairDisplay-OFL.txt',
      }.entries) {
        yield LicenseEntryWithLineBreaks([
          entry.key,
        ], await rootBundle.loadString(entry.value));
      }
    });
  }
}

class AppTextStyles {
  static const display = TextStyle(
    fontFamily: AppFonts.displayFamily,
    fontSize: 34,
    height: 1.1,
    fontWeight: FontWeight.w500,
    color: AppColors.ink,
  );

  static const displaySm = TextStyle(
    fontFamily: AppFonts.displayFamily,
    fontSize: 26,
    height: 1.15,
    fontWeight: FontWeight.w500,
    color: AppColors.ink,
  );

  static const screenTitle = TextStyle(
    fontFamily: AppFonts.displayFamily,
    fontSize: 24,
    height: 1.1,
    fontWeight: FontWeight.w500,
    color: AppColors.ink,
  );

  static const sectionTitle = TextStyle(
    fontFamily: AppFonts.bodyFamily,
    fontSize: 16,
    fontWeight: FontWeight.w700,
    color: AppColors.ink,
  );

  static const body = TextStyle(
    fontFamily: AppFonts.bodyFamily,
    fontSize: 14,
    height: 1.4,
    color: AppColors.ink,
  );

  static const bodyMuted = TextStyle(
    fontFamily: AppFonts.bodyFamily,
    fontSize: 13,
    height: 1.4,
    color: AppColors.muted,
  );

  static const caption = TextStyle(
    fontFamily: AppFonts.bodyFamily,
    fontSize: 11,
    height: 1.3,
    color: AppColors.muted,
  );

  static const price = TextStyle(
    fontFamily: AppFonts.bodyFamily,
    fontSize: 18,
    fontWeight: FontWeight.w700,
    color: AppColors.ink,
  );

  static const chip = TextStyle(
    fontFamily: AppFonts.bodyFamily,
    fontSize: 12,
    fontWeight: FontWeight.w600,
    color: AppColors.ink,
  );
}
