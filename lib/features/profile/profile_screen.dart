import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/presentation/price_format.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';
import '../catalog/brands_screen.dart';
import '../catalog/discovery_screen.dart';
import '../catalog/search_screen.dart';
import '../settings/settings_screen.dart';
import 'edit_profile_screen.dart';
import 'fit_sizes.dart';
import 'profile_preferences_screen.dart';

class ProfileScreen extends StatelessWidget {
  const ProfileScreen({super.key, required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: controller,
    builder: (context, _) {
      final email = controller.email.trim();
      final name = email.split('@').first;
      final preferences = ProfilePreferencesData.fromController(controller);
      final viewport = MediaQuery.sizeOf(context);
      final compactHeader = viewport.height < 500 && viewport.width >= 600;
      final settings = IconButton(
        tooltip: 'Настройки',
        onPressed: () => Navigator.push(
          context,
          MaterialPageRoute(
            builder: (_) => SettingsScreen(controller: controller),
          ),
        ),
        icon: const Icon(Icons.settings_outlined),
      );
      return Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 640),
          child: ListView(
            key: const ValueKey('profile-scroll'),
            padding: EdgeInsets.fromLTRB(20, compactHeader ? 8 : 16, 20, 24),
            children: [
              if (compactHeader)
                Row(
                  children: [
                    const Expanded(child: EditorialHeading('Профиль')),
                    const SizedBox(width: 24),
                    Expanded(
                      child: _AccountSummary(
                        name: name,
                        email: email,
                        compact: true,
                      ),
                    ),
                    settings,
                  ],
                )
              else ...[
                EditorialHeading('Профиль', trailing: settings),
                const SizedBox(height: 20),
                _AccountSummary(name: name, email: email),
              ],
              SizedBox(height: compactHeader ? 16 : 24),
              _SelectionCard(controller: controller),
              const SizedBox(height: 12),
              SoftCard(
                padding: const EdgeInsets.symmetric(
                  horizontal: 18,
                  vertical: 6,
                ),
                child: MenuTile(
                  key: const ValueKey('profile-preferences'),
                  icon: Icons.favorite_border,
                  title: 'Ваши предпочтения',
                  subtitle: preferences.hasLearned
                      ? 'Стили и цвета по вашим оценкам'
                      : preferences.hasAny
                      ? 'Ваш выбор и оценки вещей'
                      : 'Пока мало данных',
                  onTap: () => Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) =>
                          ProfilePreferencesScreen(controller: controller),
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 24),
              const Padding(
                padding: EdgeInsets.only(left: 4, bottom: 8),
                child: Text('Магазины', style: AppTextStyles.caption),
              ),
              SoftCard(
                padding: const EdgeInsets.symmetric(
                  horizontal: 18,
                  vertical: 6,
                ),
                child: Column(
                  children: [
                    if (controller.supportsCatalogueDiscovery) ...[
                      MenuTile(
                        icon: Icons.grid_view_outlined,
                        title: 'Каталог магазинов',
                        subtitle: 'Размер уточняйте у магазина',
                        onTap: () => Navigator.push(
                          context,
                          MaterialPageRoute(
                            builder: (_) =>
                                DiscoveryScreen(controller: controller),
                          ),
                        ),
                      ),
                      const Divider(color: AppColors.line, height: 1),
                    ],
                    MenuTile(
                      icon: Icons.storefront_outlined,
                      title: 'Бренды',
                      onTap: () async {
                        final session = controller.navigationSessionEpoch;
                        final brand = await Navigator.push<String>(
                          context,
                          MaterialPageRoute(
                            builder: (_) =>
                                BrandsScreen(controller: controller),
                          ),
                        );
                        if (!context.mounted ||
                            session != controller.navigationSessionEpoch ||
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
                  ],
                ),
              ),
              const SizedBox(height: 12),
              MenuTile(
                key: const ValueKey('profile-photo-analysis'),
                icon: Icons.add_a_photo_outlined,
                title: 'Анализ по фото',
                subtitle: 'По желанию',
                onTap: controller.isLoading ? null : controller.goToReanalyze,
              ),
            ],
          ),
        ),
      );
    },
  );
}

class _AccountSummary extends StatelessWidget {
  const _AccountSummary({
    required this.name,
    required this.email,
    this.compact = false,
  });
  final String name;
  final String email;
  final bool compact;

