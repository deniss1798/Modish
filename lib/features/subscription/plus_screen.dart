import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';

class PlusScreen extends StatelessWidget {
  const PlusScreen({super.key, required this.controller});
  final AppController controller;

  Future<void> _returnToApp(BuildContext context) async {
    if (await Navigator.maybePop(context) || !context.mounted) return;
    // A direct route has no predecessor. Keep the current session/onboarding
    // stage rather than sending a cold user straight to the personal feed.
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(
        builder: (_) =>
            AppScope(notifier: controller, child: const RootScreen()),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        backgroundColor: AppColors.bg,
        elevation: 0,
        title: const Text('Modish'),
        leading: IconButton(
          tooltip: 'Вернуться',
          icon: const Icon(Icons.close),
          onPressed: () => _returnToApp(context),
        ),
      ),
      body: SafeArea(
        top: false,
        child: ListView(
          padding: const EdgeInsets.all(24),
          children: [
            const SoftCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Icon(Icons.info_outline, color: AppColors.accent, size: 32),
                  SizedBox(height: 20),
                  Text(
                    'Дополнительные возможности недоступны',
                    style: AppTextStyles.displaySm,
                  ),
                  SizedBox(height: 12),
                  Text(
                    'Вернитесь на предыдущий экран, чтобы продолжить пользоваться приложением.',
                    style: AppTextStyles.bodyMuted,
                  ),
                ],
              ),
            ),
            const SizedBox(height: 24),
            PrimaryButton(
              label: 'Вернуться',
              onPressed: () => _returnToApp(context),
            ),
          ],
        ),
      ),
    );
  }
}
