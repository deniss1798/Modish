import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/widgets/modish_widgets.dart';

/// Загрузка анализа: ровный фон, заголовок по центру, линейный прогресс (без смещения Material ring).
class AnalysisScreen extends StatelessWidget {
  const AnalysisScreen({super.key, required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final progress = (controller.analysisProgress / 100).clamp(0.0, 1.0);
    final pct = controller.analysisProgress;
    return MobileViewport(
      minimalBackdrop: true,
      child: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 32),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Center(child: Brand()),
              const SizedBox(height: 32),
              Text(
                'Анализируем\nваш стиль',
                textAlign: TextAlign.center,
                style: const TextStyle(
                  fontFamily: 'Georgia',
                  fontSize: 30,
                  height: 1.25,
                  color: AppColors.ink,
                ),
              ),
              const SizedBox(height: 40),
              ClipRRect(
                borderRadius: BorderRadius.circular(999),
                child: LinearProgressIndicator(
                  value: progress,
                  minHeight: 8,
                  backgroundColor: AppColors.line,
                  color: AppColors.accent,
                ),
              ),
              const SizedBox(height: 20),
              Text(
                '$pct%',
                textAlign: TextAlign.center,
                style: const TextStyle(
                  fontFamily: 'Georgia',
                  fontSize: 34,
                  fontWeight: FontWeight.w600,
                  color: AppColors.ink,
                  height: 1.0,
                ),
              ),
              const SizedBox(height: 12),
              Text(
                pct >= 100 ? 'Отправляем фото на сервер…' : 'Оцениваем силуэт и палитру…',
                textAlign: TextAlign.center,
                style: const TextStyle(
                  fontSize: 15,
                  color: AppColors.muted,
                  height: 1.35,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
