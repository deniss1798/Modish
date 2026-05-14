import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';

class IntroOnboardingScreen extends StatefulWidget {
  const IntroOnboardingScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<IntroOnboardingScreen> createState() => _IntroOnboardingScreenState();
}

class _IntroOnboardingScreenState extends State<IntroOnboardingScreen> {
  final _page = PageController();
  int _index = 0;

  static const _slides = [
    (
      'Ваш стиль.\nНаш интеллект',
      'Персональные подборки, основанные на ваших предпочтениях',
      Icons.auto_awesome_outlined,
    ),
    (
      'Подборка для вас',
      'Сохраняйте любимое и переходите в магазин в один тап',
      Icons.explore_outlined,
    ),
    (
      'Образы на каждый день',
      'Готовые луки для офиса, прогулок и вечера',
      Icons.style_outlined,
    ),
  ];

  @override
  void dispose() {
    _page.dispose();
    super.dispose();
  }

  void _next() {
    if (_index < _slides.length - 1) {
      _page.nextPage(
        duration: const Duration(milliseconds: 320),
        curve: Curves.easeOut,
      );
    } else {
      widget.controller.goToAuth(registerMode: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    return MobileViewport(
      child: SafeArea(
        child: Column(
          children: [
            Align(
              alignment: Alignment.centerRight,
              child: TextButton(
                onPressed: () => widget.controller.goToAuth(registerMode: true),
                child: const Text('Пропустить', style: TextStyle(color: AppColors.muted)),
              ),
            ),
            Expanded(
              child: PageView.builder(
                controller: _page,
                itemCount: _slides.length,
                onPageChanged: (i) => setState(() => _index = i),
                itemBuilder: (context, i) {
                  final s = _slides[i];
                  return Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 28),
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Container(
                          width: 120,
                          height: 120,
                          decoration: BoxDecoration(
                            color: AppColors.chipBg,
                            borderRadius: BorderRadius.circular(32),
                            border: Border.all(color: AppColors.line),
                          ),
                          child: Icon(s.$3, size: 52, color: AppColors.accent),
                        ),
                        const SizedBox(height: 36),
                        Text(s.$1, textAlign: TextAlign.center, style: AppTextStyles.display),
                        const SizedBox(height: 16),
                        Text(s.$2, textAlign: TextAlign.center, style: AppTextStyles.bodyMuted),
                      ],
                    ),
                  );
                },
              ),
            ),
            Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: List.generate(_slides.length, (i) {
                final on = i == _index;
                return AnimatedContainer(
                  duration: const Duration(milliseconds: 200),
                  margin: const EdgeInsets.symmetric(horizontal: 4),
                  width: on ? 24 : 8,
                  height: 8,
                  decoration: BoxDecoration(
                    color: on ? AppColors.accent : AppColors.line,
                    borderRadius: BorderRadius.circular(4),
                  ),
                );
              }),
            ),
            const SizedBox(height: 24),
            Padding(
              padding: const EdgeInsets.fromLTRB(24, 0, 24, 24),
              child: PrimaryButton(
                label: _index < _slides.length - 1 ? 'Далее' : 'Начать',
                onPressed: _next,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
