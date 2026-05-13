import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';

class PlusScreen extends StatelessWidget {
  const PlusScreen({super.key, required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final b = controller.billing;
    final status = '${b['status'] ?? 'trial'}';
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        backgroundColor: AppColors.bg,
        elevation: 0,
        leading: IconButton(icon: const Icon(Icons.close), onPressed: () => Navigator.pop(context)),
      ),
      body: ListView(
        padding: const EdgeInsets.all(24),
        children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            decoration: BoxDecoration(
              color: AppColors.ink,
              borderRadius: BorderRadius.circular(20),
            ),
            child: const Text('Modish Plus', style: TextStyle(color: Colors.white, fontWeight: FontWeight.w600)),
          ),
          const SizedBox(height: 20),
          Text('AI-стилист\nбез ограничений', style: AppTextStyles.display.copyWith(fontSize: 34)),
          const SizedBox(height: 12),
          Text(
            'Персональные подборки, визуальный анализ и приоритетные рекомендации.',
            style: AppTextStyles.bodyMuted,
          ),
          const SizedBox(height: 28),
          const SoftCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _FeatureRow('Неограниченные подборки'),
                _FeatureRow('1 визуальный анализ в месяц'),
                _FeatureRow('Образы под любой сценарий'),
                _FeatureRow('Приоритет в ленте'),
              ],
            ),
          ),
          const SizedBox(height: 24),
          Text('129 ₽ / месяц', style: AppTextStyles.price.copyWith(fontSize: 28)),
          Text('Статус: $status', style: AppTextStyles.caption),
          const SizedBox(height: 24),
          PrimaryButton(
            label: 'Оформить Plus',
            onPressed: () {
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(content: Text('Оплата в прототипе не подключена')),
              );
            },
          ),
        ],
      ),
    );
  }
}

class _FeatureRow extends StatelessWidget {
  const _FeatureRow(this.text);
  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Row(
        children: [
          const Icon(Icons.check_circle_outline, color: AppColors.success, size: 22),
          const SizedBox(width: 10),
          Expanded(child: Text(text)),
        ],
      ),
    );
  }
}
