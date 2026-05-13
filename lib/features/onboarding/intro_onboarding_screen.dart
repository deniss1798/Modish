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
      'https://images.unsplash.com/photo-1509631179647-0177331693ae?auto=format&fit=crop&w=700&q=80',
    ),
    (
      'Подборка для вас',
      'Сохраняйте любимое и переходите в магазин в один тап',
      'https://images.unsplash.com/photo-1483985988355-763728e1935b?auto=format&fit=crop&w=700&q=80',
    ),
    (
      'Образы на каждый день',
      'Готовые луки для офиса, прогулок и вечера',
      'https://images.unsplash.com/photo-1496747611176-843222e1e57c?auto=format&fit=crop&w=700&q=80',
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
                child: const Text(
                  'Пропустить',
                  style: TextStyle(color: AppColors.muted),
                ),
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
                        ClipRRect(
                          borderRadius: BorderRadius.circular(8),
                          child: SizedBox(
                            width: 210,
                            height: 250,
                            child: Image.network(
                              s.$3,
                              fit: BoxFit.cover,
                              errorBuilder: (context, error, stackTrace) =>
                                  const ColoredBox(
                                    color: AppColors.chipBg,
                                    child: Icon(
                                      Icons.checkroom_outlined,
                                      size: 54,
                                      color: AppColors.ink,
                                    ),
                                  ),
                            ),
                          ),
                        ),
                        const SizedBox(height: 34),
                        Align(
                          alignment: Alignment.centerLeft,
                          child: Text(
                            s.$1,
                            style: AppTextStyles.display.copyWith(fontSize: 28),
                          ),
                        ),
                        const SizedBox(height: 14),
                        Align(
                          alignment: Alignment.centerLeft,
                          child: Text(s.$2, style: AppTextStyles.bodyMuted),
                        ),
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
                    color: on ? AppColors.ink : AppColors.line,
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
