import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';
import '../../core/widgets/product_image.dart';
import 'outfit_detail_screen.dart';

enum _OutfitScenario { all, daily, office, evening, casual, minimal }

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
        _OutfitScenario.casual => 'casual',
        _OutfitScenario.minimal => 'minimal',
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
      SnackBar(content: Text(n > 0 ? 'Обновлено: $n образов' : 'Не удалось собрать образы — мало товаров в каталоге')),
    );
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    final outfits = c.outfits.where(_matches).toList();
    return ListView(
      padding: const EdgeInsets.only(bottom: 20),
      children: [
        ScreenHeader(
          title: 'Образы',
          subtitle: 'Верх · низ · обувь · аксессуар',
          trailing: c.isLoading
              ? const Padding(
                  padding: EdgeInsets.all(12),
                  child: SizedBox(
                    width: 22,
                    height: 22,
                    child: CircularProgressIndicator(strokeWidth: 2.2, color: AppColors.accent),
                  ),
                )
              : IconButton(
                  icon: const Icon(Icons.auto_awesome_outlined),
                  onPressed: _generate,
                ),
        ),
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: 20),
          child: Row(
            children: [
              for (final chip in [
                ('Для вас', _OutfitScenario.all),
                ('Каждый день', _OutfitScenario.daily),
                ('В офис', _OutfitScenario.office),
                ('Вечер', _OutfitScenario.evening),
                ('Casual', _OutfitScenario.casual),
                ('Минимализм', _OutfitScenario.minimal),
              ])
                ModishChip(
                  label: chip.$1,
                  selected: _scenario == chip.$2,
                  onTap: () => setState(() => _scenario = chip.$2),
                ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        if (outfits.isEmpty)
          Padding(
            padding: const EdgeInsets.all(20),
            child: SoftCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('Пока нет образов', style: AppTextStyles.sectionTitle),
                  const SizedBox(height: 8),
                  Text(
                    'Нажмите ✨ — соберём лук из вашей подборки (${_scenarioLabel(_apiScenario ?? 'daily')}).',
                    style: AppTextStyles.bodyMuted,
                  ),
                  const SizedBox(height: 14),
                  PrimaryButton(
                    label: 'Сгенерировать',
                    icon: Icons.auto_awesome_outlined,
                    expanded: false,
                    onPressed: c.isLoading ? null : _generate,
                  ),
                ],
              ),
            ),
          )
        else
          ...outfits.map((o) => _OutfitCard(outfit: o, controller: c)),
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
              const SizedBox(height: 12),
              Row(
                children: [
                  Expanded(child: Text(title, style: AppTextStyles.displaySm.copyWith(fontSize: 20))),
                  Icon(saved ? Icons.bookmark : Icons.bookmark_border, color: AppColors.accent),
                ],
              ),
              const SizedBox(height: 4),
              Text('$filled вещей · $total ₽', style: AppTextStyles.bodyMuted),
            ],
          ),
        ),
      ),
    );
  }
}

String _slotImageUrl(Map<String, dynamic> products, String slot) {
  final v = products[slot];
  if (v is! Map) return '';
  return (v['image_url'] ?? '').toString();
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
    final empty = imageUrl.trim().isEmpty;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Expanded(
          child: ClipRRect(
            borderRadius: BorderRadius.circular(8),
            child: empty
                ? DecoratedBox(
                    decoration: BoxDecoration(color: AppColors.chipBg),
                    child: Icon(icon, color: AppColors.muted, size: 22),
                  )
                : ProductFillImage(imageUrl: imageUrl, borderRadius: BorderRadius.circular(8)),
          ),
        ),
        const SizedBox(height: 4),
        Text(label, textAlign: TextAlign.center, style: AppTextStyles.caption, maxLines: 1),
      ],
    );
  }
}
