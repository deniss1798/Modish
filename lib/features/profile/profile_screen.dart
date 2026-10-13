import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/presentation/catalog_labels.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';
import '../catalog/brands_screen.dart';
import '../catalog/search_screen.dart';
import '../settings/settings_screen.dart';
import 'edit_profile_screen.dart';

class ProfileScreen extends StatelessWidget {
  const ProfileScreen({super.key, required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final fit = controller.fitProfile;
    final taste = controller.tasteProfile;
    final weights = taste['weights'] as Map? ?? const {};
    final colors = _values(
      _positiveWeights(weights['colors']),
      catalogColorLabels,
    );
    final learnedStyles = _values(
      _positiveWeights(weights['styles']),
      _styleLabels,
    );
    final chosenStyles = _values(
      controller.activationStatus['style_preferences'],
      _styleLabels,
    );
    final scenarios = _values(fit['style_scenarios'], _styleLabels);
    final size = (fit['clothing_size'] ?? 'Не указан').toString();
    final budgetMax = fit['budget_max'];
    final budgetLabel = budgetMax != null ? 'до $budgetMax ₽' : 'Не указан';
    final name = controller.email.split('@').first;

    final feedCount = controller.productFeed.length;
    final savedCount =
        controller.savedProductRows.length + controller.savedOutfits.length;

    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 24),
      children: [
        const EditorialHeading('Профиль'),
        const SizedBox(height: 12),
        SoftCard(
          child: Row(
            children: [
              CircleAvatar(
                radius: 32,
                backgroundColor: AppColors.chipBg,
                child: Text(
                  name.isNotEmpty ? name[0].toUpperCase() : '?',
                  style: AppTextStyles.displaySm,
                ),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Flexible(
                          child: Text(
                            name,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: AppTextStyles.displaySm.copyWith(
                              fontSize: 22,
                            ),
                          ),
                        ),
                      ],
                    ),
                    Text(
                      controller.email,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: AppTextStyles.bodyMuted,
                    ),
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
              StatTile(value: '$feedCount', label: 'В ленте'),
              StatTile(value: '$savedCount', label: 'Сохранено'),
              StatTile(value: '${controller.outfits.length}', label: 'Образов'),
            ],
          ),
        ),
        const SizedBox(height: 14),
        SoftCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'Ваши предпочтения',
                style: AppTextStyles.sectionTitle,
              ),
              const SizedBox(height: 8),
              const Text(
                'Эта сводка помогает объяснить подбор. Она уточняется по вашим оценкам вещей.',
                style: AppTextStyles.bodyMuted,
              ),
              if (chosenStyles.isNotEmpty)
                _PreferenceLine(
                  label: 'Вы выбрали при знакомстве',
                  values: chosenStyles,
                ),
              if (learnedStyles.isNotEmpty)
                _PreferenceLine(
                  label: 'Стили по вашим оценкам',
                  values: learnedStyles,
                ),
              if (colors.isNotEmpty)
                _PreferenceLine(
                  label: 'Цвета по вашим оценкам',
                  values: colors,
                ),
              if (scenarios.isNotEmpty)
                _PreferenceLine(
                  label: 'В параметрах подборки',
                  values: scenarios,
                ),
              if (learnedStyles.isEmpty && colors.isEmpty) ...[
                const SizedBox(height: 14),
                const Text(
                  'Пока мало данных. Отмечайте понравившиеся вещи — здесь появятся ваши предпочтения.',
                  style: AppTextStyles.bodyMuted,
                ),
              ],
              const SizedBox(height: 18),
              Row(
                children: [
                  Expanded(
                    child: _InfoBox(label: 'Размер', value: size),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: _InfoBox(label: 'Бюджет', value: budgetLabel),
                  ),
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
                icon: Icons.tune,
                title: 'Параметры подборки',
                subtitle: 'Рост, вес, пол, размер, бюджет',
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => EditProfileScreen(controller: controller),
                  ),
                ),
              ),
              const Divider(color: AppColors.line),
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
                icon: Icons.storefront_outlined,
                title: 'Бренды',
                onTap: () async {
                  final brand = await Navigator.push<String>(
                    context,
                    MaterialPageRoute(
                      builder: (_) => BrandsScreen(controller: controller),
                    ),
                  );
                  if (!context.mounted ||
                      brand == null ||
                      brand.trim().isEmpty) {
                    return;
                  }
                  Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => SearchScreen(
                        controller: controller,
                        initialQuery: brand,
                      ),
                    ),
                  );
                },
              ),
              const Divider(color: AppColors.line),
              MenuTile(
                icon: Icons.settings_outlined,
                title: 'Настройки',
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => SettingsScreen(controller: controller),
                  ),
                ),
              ),
              const Divider(color: AppColors.line),
              MenuTile(
                icon: Icons.auto_awesome_outlined,
                title: 'Анализ по фото (дополнительно)',
                subtitle: 'Улучшит цвета и стиль',
                onTap: controller.isLoading ? null : controller.goToReanalyze,
              ),
            ],
          ),
        ),
      ],
    );
  }
}

const _styleLabels = {
  'daily': 'Каждый день',
  'office': 'В офис',
  'evening': 'Вечер',
  'casual': 'Повседневный',
  'smart_casual': 'Smart casual',
  'minimal': 'Минимализм',
  'minimalism': 'Минимализм',
  'romantic': 'Романтичный',
  'elegant': 'Элегантный',
  'streetwear': 'Городской',
  'classic': 'Классический',
  'sport': 'Спортивный',
  'sporty': 'Спортивный',
  'boho': 'Бохо',
};
List<String> _values(dynamic raw, Map<String, String> labels) =>
    (raw is List ? raw : const [])
        .map((v) => labels['$v'.toLowerCase()] ?? '$v')
        .where((v) => v.trim().isNotEmpty)
        .toSet()
        .take(5)
        .toList();

List<String> _positiveWeights(dynamic raw) {
  if (raw is! Map) return const [];
  final entries =
      raw.entries.where((e) => (num.tryParse('${e.value}') ?? 0) > 0).toList()
        ..sort(
          (a, b) => (num.tryParse('${b.value}') ?? 0).compareTo(
            num.tryParse('${a.value}') ?? 0,
          ),
        );
  return entries.map((e) => '${e.key}').toList();
}

class _PreferenceLine extends StatelessWidget {
  const _PreferenceLine({required this.label, required this.values});
  final String label;
  final List<String> values;
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(top: 14),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: AppTextStyles.caption),
        const SizedBox(height: 5),
        Text(values.join(' · '), style: AppTextStyles.body),
      ],
    ),
  );
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
