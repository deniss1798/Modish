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
    final uploaded = pct >= 100;
    return MobileViewport(
      minimalBackdrop: true,
      child: SafeArea(
        child: Stack(
          children: [
            Padding(
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
                  _Steps(
                    uploaded: uploaded,
                    analyzing: uploaded,
                    ready: false,
                  ),
                  const SizedBox(height: 18),
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
                    pct >= 100 ? 'Фото загружено · анализируем на сервере…' : 'Загружаем фото…',
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
            Positioned(
              left: 8,
              top: 4,
              child: IconButton(
                onPressed: () => controller.exitUploadFlow(),
                icon: const Icon(Icons.arrow_back),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Steps extends StatelessWidget {
  const _Steps({required this.uploaded, required this.analyzing, required this.ready});
  final bool uploaded;
  final bool analyzing;
  final bool ready;

  Widget _row(String label, bool done) {
    return Row(
      children: [
        Icon(done ? Icons.check_circle : Icons.radio_button_unchecked, size: 18, color: AppColors.accent),
        const SizedBox(width: 8),
        Text(label, style: const TextStyle(color: AppColors.muted)),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _row('загружено', uploaded),
        const SizedBox(height: 6),
        _row('анализируется', analyzing),
        const SizedBox(height: 6),
        _row('готово', ready),
      ],
    );
  }
}
