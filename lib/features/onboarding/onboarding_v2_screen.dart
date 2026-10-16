import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/presentation/outfit_recommendations.dart';
import '../../core/presentation/price_format.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';
import '../../core/widgets/product_image.dart';
import '../outfits/outfits_screen.dart';
import '../catalog/discovery_screen.dart';

/// Preferences → real catalogue Style Swipe → verified Wow outfits.
class OnboardingV2Screen extends StatefulWidget {
  const OnboardingV2Screen({super.key, required this.controller});
  final AppController controller;
  @override
  State<OnboardingV2Screen> createState() => _OnboardingV2ScreenState();
}

class _OnboardingV2ScreenState extends State<OnboardingV2Screen> {
  String? _gender;
  String? _age;
  String? _size;
  String? _shoe;
  final _height = TextEditingController();
  String _budget = 'mass';
  String? _unavailableId;
  final _styles = <String>{};
  static const _styleLabels = {
    'casual': 'Повседневный',
    'smart_casual': 'Smart casual',
    'minimalism': 'Минимализм',
    'romantic': 'Романтичный',
    'elegant': 'Элегантный',
    'streetwear': 'Городской',
  };

  @override
  void initState() {
    super.initState();
    final c = widget.controller;
    final prefs = c.activationStatus;
    final gender = prefs['gender_target'] ?? c.fitProfile['gender_target'];
    _gender = gender == 'womenswear' ? 'womenswear' : null;
    _age = prefs['age_group'] as String?;
    _size = prefs['clothing_size'] as String?;
    _shoe = prefs['shoe_size'] as String?;
    _height.text = '${prefs['height'] ?? ''}';
    _budget = ['economy', 'mass', 'mid'].contains(prefs['price_segment'])
        ? prefs['price_segment'] as String
        : 'mass';
    _styles.addAll(
      (prefs['style_preferences'] as List? ?? [])
          .map((v) => '$v')
          .where(_styleLabels.containsKey)
          .take(3),
    );
    c.addListener(_onChange);
    if (c.onboardingV2SubStep == 1 && c.styleSwipeCards.isEmpty) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && !c.isLoading) c.loadStyleSwipe();
      });
    }
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onChange);
    _height.dispose();
    super.dispose();
  }

  void _onChange() {
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (didPop || c.isLoading) return;
        if (c.onboardingV2SubStep == 1) {
          c.editActivationPreferences();
        } else if (c.onboardingV2SubStep == 5) {
          c.finishOnboardingV2ToHome();
        } else {
          c.goToWelcome();
        }
      },
      child: MobileViewport(
        child: SafeArea(
          child: switch (c.onboardingV2SubStep) {
            1 => _swipe(c),
            4 => _progress(),
            5 => _wow(c),
            _ => _preferences(c),
          },
        ),
      ),
    );
  }

  Widget _error(AppController c) => c.error == null
      ? const SizedBox.shrink()
      : Padding(
          padding: const EdgeInsets.symmetric(vertical: 10),
          child: Text(c.error!, style: const TextStyle(color: AppColors.error)),
        );

  Widget _select(
    String label,
    String? selected,
    List<String> values,
    ValueChanged<String?> onChanged,
  ) => DropdownButtonFormField<String>(
    initialValue: values.contains(selected) ? selected : null,
    decoration: InputDecoration(labelText: label),
    items: values
        .map((s) => DropdownMenuItem(value: s, child: Text(s)))
        .toList(),
    onChanged: widget.controller.isLoading ? null : onChanged,
  );

  Widget _preferences(AppController c) => ListView(
    padding: const EdgeInsets.fromLTRB(22, 12, 22, 24),
    children: [
      Row(
        children: [
          IconButton(
            tooltip: 'Назад',
            onPressed: c.isLoading ? null : c.goToWelcome,
            icon: const Icon(Icons.arrow_back),
          ),
          const Spacer(),
          const Text('1 / 3 · О вас', style: AppTextStyles.caption),
        ],
      ),
      const SizedBox(height: 12),
      const Text(
        'Гардероб, который\nпохож на вас',
        style: AppTextStyles.screenTitle,
      ),
      const SizedBox(height: 12),
      const Text(
        'Женские образы на каждый день и smart casual. Несколько деталей — и перейдём к вашему вкусу.',
        style: AppTextStyles.bodyMuted,
      ),
      const SizedBox(height: 24),
      const Text('Для кого подбираем?', style: AppTextStyles.sectionTitle),
      const SizedBox(height: 6),
      const Text(
        'Сейчас доступна подборка женской одежды. Подтвердите ваш выбор.',
        style: AppTextStyles.bodyMuted,
      ),
      const SizedBox(height: 8),
      Align(
        alignment: Alignment.centerLeft,
        child: ChoiceChip(
          key: const ValueKey('onboarding-gender-womenswear'),
          label: const Text('Женское'),
          selected: _gender == 'womenswear',
          onSelected: c.isLoading
              ? null
              : (selected) =>
                    setState(() => _gender = selected ? 'womenswear' : null),
        ),
      ),
      const SizedBox(height: 16),
      TextField(
        key: const ValueKey('onboarding-height'),
        controller: _height,
        keyboardType: TextInputType.number,
        decoration: const InputDecoration(
          labelText: 'Рост (см)',
          hintText: 'Введите ваш рост',
        ),
        onChanged: (_) => setState(() {}),
      ),
      const SizedBox(height: 16),
      _select('Возраст', _age, const [
        '18-24',
        '25-34',
        '35-44',
        '45+',
      ], (v) => setState(() => _age = v)),
      const SizedBox(height: 16),
      LayoutBuilder(
        builder: (context, bounds) {
          final clothing = _select('Одежда (буквы / RU)', _size, const [
            'XXS',
            'XS',
            'S',
            'M',
            'L',
            'XL',
            'XXL',
            'XXXL',
            '40',
            '42',
            '44',
            '46',
            '48',
            '50',
            '52',
            '54',
            '56',
          ], (v) => setState(() => _size = v));
          final footwear = _select('Обувь (EU)', _shoe, const [
            '34',
            '35',
            '36',
            '37',
            '38',
            '39',
            '40',
            '41',
            '42',
          ], (v) => setState(() => _shoe = v));
          // Both systems remain readable on narrow phones and large text.
          if (bounds.maxWidth < 380 ||
              MediaQuery.textScalerOf(context).scale(12) > 14) {
            return Column(
              children: [clothing, const SizedBox(height: 16), footwear],
            );
          }
          return Row(
            children: [
              Expanded(child: clothing),
              const SizedBox(width: 12),
              Expanded(child: footwear),
            ],
          );
        },
      ),
      const SizedBox(height: 24),
      const Text('Бюджет на образ', style: AppTextStyles.sectionTitle),
      const SizedBox(height: 10),
      Wrap(
        spacing: 8,
        runSpacing: 8,
        children: [
          for (final option in [
            ('economy', 'До 4 000 ₽'),
            ('mass', 'До 10 000 ₽'),
            ('mid', 'До 25 000 ₽'),
          ])
            ChoiceChip(
              label: Text(option.$2),
              selected: _budget == option.$1,
              onSelected: c.isLoading
                  ? null
                  : (_) => setState(() => _budget = option.$1),
            ),
        ],
      ),
      const SizedBox(height: 24),
      const Text('Что вам ближе?', style: AppTextStyles.sectionTitle),
      const SizedBox(height: 6),
      const Text(
        'Выберите 2–3 направления. Дальше покажем настоящие вещи.',
        style: AppTextStyles.bodyMuted,
      ),
      const SizedBox(height: 10),
      Wrap(
        spacing: 8,
        runSpacing: 8,
        children: _styleLabels.entries
            .map(
              (entry) => FilterChip(
                label: Text(entry.value),
                selected: _styles.contains(entry.key),
                onSelected: c.isLoading
                    ? null
                    : (selected) => setState(() {
                        if (!selected) {
                          _styles.remove(entry.key);
                        } else if (_styles.length < 3) {
                          _styles.add(entry.key);
                        }
                      }),
              ),
            )
            .toList(),
      ),
      _error(c),
      const SizedBox(height: 24),
      PrimaryButton(
        label: c.isLoading ? 'Подбираем вещи…' : 'Настроить мой вкус',
        onPressed:
            c.isLoading ||
                _gender == null ||
                _age == null ||
                _size == null ||
                _shoe == null ||
                (int.tryParse(_height.text) ?? 0) < 50 ||
                (int.tryParse(_height.text) ?? 0) > 250 ||
                _styles.length < 2
            ? null
            : () => c.startStyleSwipe(
                genderTarget: _gender!,
                ageGroup: _age!,
                clothingSize: _size!,
                shoeSize: _shoe!,
                styles: _styles.toList(),
                priceSegment: _budget,
                height: int.parse(_height.text),
              ),
      ),
    ],
  );

  Widget _swipe(AppController c) {
    final p = c.styleSwipeCards.isEmpty ? null : c.styleSwipeCards.first;
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 8, 20, 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              IconButton(
                tooltip: 'Изменить параметры',
                onPressed: c.isLoading ? null : c.editActivationPreferences,
                icon: const Icon(Icons.arrow_back),
              ),
              const Spacer(),
              Text(
                '2 / 3 · Ваш вкус  ${c.styleSwipeRated}/${c.styleSwipeTarget}',
                style: AppTextStyles.caption,
              ),
            ],
          ),
          const SizedBox(height: 6),
          const Text(
            'Нравится такой стиль?',
            style: AppTextStyles.sectionTitle,
          ),
          const SizedBox(height: 10),
          LinearProgressIndicator(
            value: (c.styleSwipeRated / c.styleSwipeTarget).clamp(0, 1),
            color: AppColors.accent,
            backgroundColor: AppColors.line,
          ),
          const SizedBox(height: 14),
          Expanded(
            child: p == null
                ? Center(
                    child: SingleChildScrollView(
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(
                            c.styleSwipeRated >= c.styleSwipeMinimum
                                ? Icons.check_circle_outline
                                : Icons.checkroom_outlined,
                            size: 48,
                            color: AppColors.accent,
                          ),
                          const SizedBox(height: 16),
                          Text(
                            c.styleSwipeRated >= c.styleSwipeMinimum
                                ? 'Теперь мы лучше знаем ваш вкус'
                                : c.isLoading
                                ? 'Подбираем вещи…'
                                : 'Подходящих вещей пока мало',
                            textAlign: TextAlign.center,
                            style: AppTextStyles.sectionTitle,
                          ),
                          const SizedBox(height: 12),
                          if (c.styleSwipeRated < c.styleSwipeMinimum) ...[
                            const Text(
                              'Проверьте размеры или измените бюджет. Мы сохранили ваши оценки.',
                              textAlign: TextAlign.center,
                              style: AppTextStyles.bodyMuted,
                            ),
                            TextButton(
                              onPressed: c.isLoading ? null : c.loadStyleSwipe,
                              child: const Text('Попробовать ещё'),
                            ),
                            TextButton(
                              onPressed: c.isLoading
                                  ? null
                                  : c.editActivationPreferences,
                              child: const Text('Изменить параметры'),
                            ),
                            if (!c.isLoading &&
                                c.token?.isNotEmpty == true &&
                                c.supportsCatalogueDiscovery) ...[
                              TextButton.icon(
                                key: const ValueKey(
                                  'cold-swipe-open-catalogue',
                                ),
                                onPressed: () => Navigator.push(
                                  context,
                                  MaterialPageRoute(
                                    builder: (_) =>
                                        DiscoveryScreen(controller: c),
                                  ),
                                ),
                                icon: const Icon(Icons.storefront_outlined),
                                label: const Text('Посмотреть каталог'),
                              ),
                              const Text(
                                'Размеры нужно уточнять у магазина. Ваши оценки сохранены.',
                                textAlign: TextAlign.center,
                                style: AppTextStyles.bodyMuted,
                              ),
                            ],
                          ],
                        ],
                      ),
                    ),
                  )
                : GestureDetector(
                    onHorizontalDragEnd: c.isLoading || _unavailableId == p.id
                        ? null
                        : (details) {
                            final velocity = details.primaryVelocity ?? 0;
                            if (velocity.abs() > 180) {
                              c.rateStyleSwipe(
                                velocity > 0 ? 'like' : 'dislike',
                              );
                            }
                          },
                    child: SoftCard(
                      padding: const EdgeInsets.all(12),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Expanded(
                            child: SizedBox(
                              width: double.infinity,
                              child: ProductFillImage(
                                key: ValueKey(p.id),
                                imageUrl: p.imageUrl,
                                onUnavailable: () {
                                  if (mounted) {
                                    setState(() => _unavailableId = p.id);
                                  }
                                },
                                borderRadius: BorderRadius.circular(16),
                              ),
                            ),
                          ),
                          const SizedBox(height: 10),
                          Text(
                            p.brand,
                            style: AppTextStyles.caption,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                          const SizedBox(height: 4),
                          Text(
                            p.title,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(fontWeight: FontWeight.w600),
                          ),
                          const SizedBox(height: 6),
                          Text(
                            '${formatPriceAmount(p.price)} ₽',
                            style: const TextStyle(
                              fontWeight: FontWeight.w700,
                              color: AppColors.accentSoft,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
          ),
          if (c.error != null)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                c.error!,
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(color: AppColors.error, fontSize: 12),
              ),
            ),
          if (c.error != null && p != null)
            TextButton.icon(
              key: const ValueKey('refresh-style-swipe'),
              onPressed: c.isLoading ? null : c.loadStyleSwipe,
              icon: const Icon(Icons.refresh),
              label: const Text('Обновить подборку'),
            ),
          const SizedBox(height: 14),
          if (p != null && _unavailableId == p.id)
            PrimaryButton(
              label: 'Показать другую вещь',
              onPressed: c.isLoading
                  ? null
                  : () => c.rateStyleSwipe('unavailable'),
            ),
          if (p != null && _unavailableId != p.id)
            Row(
              children: [
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: c.isLoading
                        ? null
                        : () => c.rateStyleSwipe('dislike'),
                    icon: const Icon(Icons.close),
                    label: const Text('Не нравится'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: FilledButton.icon(
                    onPressed: c.isLoading
                        ? null
                        : () => c.rateStyleSwipe('like'),
                    icon: const Icon(Icons.favorite_border),
                    label: const Text('Нравится'),
                  ),
                ),
              ],
            ),
          if (c.styleSwipeCanContinue) ...[
            const SizedBox(height: 10),
            PrimaryButton(
              label: 'Показать мои образы',
              onPressed: c.isLoading ? null : c.onboardingV2CompleteWow,
            ),
          ] else
            Padding(
              padding: const EdgeInsets.only(top: 10),
              child: Text(
                c.styleSwipeRated < c.styleSwipeMinimum
                    ? 'Ещё ${c.styleSwipeMinimum - c.styleSwipeRated} оценок до завершения настройки вкуса'
                    : 'Найдите хотя бы одну вещь, которая нравится, или измените предпочтения.',
                textAlign: TextAlign.center,
                style: AppTextStyles.caption,
              ),
            ),
        ],
      ),
    );
  }

  Widget _progress() => const Center(
    child: Padding(
      padding: EdgeInsets.all(28),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          CircularProgressIndicator(color: AppColors.accent),
          SizedBox(height: 28),
          Text(
            'Собираем ваши первые образы',
            textAlign: TextAlign.center,
            style: AppTextStyles.screenTitle,
          ),
          SizedBox(height: 12),
          Text(
            'Учитываем оценки и проверяем сочетания, размеры и бюджет.',
            textAlign: TextAlign.center,
            style: AppTextStyles.bodyMuted,
          ),
        ],
      ),
    ),
  );

  Widget _wow(AppController c) => ListView(
    padding: const EdgeInsets.fromLTRB(22, 20, 22, 24),
    children: [
      const Text('3 / 3 · Ваши первые образы', style: AppTextStyles.caption),
      const SizedBox(height: 12),
      Text(
        c.wowOutfits.isEmpty
            ? 'Подходящие образы пока не найдены'
            : 'Уже больше похоже на вас',
        style: AppTextStyles.screenTitle,
      ),
      const SizedBox(height: 12),
      Text(
        c.wowOutfits.isEmpty
            ? '$noOutfitRecommendationsMessage Можно перейти к ленте отдельных вещей.'
            : 'Вещи сочетаются между собой и проходят ваши условия. Нажмите на образ, чтобы рассмотреть его.',
        style: AppTextStyles.bodyMuted,
      ),
      const SizedBox(height: 22),
      ...limitOutfitRecommendations(c.wowOutfits).map(
        (outfit) => Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            OutfitCard(
              outfit: outfit.apiData,
              controller: c,
              padding: EdgeInsets.zero,
            ),
            if (outfit.explanation?.isNotEmpty == true)
              Padding(
                padding: const EdgeInsets.fromLTRB(6, 10, 6, 0),
                child: Text(
                  outfit.explanation!,
                  style: AppTextStyles.bodyMuted,
                ),
              ),
            const SizedBox(height: 22),
          ],
        ),
      ),
      PrimaryButton(
        label: c.isLoading ? 'Загрузка…' : 'Открыть мою ленту',
        onPressed: c.isLoading ? null : c.finishOnboardingV2ToHome,
      ),
    ],
  );
}
