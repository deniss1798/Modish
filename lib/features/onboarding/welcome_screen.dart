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
    final landscape =
        MediaQuery.orientationOf(context) == Orientation.landscape;
    return MobileViewport(
      minimalBackdrop: true,
      child: Stack(
        fit: StackFit.expand,
        children: [
          HeroFashionBackdrop(
            // The photograph already contains a portrait wordmark. In a wide
            // crop use its upper scene and present the existing clean brand
            // beside the actions instead of enlarging the embedded lettering.
            alignment: landscape ? Alignment.topCenter : Alignment.center,
          ),
          SafeArea(
            child: LayoutBuilder(
              builder: (context, constraints) {
                final wide = landscape && constraints.maxWidth >= 480;
                return SingleChildScrollView(
                  key: const ValueKey('welcome-scroll'),
                  child: ConstrainedBox(
                    constraints: BoxConstraints(
                      minHeight: constraints.maxHeight,
                    ),
                    child: Padding(
                      padding: wide
                          ? const EdgeInsets.all(20)
                          : const EdgeInsets.fromLTRB(24, 16, 24, 24),
                      child: wide
                          ? Center(
                              child: ConstrainedBox(
                                constraints: const BoxConstraints(
                                  maxWidth: 960,
                                ),
                                child: Row(
                                  children: [
                                    Expanded(
                                      child: LayoutBuilder(
                                        builder: (context, brandConstraints) =>
                                            _LandscapeBrand(
                                              width: brandConstraints.maxWidth
                                                  .clamp(120, 240),
                                            ),
                                      ),
                                    ),
                                    const SizedBox(width: 24),
                                    SizedBox(
                                      key: const ValueKey('welcome-auth-panel'),
                                      width: (constraints.maxWidth * .42).clamp(
                                        220,
                                        320,
                                      ),
                                      child: _WelcomeActions(
                                        controller: controller,
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                            )
                          : Column(
                              mainAxisSize: MainAxisSize.min,
                              mainAxisAlignment: MainAxisAlignment.end,
                              crossAxisAlignment: CrossAxisAlignment.stretch,
                              children: [
                                if (landscape) ...[
                                  const _LandscapeBrand(width: 180),
                                  const SizedBox(height: 24),
                                ] else
                                  // Reserve the embedded portrait brand's area.
                                  // Large text grows the scrollable content below
                                  // it instead of covering the photograph's logo.
                                  SizedBox(
                                    height: ((constraints.maxHeight - 40) * .66)
                                        .clamp(220, double.infinity),
                                  ),
                                _WelcomeActions(controller: controller),
                              ],
                            ),
                    ),
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}

class _LandscapeBrand extends StatelessWidget {
  const _LandscapeBrand({required this.width});

  final double width;

  @override
  Widget build(BuildContext context) => Column(
    key: const ValueKey('welcome-landscape-brand'),
    mainAxisSize: MainAxisSize.min,
    children: [
      // The supplied clean logo has a near-black matte. Keep the original
      // asset and global Brand rendering; composite only this scene's matte
      // transparently so it does not appear as a rectangular black sticker.
      ColorFiltered(
        colorFilter: const ColorFilter.matrix([
          1,
          0,
          0,
          0,
          0,
          0,
          1,
          0,
          0,
          0,
          0,
          0,
          1,
          0,
          0,
          1,
          1,
          1,
          0,
          -48,
        ]),
        child: Brand(width: width),
      ),
      const SizedBox(height: 14),
      Text(
        'YOUR STYLE\nSMARTER',
        textAlign: TextAlign.center,
        style: AppTextStyles.caption.copyWith(
          fontSize: 10,
          letterSpacing: 3,
          height: 1.7,
          color: AppColors.accentSoft,
        ),
      ),
    ],
  );
}

class _WelcomeActions extends StatelessWidget {
  const _WelcomeActions({required this.controller});

  final AppController controller;

  @override
  Widget build(BuildContext context) => Column(
    mainAxisSize: MainAxisSize.min,
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
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
      Text(
        'Продолжая, вы соглашаетесь\nс Условиями и Политикой конфиденциальности',
        textAlign: TextAlign.center,
        style: AppTextStyles.caption.copyWith(
          color: AppColors.muted,
          height: 1.35,
        ),
      ),
    ],
  );
}
