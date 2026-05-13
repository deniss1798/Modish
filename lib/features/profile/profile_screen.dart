import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';
import '../catalog/brands_screen.dart';
import '../settings/settings_screen.dart';
import '../subscription/plus_screen.dart';

class ProfileScreen extends StatelessWidget {
  const ProfileScreen({super.key, required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final b = controller.billing;
    final plus = b['is_plus_available'] == true || '${b['status']}' == 'active';
    final summary = controller.summary;
    final fit = controller.fitProfile;
    final suitableColors = List<String>.from(summary['suitable_colors'] as List? ?? const []);
    final styleLines = List<String>.from(summary['style_direction_human'] as List? ?? const []);
    final size = (fit['clothing_size'] ?? 'M').toString();
    final budgetMax = fit['budget_max'];
    final budgetLabel = budgetMax != null ? 'до $budgetMax ₽' : 'Средний';
    final name = controller.email.split('@').first;

    final feedCount = controller.productFeed.length;
    final savedCount = controller.savedProductRows.length + controller.savedRows.length;
    final viewsCount = controller.productFeed.length;

    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 24),
      children: [
        ScreenHeader(title: 'Профиль'),
        SoftCard(
          child: Row(
            children: [
              CircleAvatar(
                radius: 32,
                backgroundColor: AppColors.chipBg,
                child: Text(name.isNotEmpty ? name[0].toUpperCase() : '?', style: AppTextStyles.displaySm),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Text(name, style: AppTextStyles.displaySm.copyWith(fontSize: 22)),
                        if (plus) ...[
                          const SizedBox(width: 8),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                            decoration: BoxDecoration(
                              color: AppColors.ink,
                              borderRadius: BorderRadius.circular(10),
                            ),
                            child: const Text('Plus', style: TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.w600)),
                          ),
                        ],
                      ],
                    ),
                    Text(controller.email, style: AppTextStyles.bodyMuted),
                  ],
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 14),
        SoftCard(
          child: Row(
            children: [
              StatTile(value: '$feedCount', label: 'Подборки'),
              StatTile(value: '$savedCount', label: 'Сохранено'),
              StatTile(value: '$viewsCount', label: 'Просмотры'),
            ],
          ),
        ),
        const SizedBox(height: 14),
        SoftCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('Мой стиль', style: AppTextStyles.sectionTitle),
              const SizedBox(height: 14),
              Text('Цвета', style: AppTextStyles.caption.copyWith(fontWeight: FontWeight.w600)),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                children: (suitableColors.isEmpty ? ['Бежевый', 'Чёрный', 'Белый'] : suitableColors.take(5)).map((c) {
                  return Chip(label: Text(c), backgroundColor: AppColors.chipBg, side: BorderSide.none);
                }).toList(),
              ),
              const SizedBox(height: 14),
              Text('Предпочтения', style: AppTextStyles.caption.copyWith(fontWeight: FontWeight.w600)),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: (styleLines.isEmpty ? ['Минимализм', 'Casual'] : styleLines.take(6)).map((s) {
                  return Chip(label: Text(s), backgroundColor: AppColors.chipBg, side: BorderSide.none);
                }).toList(),
              ),
              const SizedBox(height: 14),
              Row(
                children: [
                  Expanded(child: _InfoBox(label: 'Размер', value: size)),
                  const SizedBox(width: 10),
                  Expanded(child: _InfoBox(label: 'Бюджет', value: budgetLabel)),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(height: 14),
        SoftCard(
          child: Column(
            children: [
              MenuTile(
                icon: Icons.style_outlined,
                title: 'Мои образы',
                onTap: () => controller.setTab(1),
              ),
              const Divider(color: AppColors.line),
              MenuTile(
                icon: Icons.bookmark_border,
                title: 'Сохранённое',
                onTap: () => controller.setTab(2),
              ),
              const Divider(color: AppColors.line),
              MenuTile(
                icon: Icons.history,
                title: 'История просмотров',
                onTap: () => controller.setTab(0),
              ),
              const Divider(color: AppColors.line),
              MenuTile(
                icon: Icons.storefront_outlined,
                title: 'Бренды',
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(builder: (_) => BrandsScreen(controller: controller)),
                ),
              ),
              const Divider(color: AppColors.line),
              MenuTile(
                icon: Icons.workspace_premium_outlined,
                title: 'Modish Plus',
                subtitle: '129 ₽ / месяц',
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(builder: (_) => PlusScreen(controller: controller)),
                ),
              ),
              const Divider(color: AppColors.line),
              MenuTile(
                icon: Icons.settings_outlined,
                title: 'Настройки',
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(builder: (_) => SettingsScreen(controller: controller)),
                ),
              ),
              const Divider(color: AppColors.line),
              MenuTile(
                icon: Icons.auto_awesome_outlined,
                title: 'Обновить фото-анализ',
                onTap: controller.isLoading ? null : controller.goToReanalyze,
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _InfoBox extends StatelessWidget {
  const _InfoBox({required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.bg,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.line),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: AppTextStyles.caption),
          const SizedBox(height: 4),
          Text(value, style: const TextStyle(fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }
}
