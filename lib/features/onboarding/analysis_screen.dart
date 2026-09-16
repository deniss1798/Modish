import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';

class AnalysisScreen extends StatelessWidget {
  const AnalysisScreen({super.key, required this.controller});
  final AppController controller;
  @override
  Widget build(BuildContext context) => PopScope(
    canPop: false,
    onPopInvokedWithResult: (didPop, _) {
      if (!didPop) controller.exitUploadFlow();
    },
    child: MobileViewport(
      minimalBackdrop: true,
      child: SafeArea(
        child: Stack(
          children: [
            Center(
              child: Padding(
                padding: const EdgeInsets.all(32),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Brand(),
                    const SizedBox(height: 36),
                    const Text(
                      'Анализируем ваш стиль',
                      textAlign: TextAlign.center,
                      style: AppTextStyles.screenTitle,
                    ),
                    const SizedBox(height: 28),
                    const CircularProgressIndicator(color: AppColors.accent),
                    const SizedBox(height: 24),
                    Text(
                      controller.analysisProgress >= 100
                          ? 'Анализ готов. Обновляем рекомендации…'
                          : 'Отправляем фото и уточняем рекомендации. Это может занять немного времени.',
                      textAlign: TextAlign.center,
                      style: AppTextStyles.bodyMuted,
                    ),
                    const SizedBox(height: 24),
                    TextButton(
                      onPressed: controller.exitUploadFlow,
                      child: const Text('Вернуться в профиль'),
                    ),
                  ],
                ),
              ),
            ),
            Positioned(
              left: 8,
              top: 4,
              child: IconButton(
                tooltip: 'Вернуться в профиль',
                onPressed: controller.exitUploadFlow,
                icon: const Icon(Icons.arrow_back),
              ),
            ),
          ],
        ),
      ),
    ),
  );
}
