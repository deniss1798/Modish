import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';
import '../../core/widgets/product_image.dart';
import 'outfit_detail_screen.dart';

enum _OutfitScenario { all, daily, office, evening }

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

  bool _matches(Map<String, dynamic> o) {
    if (_scenario == _OutfitScenario.all) return true;
    final dir = '${o['style_direction'] ?? ''}'.toLowerCase();
    return switch (_scenario) {
      _OutfitScenario.daily => dir.contains('daily') || dir.contains('повсед'),
      _OutfitScenario.office => dir.contains('office') || dir.contains('офис'),
      _OutfitScenario.evening =>
        dir.contains('evening') || dir.contains('вечер'),
      _OutfitScenario.all => true,
    };
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
          subtitle: 'Подборки под ваш стиль и сценарий',
          trailing: IconButton(
            icon: const Icon(Icons.auto_awesome_outlined),
            onPressed: c.isLoading ? null : () => c.generateOutfitsV2(count: 3),
          ),
        ),
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: 20),
          child: Row(
            children: [
              ModishChip(
                label: 'Для вас',
                selected: _scenario == _OutfitScenario.all,
                onTap: () => setState(() => _scenario = _OutfitScenario.all),
              ),
              ModishChip(
                label: 'Каждый день',
                selected: _scenario == _OutfitScenario.daily,
                onTap: () => setState(() => _scenario = _OutfitScenario.daily),
              ),
              ModishChip(
                label: 'В офис',
                selected: _scenario == _OutfitScenario.office,
                onTap: () => setState(() => _scenario = _OutfitScenario.office),
              ),
              ModishChip(
                label: 'Вечер',
                selected: _scenario == _OutfitScenario.evening,
                onTap: () =>
                    setState(() => _scenario = _OutfitScenario.evening),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        if (outfits.isEmpty)
          const Padding(
            padding: EdgeInsets.all(20),
            child: SoftCard(
              child: Text(
                'Пока нет образов. Нажмите ✨, чтобы сгенерировать.',
                style: AppTextStyles.bodyMuted,
              ),
            ),
          )
        else
          ...outfits.map((o) {
            final title = (o['style_direction'] ?? 'Образ').toString();
            final total = (o['total_price'] ?? 0).toString();
            final saved = o['is_saved'] == true;
            final raw = o['products'];
            final pmap = raw is Map
                ? Map<String, dynamic>.from(raw)
                : <String, dynamic>{};
            final itemCount = pmap.length;
            return Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
              child: SoftCard(
                padding: const EdgeInsets.all(14),
                child: InkWell(
                  onTap: () => Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) =>
                          OutfitDetailScreen(outfit: o, controller: c),
                    ),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      SizedBox(
                        height: 180,
                        child: Row(
                          children: [
                            for (final slot in [
                              ('top', Icons.checkroom_outlined),
                              ('bottom', Icons.view_week_outlined),
                              ('shoes', Icons.ice_skating_outlined),
                            ])
                              Expanded(
                                child: Padding(
                                  padding: EdgeInsets.only(
                                    right: slot.$1 == 'shoes' ? 0 : 8,
                                  ),
                                  child: _OutfitSlotImage(
                                    imageUrl: _slotImageUrl(pmap, slot.$1),
                                    icon: slot.$2,
                                  ),
                                ),
                              ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 12),
                      Row(
                        children: [
                          Expanded(
                            child: Text(
                              title,
                              style: AppTextStyles.displaySm.copyWith(
                                fontSize: 20,
                              ),
                            ),
                          ),
                          Icon(
                            saved ? Icons.bookmark : Icons.bookmark_border,
                            color: AppColors.ink,
                          ),
                        ],
                      ),
                      const SizedBox(height: 4),
                      Text(
                        '$itemCount вещей · $total ₽',
                        style: AppTextStyles.bodyMuted,
                      ),
                    ],
                  ),
                ),
              ),
            );
          }),
      ],
    );
  }
}

String _slotImageUrl(Map<String, dynamic> products, String slot) {
  final v = products[slot];
  if (v is! Map) return '';
  return (v['image_url'] ?? '').toString();
}

class _OutfitSlotImage extends StatelessWidget {
  const _OutfitSlotImage({required this.imageUrl, required this.icon});
  final String imageUrl;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(8),
      child: Stack(
        fit: StackFit.expand,
        children: [
          ProductFillImage(
            imageUrl: imageUrl,
            borderRadius: BorderRadius.circular(8),
          ),
          if (_isGenericDemoUrl(imageUrl) || imageUrl.trim().isEmpty)
            DecoratedBox(
              decoration: BoxDecoration(color: AppColors.chipBg),
              child: Icon(icon, color: AppColors.muted, size: 28),
            ),
        ],
      ),
    );
  }
}

bool _isGenericDemoUrl(String raw) {
  final uri = Uri.tryParse(raw.trim());
  if (uri == null) return false;
  final host = uri.host.toLowerCase();
  return host.contains('picsum.photos') ||
      host.contains('placehold.co') ||
      host.contains('dummyimage.com');
}