  @override
  Widget build(BuildContext context) => Row(
    children: [
      CircleAvatar(
        radius: compact ? 18 : 24,
        backgroundColor: AppColors.chipBg,
        child: name.isEmpty
            ? const Icon(Icons.person_outline, color: AppColors.accent)
            : Text(
                name[0].toUpperCase(),
                style: AppTextStyles.displaySm.copyWith(
                  fontSize: compact ? 20 : 26,
                ),
              ),
      ),
      const SizedBox(width: 12),
      Expanded(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              name.isEmpty ? 'Ваш аккаунт' : name,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: AppTextStyles.sectionTitle,
            ),
            if (email.isNotEmpty) ...[
              const SizedBox(height: 3),
              Text(
                email,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: AppTextStyles.caption,
              ),
            ],
          ],
        ),
      ),
    ],
  );
}

class _SelectionCard extends StatelessWidget {
  const _SelectionCard({required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final fit = controller.fitProfile;
    final sizes = <(String, String)>[
      ('Верх', sizeFieldValue(fit, 'top')),
      ('Низ', sizeFieldValue(fit, 'bottom')),
      (
        fit['gender_target'] == 'menswear' ? 'Комплект' : 'Платье',
        sizeFieldValue(fit, 'one_piece'),
      ),
      ('Обувь', sizeFieldValue(fit, 'shoes')),
    ].where((entry) => entry.$2.isNotEmpty).toList();
    // Legacy values describe only their own category, never every clothing slot.
    if (fit['size_profile'] == null) {
      final clothing = fit['clothing_size']?.toString().trim() ?? '';
      final shoe = fit['shoe_size']?.toString().trim() ?? '';
      if (clothing.isNotEmpty) sizes.add(('Одежда', clothing));
      if (shoe.isNotEmpty) sizes.add(('Обувь', shoe));
    }
    final budget = num.tryParse('${fit['budget_max']}');
    final budgetLabel = budget != null && budget.isFinite && budget > 0
        ? 'до ${formatPriceAmount(budget)} ₽'
        : 'Не указан';
    final wide = MediaQuery.sizeOf(context).width >= 600;
    return Material(
      color: Colors.transparent,
      child: InkWell(
        key: const ValueKey('profile-edit-parameters'),
        borderRadius: BorderRadius.circular(22),
        onTap: () => Navigator.push(
          context,
          MaterialPageRoute(
            builder: (_) => EditProfileScreen(controller: controller),
          ),
        ),
        child: SoftCard(
          padding: wide ? const EdgeInsets.all(16) : null,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  const Icon(Icons.tune, color: AppColors.accent, size: 22),
                  const SizedBox(width: 10),
                  const Expanded(
                    child: Text(
                      'Параметры подборки',
                      style: AppTextStyles.sectionTitle,
                    ),
                  ),
                  const SizedBox(width: 8),
                  const Icon(
                    Icons.edit_outlined,
                    color: AppColors.accent,
                    size: 20,
                  ),
                ],
              ),
              SizedBox(height: wide ? 12 : 18),
              if (sizes.isEmpty)
                const Text('Размеры не указаны', style: AppTextStyles.bodyMuted)
              else
                Wrap(
                  spacing: 24,
                  runSpacing: 12,
                  children: [
                    for (final size in sizes)
                      _ParameterValue(label: size.$1, value: size.$2),
                    if (wide)
                      _ParameterValue(label: 'Бюджет', value: budgetLabel),
                  ],
                ),
              if (!wide || sizes.isEmpty) ...[
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: 14),
                  child: Divider(color: AppColors.line, height: 1),
                ),
                _ParameterValue(label: 'Бюджет', value: budgetLabel),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _ParameterValue extends StatelessWidget {
  const _ParameterValue({required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Column(
    mainAxisSize: MainAxisSize.min,
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text(label, style: AppTextStyles.caption),
      const SizedBox(height: 4),
      Text(
        value,
        style: AppTextStyles.body.copyWith(fontWeight: FontWeight.w600),
      ),
    ],
  );
}
