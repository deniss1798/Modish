import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';
import '../../core/widgets/product_image.dart';
import '../products/models.dart' as prod;
import '../products/product_detail_screen.dart';

class OutfitDetailScreen extends StatelessWidget {
  const OutfitDetailScreen({super.key, required this.outfit, required this.controller});
  final Map<String, dynamic> outfit;
  final AppController controller;

  static const _slots = [
    ('top', 'Верх'),
    ('bottom', 'Низ'),
    ('shoes', 'Обувь'),
    ('accessory', 'Аксессуар'),
  ];

  Future<void> _toggleSaved(BuildContext context) async {
    final id = '${outfit['id']}';
    final saved = outfit['is_saved'] == true;
    if (saved) {
      await controller.unsaveOutfit(id);
      if (context.mounted) Navigator.pop(context);
    } else {
      await controller.saveOutfit(id);
    }
  }

  void _openProduct(BuildContext context, Map<String, dynamic> m) {
    final product = prod.Product.fromApi(m);
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => ProductDetailScreen(
          controller: controller,
          card: prod.FeedCard(product: product, reason: ''),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final scenario = '${outfit['style_direction'] ?? 'Образ'}';
    final reason = (outfit['reason'] ?? '').toString();
    final total = (outfit['total_price'] ?? 0).toString();
    final saved = outfit['is_saved'] == true;
    final pmap = outfit['products'] is Map
        ? Map<String, dynamic>.from(outfit['products'] as Map)
        : <String, dynamic>{};

    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        backgroundColor: AppColors.bg,
        elevation: 0,
        leading: IconButton(icon: const Icon(Icons.arrow_back), onPressed: () => Navigator.pop(context)),
        actions: [
          IconButton(
            icon: Icon(saved ? Icons.bookmark : Icons.bookmark_border),
            tooltip: saved ? 'Убрать из сохранённого' : 'Сохранить образ',
            onPressed: controller.isLoading ? null : () => _toggleSaved(context),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 0, 20, 24),
        children: [
          Text(scenario, style: AppTextStyles.displaySm),
          const SizedBox(height: 6),
          Text(reason, style: AppTextStyles.bodyMuted),
          const SizedBox(height: 16),
          ..._slots.map((slot) {
            final p = pmap[slot.$1];
            if (p is! Map) return const SizedBox.shrink();
            final m = Map<String, dynamic>.from(p);
            return Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: SoftCard(
                padding: const EdgeInsets.all(12),
                child: InkWell(
                  borderRadius: BorderRadius.circular(8),
                  onTap: () => _openProduct(context, m),
                  child: Row(
                    children: [
                      SizedBox(
                        width: 76,
                        height: 96,
                        child: ProductFillImage(
                          imageUrl: (m['image_url'] ?? '').toString(),
                          borderRadius: BorderRadius.circular(10),
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(slot.$2, style: AppTextStyles.caption),
                            Text(
                              (m['title'] ?? 'Товар').toString(),
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(fontWeight: FontWeight.w600),
                            ),
                            const SizedBox(height: 4),
                            Text('${m['brand'] ?? ''} · ${m['price'] ?? ''} ₽', style: AppTextStyles.bodyMuted),
                          ],
                        ),
                      ),
                      const Icon(Icons.chevron_right, color: AppColors.muted),
                    ],
                  ),
                ),
              ),
            );
          }),
          Text('Итого: $total ₽', style: AppTextStyles.price),
          const SizedBox(height: 16),
          PrimaryButton(
            label: 'Купить всё',
            icon: Icons.shopping_bag_outlined,
            onPressed: () => _buyAll(context),
          ),
          const SizedBox(height: 10),
          SecondaryButton(
            label: saved ? 'Убрать из сохранённого' : 'Сохранить образ',
            onPressed: controller.isLoading ? null : () => _toggleSaved(context),
          ),
        ],
      ),
    );
  }

  Future<void> _buyAll(BuildContext context) async {
    final pmap = outfit['products'] is Map
        ? Map<String, dynamic>.from(outfit['products'] as Map)
        : <String, dynamic>{};
    var opened = 0;
    for (final slot in _slots) {
      final p = pmap[slot.$1];
      if (p is! Map) continue;
      final id = (p['id'] ?? '').toString();
      if (id.isEmpty) continue;
      try {
        final res = await controller.api.affiliateClick(id);
        final url = res['url']?.toString();
        if (url != null && url.isNotEmpty) {
          await launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
          opened++;
        }
      } catch (_) {}
    }
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(opened > 0 ? 'Открыто ссылок: $opened' : 'Ссылки недоступны')),
      );
    }
  }
}
