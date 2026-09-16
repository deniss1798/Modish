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
                  if (MediaQuery.sizeOf(context).height >= 800)
                    Text(
                      'Стиль, который\nчувствует вас',
                      textAlign: TextAlign.center,
                      style: AppTextStyles.display.copyWith(
                        color: AppColors.ink,
                        fontSize: 28,
                        height: 1.08,
                      ),
                    ),
                  if (MediaQuery.sizeOf(context).height >= 800)
                    const SizedBox(height: 14),
                  Text(
                    'Открывайте вещи. Собирайте образы.\nНаходите своё.',
                    textAlign: TextAlign.center,
                    style: AppTextStyles.body.copyWith(
                      color: AppColors.ink.withValues(alpha: 0.88),
                      fontSize: 15,
                      height: 1.45,
                    ),
                  ),
                  const SizedBox(height: 24),
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
