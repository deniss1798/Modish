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
                  const Spacer(),
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
