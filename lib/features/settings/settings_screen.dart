import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';

class SettingsScreen extends StatelessWidget {
  const SettingsScreen({super.key, required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        backgroundColor: AppColors.bg,
        elevation: 0,
        leading: IconButton(icon: const Icon(Icons.arrow_back), onPressed: () => Navigator.pop(context)),
        title: const Text('Настройки', style: AppTextStyles.sectionTitle),
      ),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Text('Аккаунт', style: AppTextStyles.caption.copyWith(fontWeight: FontWeight.w700)),
          const SizedBox(height: 8),
          SoftCard(
            child: Column(
              children: [
                MenuTile(icon: Icons.person_outline, title: 'Личные данные', subtitle: controller.email, onTap: () {}),
                const Divider(color: AppColors.line),
                MenuTile(icon: Icons.lock_outline, title: 'Безопасность', onTap: () {}),
              ],
            ),
          ),
          const SizedBox(height: 20),
          Text('Приложение', style: AppTextStyles.caption.copyWith(fontWeight: FontWeight.w700)),
          const SizedBox(height: 8),
          SoftCard(
            child: Column(
              children: [
                MenuTile(icon: Icons.notifications_outlined, title: 'Уведомления', onTap: () {}),
                const Divider(color: AppColors.line),
                MenuTile(icon: Icons.language, title: 'Язык', subtitle: 'Русский', onTap: () {}),
                const Divider(color: AppColors.line),
                MenuTile(icon: Icons.dark_mode_outlined, title: 'Тема', subtitle: 'Светлая', onTap: () {}),
              ],
            ),
          ),
          const SizedBox(height: 20),
          Text('Поддержка', style: AppTextStyles.caption.copyWith(fontWeight: FontWeight.w700)),
          const SizedBox(height: 8),
          SoftCard(
            child: Column(
              children: [
                MenuTile(icon: Icons.help_outline, title: 'Помощь', onTap: () {}),
                const Divider(color: AppColors.line),
                MenuTile(
                  icon: Icons.logout,
                  title: 'Выйти',
                  trailing: const SizedBox.shrink(),
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
