import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/widgets/modish_widgets.dart';

class ProfileScreen extends StatelessWidget {
  const ProfileScreen({super.key, required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final b = controller.billing;
    final trialEnd = '${b['trial_ends_at'] ?? '—'}';
    final plus = b['is_plus_available'] == true;
    final status = '${b['status'] ?? 'trial'}';
    final plan = '${b['plan'] ?? 'plus'}';

    return SafeArea(
      child: ListView(
        padding: const EdgeInsets.fromLTRB(18, 22, 18, 20),
        children: [
          const Center(child: Brand()),
          const SizedBox(height: 12),
          const Center(
            child: Text(
              'Профиль',
              style: TextStyle(fontSize: 30, color: AppColors.ink),
            ),
          ),
          const SizedBox(height: 20),
          SoftCard(
            child: ListTile(
              contentPadding: EdgeInsets.zero,
              leading: const CircleAvatar(
                radius: 26,
                child: Icon(Icons.person),
              ),
              title: Text(
                controller.email.split('@').first,
                style: const TextStyle(fontFamily: 'Georgia', fontSize: 26),
              ),
              subtitle: Text(controller.email),
            ),
          ),
          const SizedBox(height: 14),
          SoftCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'Подписка',
                  style: TextStyle(fontWeight: FontWeight.w700, fontSize: 18),
                ),
                const SizedBox(height: 8),
                Text('План: $plan · статус: $status'),
                Text('Plus доступен: ${plus ? 'да' : 'нет'}'),
                Text('Окончание trial: $trialEnd'),
                const SizedBox(height: 6),
                const Text(
                  '129 ₽ / месяц после trial (без привязки карты в этом прототипе).',
                  style: TextStyle(fontSize: 13, color: AppColors.muted),
                ),
                const SizedBox(height: 12),
                FilledButton(
                  onPressed: () {},
                  child: const Text('Оформить Plus'),
                ),
              ],
            ),
          ),
          const SizedBox(height: 14),
          SoftCard(
            child: Column(
              children: [
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(
                    Icons.auto_awesome,
                    color: AppColors.accent,
                  ),
                  title: const Text('Обновить фото-анализ'),
                  subtitle: const Text(
                    'Plus: 1 анализ в месяц. Выберите новое фото.',
                    style: TextStyle(fontSize: 12),
                  ),
                  onTap: controller.isLoading ? null : controller.goToReanalyze,
                ),
                const Divider(),
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.palette_outlined, color: AppColors.accent),
                  title: const Text('Направление рекомендаций'),
                  subtitle: Text('Сейчас: ${controller.styleTarget}'),
                  onTap: () => controller.goToReanalyze(),
                ),
                const Divider(),
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.settings_outlined, color: AppColors.muted),
                  title: const Text('Настройки'),
                  onTap: () {},
                ),
                const Divider(),
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.logout, color: AppColors.accent),
                  title: const Text('Выйти'),
                  onTap: controller.isLoading ? null : () => controller.logout(),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
