import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';

class WelcomeScreen extends StatelessWidget {
  const WelcomeScreen({super.key, required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    return MobileViewport(
      minimalBackdrop: true,
      child: Stack(
        fit: StackFit.expand,
        children: [
          const HeroFashionBackdrop(),
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(24, 16, 24, 24),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const SizedBox(height: 8),
                  const Center(child: GoldWordmark(fontSize: 38)),
                  const Spacer(),
                  Text(
                    'Ваш\nперсональный\nстилист',
                    textAlign: TextAlign.left,
                    style: AppTextStyles.display.copyWith(
                      color: AppColors.ink,
                      fontSize: 32,
                      height: 1.08,
                    ),
                  ),
                  const SizedBox(height: 14),
                  Text(
                    'AI-подборки, вдохновение\nи лучшие вещи в одном месте',
                    textAlign: TextAlign.left,
                    style: AppTextStyles.body.copyWith(
                      color: AppColors.ink.withValues(alpha: 0.88),
                      fontSize: 15,
                      height: 1.45,
                    ),
                  ),
                  const SizedBox(height: 32),
                  PrimaryButton(
                    label: 'Войти',
                    onPressed: () => controller.goToAuth(registerMode: false),
                  ),
                  const SizedBox(height: 12),
                  SecondaryButton(
                    label: 'Создать аккаунт',
                    onDark: true,
                    onPressed: () => controller.goToAuth(registerMode: true),
                  ),
                  const SizedBox(height: 16),
                  Center(
                    child: Text(
                      'Продолжая, вы соглашаетесь\nс Условиями и Политикой конфиденциальности',
                      textAlign: TextAlign.center,
                      style: AppTextStyles.caption.copyWith(
                        color: AppColors.muted,
                        height: 1.35,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
