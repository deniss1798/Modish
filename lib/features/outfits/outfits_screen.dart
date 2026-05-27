import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';
import '../../core/widgets/product_image.dart';
import 'outfit_detail_screen.dart';

enum _OutfitScenario { all, daily, office, evening }

String _scenarioLabel(String key) {
  return switch (key.toLowerCase()) {
    'daily' => 'Каждый день',
    'office' => 'В офис',
    'evening' => 'Вечер',
    'casual' => 'Casual',
    'minimal' => 'Минимализм',
    _ => key,
  };
}

class OutfitsScreen extends StatefulWidget {
  const OutfitsScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<OutfitsScreen> createState() => _OutfitsScreenState();
}

class _OutfitsScreenState extends State<OutfitsScreen> {
  _OutfitScenario _scenario = _OutfitScenario.all;

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_onCtrl);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onCtrl);
    super.dispose();
  }

  void _onCtrl() => setState(() {});

  String? get _apiScenario => switch (_scenario) {
        _OutfitScenario.all => null,
        _OutfitScenario.daily => 'daily',
        _OutfitScenario.office => 'office',
        _OutfitScenario.evening => 'evening',
      };

  bool _matches(Map<String, dynamic> o) {
    if (_scenario == _OutfitScenario.all) return true;
    final dir = '${o['style_direction'] ?? ''}'.toLowerCase();
    return dir == _apiScenario;
  }

  Future<void> _generate() async {
    final scenario = _apiScenario ?? 'daily';
    await widget.controller.generateOutfitsV2(count: 3, scenario: scenario);
    if (!mounted) return;
    final err = widget.controller.error?.trim();
    if (err != null && err.isNotEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(err)));
      return;
    }
    final n = widget.controller.outfits.where(_matches).length;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          n > 0 ? 'Готово: $n ${_pluralOutfits(n)}' : 'Не хватило товаров в каталоге для образа',
        ),
      ),
    );
  }

  String _pluralOutfits(int n) {
    if (n % 10 == 1 && n % 100 != 11) return 'образ';
    if (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20)) return 'образа';
    return 'образов';
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    final outfits = c.outfits.where(_matches).toList();
    final busy = c.isLoading;

    return ListView(
      padding: const EdgeInsets.only(bottom: 24),
      children: [
        const Padding(
          padding: EdgeInsets.fromLTRB(20, 16, 20, 4),
          child: Text('Образы', style: AppTextStyles.screenTitle),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
          child: Text(
            _scenario == _OutfitScenario.all
                ? 'В «Все» видны образы разных сценариев — нажмите «Обновить» внутри «В офис» или «Вечер», чтобы собрать отдельные луки'
                : 'Готовые луки — верх, низ, обувь. Нажмите «Обновить образы», чтобы пересобрать этот сценарий',
            style: AppTextStyles.bodyMuted,
          ),
        ),
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: 20),
          child: Row(
            children: [
              for (final chip in [
                ('Все', _OutfitScenario.all),
                ('Каждый день', _OutfitScenario.daily),
                ('В офис', _OutfitScenario.office),
                ('Вечер', _OutfitScenario.evening),
              ])
                ModishChip(
                  label: chip.$1,
                  selected: _scenario == chip.$2,
                  onTap: () => setState(() => _scenario = chip.$2),
                ),
            ],
          ),
        ),
        const SizedBox(height: 16),
        if (outfits.isEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 20),
            child: EmptyState(
              icon: Icons.checkroom_outlined,
              title: 'Пока нет образов',
              message: 'Соберём лук из подборки для сценария «${_scenarioLabel(_apiScenario ?? 'daily')}».',
              actionLabel: 'Собрать образы',
              busy: busy,
              onAction: _generate,
            ),
          )
        else ...[
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
            child: PrimaryButton(
              label: busy ? 'Собираем…' : 'Обновить образы',
              icon: Icons.auto_awesome_outlined,
              onPressed: busy ? null : _generate,
            ),
          ),
          ...outfits.map((o) => _OutfitCard(outfit: o, controller: c)),
        ],
      ],
    );
  }
}

class _OutfitCard extends StatelessWidget {
  const _OutfitCard({required this.outfit, required this.controller});
  final Map<String, dynamic> outfit;
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final scenario = '${outfit['style_direction'] ?? ''}';
    final title = _scenarioLabel(scenario);
    final total = (outfit['total_price'] ?? 0).toString();
    final saved = outfit['is_saved'] == true;
    final pmap = outfit['products'] is Map
        ? Map<String, dynamic>.from(outfit['products'] as Map)
        : <String, dynamic>{};
    final slots = [
      ('top', Icons.checkroom_outlined, 'Верх'),
      ('bottom', Icons.view_week_outlined, 'Низ'),
      ('shoes', Icons.ice_skating_outlined, 'Обувь'),
      ('accessory', Icons.shopping_bag_outlined, 'Аксессуар'),
    ];
    final filled = slots.where((s) => pmap[s.$1] is Map).length;

    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
      child: SoftCard(
        padding: const EdgeInsets.all(14),
        child: InkWell(
          onTap: () => Navigator.push(
            context,
            MaterialPageRoute(
              builder: (_) => OutfitDetailScreen(outfit: outfit, controller: controller),
            ),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Text(title, style: AppTextStyles.sectionTitle),
                  const Spacer(),
                  if (saved)
                    const Icon(Icons.bookmark, color: AppColors.accent, size: 18),
                ],
              ),
              const SizedBox(height: 4),
              Text('$filled из 4 · $total ₽', style: AppTextStyles.caption),
              const SizedBox(height: 12),
              SizedBox(
                height: 168,
                child: Row(
                  children: [
                    for (var i = 0; i < slots.length; i++)
                      Expanded(
                        child: Padding(
                          padding: EdgeInsets.only(right: i == slots.length - 1 ? 0 : 6),
                          child: _OutfitSlotImage(
                            imageUrl: _slotImageUrl(pmap, slots[i].$1),
                            icon: slots[i].$2,
                            label: slots[i].$3,
                          ),
                        ),
                      ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

String _slotImageUrl(Map<String, dynamic> pmap, String slot) {
  final raw = pmap[slot];
  if (raw is! Map) return '';
  return '${raw['image_url'] ?? ''}';
}

class _OutfitSlotImage extends StatelessWidget {
  const _OutfitSlotImage({
    required this.imageUrl,
    required this.icon,
    required this.label,
  });
  final String imageUrl;
  final IconData icon;
  final String label;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Expanded(
          child: imageUrl.trim().isEmpty
              ? Container(
                  decoration: BoxDecoration(
                    color: AppColors.bg,
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: AppColors.line),
                  ),
                  child: Icon(icon, color: AppColors.muted, size: 28),
                )
              : ProductFillImage(
                  imageUrl: imageUrl,
                  borderRadius: BorderRadius.circular(8),
                ),
        ),
        const SizedBox(height: 4),
        Text(label, style: AppTextStyles.caption.copyWith(fontSize: 9), maxLines: 1),
      ],
    );
  }
}
