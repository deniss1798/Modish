import 'package:flutter/material.dart';
import 'app_colors.dart';
import 'app_text_styles.dart';

/// One design-system boundary, separate from navigation and account state.
class AppTheme {
  AppTheme._();

  static final ThemeData dark = ThemeData(
    useMaterial3: true,
    brightness: Brightness.dark,
    scaffoldBackgroundColor: AppColors.bg,
    fontFamily: AppFonts.bodyFamily,
    colorScheme: const ColorScheme.dark(
      surface: AppColors.card,
      primary: AppColors.accent,
      onPrimary: AppColors.onAccent,
      secondary: AppColors.accentSoft,
      onSurface: AppColors.ink,
      outline: AppColors.line,
    ),
    dividerColor: AppColors.line,
    iconTheme: const IconThemeData(color: AppColors.ink),
    appBarTheme: const AppBarTheme(
      centerTitle: true,
      backgroundColor: AppColors.bg,
      surfaceTintColor: Colors.transparent,
      foregroundColor: AppColors.ink,
      elevation: 0,
    ),
    chipTheme: ChipThemeData(
      backgroundColor: AppColors.chipBg,
      selectedColor: AppColors.accent,
      side: const BorderSide(color: AppColors.line),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18)),
      labelStyle: AppTextStyles.chip,
      secondaryLabelStyle: const TextStyle(
        fontFamily: AppFonts.bodyFamily,
        fontSize: 12,
        color: AppColors.onAccent,
      ),
    ),
    sliderTheme: const SliderThemeData(
      activeTrackColor: AppColors.accent,
      inactiveTrackColor: AppColors.line,
      thumbColor: AppColors.accent,
      overlayColor: Color(0x33C4A574),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: AppColors.card,
      hintStyle: const TextStyle(
        fontFamily: AppFonts.bodyFamily,
        color: AppColors.muted,
      ),
      labelStyle: const TextStyle(
        fontFamily: AppFonts.bodyFamily,
        color: AppColors.muted,
      ),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(16),
        borderSide: const BorderSide(color: AppColors.line),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(16),
        borderSide: const BorderSide(color: AppColors.line),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(16),
        borderSide: const BorderSide(color: AppColors.accent, width: 1.2),
      ),
    ),
    textTheme: const TextTheme(
      bodyMedium: TextStyle(
        fontFamily: AppFonts.bodyFamily,
        color: AppColors.ink,
      ),
      bodySmall: TextStyle(
        fontFamily: AppFonts.bodyFamily,
        color: AppColors.muted,
      ),
      titleMedium: TextStyle(
        fontFamily: AppFonts.bodyFamily,
        color: AppColors.ink,
        fontWeight: FontWeight.w600,
      ),
    ),
    snackBarTheme: SnackBarThemeData(
      backgroundColor: AppColors.surface,
      contentTextStyle: const TextStyle(
        fontFamily: AppFonts.bodyFamily,
        color: AppColors.ink,
      ),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      behavior: SnackBarBehavior.floating,
    ),
  );
}
