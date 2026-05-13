import 'package:flutter/material.dart';
import '../../app.dart';
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
              padding: const EdgeInsets.fromLTRB(24, 34, 24, 24),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Brand(size: 34, light: true),
                  const Spacer(),
                  Text(
                    'Ваш\nперсональный\nстилист',
                    style: AppTextStyles.display.copyWith(
                      color: Colors.white,
                      fontSize: 32,
                    ),
                  ),
                  const SizedBox(height: 12),
                  Text(
                    'AI-подборки, вдохновение\nи лучшие вещи в одном месте',
                    style: AppTextStyles.body.copyWith(
                      color: Colors.white.withValues(alpha: .9),
                    ),
                  ),
                  const SizedBox(height: 28),
                  PrimaryButton(
                    label: 'Войти',
                    onPressed: () => controller.goToAuth(registerMode: false),
                  ),
                  const SizedBox(height: 12),
                  SecondaryButton(
                    label: 'Создать аккаунт',
                    onPressed: () => controller.goToOnboarding(),
                  ),
                  const SizedBox(height: 14),
                  Center(
                    child: Text(
                      'Продолжая, вы соглашаетесь\nс Условиями и Политикой конфиденциальности',
                      textAlign: TextAlign.center,
                      style: AppTextStyles.caption.copyWith(
                        color: Colors.white70,
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
