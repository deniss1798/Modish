import 'package:flutter/material.dart';
import 'app_colors.dart';

class AppTextStyles {
  static const display = TextStyle(
    fontFamily: 'Georgia',
    fontSize: 34,
    height: 1.1,
    fontWeight: FontWeight.w500,
    color: AppColors.ink,
  );

  static const displaySm = TextStyle(
    fontFamily: 'Georgia',
    fontSize: 26,
    height: 1.15,
    fontWeight: FontWeight.w500,
    color: AppColors.ink,
  );

  static const screenTitle = TextStyle(
    fontFamily: 'Georgia',
    fontSize: 24,
    height: 1.1,
    fontWeight: FontWeight.w500,
    color: AppColors.ink,
  );

  static const sectionTitle = TextStyle(
    fontSize: 16,
    fontWeight: FontWeight.w700,
    color: AppColors.ink,
  );

  static const body = TextStyle(
    fontSize: 14,
    height: 1.4,
    color: AppColors.ink,
  );

  static const bodyMuted = TextStyle(
    fontSize: 13,
    height: 1.4,
    color: AppColors.muted,
  );

  static const caption = TextStyle(
    fontSize: 11,
    height: 1.3,
    color: AppColors.muted,
  );

  static const price = TextStyle(
    fontSize: 18,
    fontWeight: FontWeight.w700,
    color: AppColors.ink,
  );

  static const chip = TextStyle(
    fontSize: 12,
    fontWeight: FontWeight.w600,
    color: AppColors.ink,
  );
}
