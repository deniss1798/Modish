import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';
import '../../core/widgets/product_image.dart';

class OutfitDetailScreen extends StatelessWidget {
  const OutfitDetailScreen({super.key, required this.outfit, required this.controller});
  final Map<String, dynamic> outfit;
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final title = (outfit['style_direction'] ?? 'Образ').toString();
    final reason = (outfit['reason'] ?? '').toString();
    final total = (outfit['total_price'] ?? 0).toString();
    final raw = outfit['products'];
    final pmap = raw is Map ? Map<String, dynamic>.from(raw) : <String, dynamic>{};
    const slots = [('top', 'Верх'), ('bottom', 'Низ'), ('shoes', 'Обувь')];

    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        backgroundColor: AppColors.bg,
        elevation: 0,
        leading: IconButton(icon: const Icon(Icons.arrow_back), onPressed: () => Navigator.pop(context)),
        title: Text(title, style: AppTextStyles.sectionTitle),
      ),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Text(title, style: AppTextStyles.displaySm),
          const SizedBox(height: 6),
          Text(reason, style: AppTextStyles.bodyMuted),
          const SizedBox(height: 16),
          ...slots.map((slot) {
            final p = pmap[slot.$1];
            if (p is! Map) return const SizedBox.shrink();
            final m = Map<String, dynamic>.from(p);
            return Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: SoftCard(
                padding: const EdgeInsets.all(12),
                child: Row(
                  children: [
                    SizedBox(
                      width: 72,
                      height: 90,
                      child: ProductFillImage(
                        imageUrl: (m['image_url'] ?? '').toString(),
                        borderRadius: BorderRadius.circular(12),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(slot.$2, style: AppTextStyles.caption),
                          Text((m['title'] ?? 'Товар').toString(), maxLines: 2, overflow: TextOverflow.ellipsis),
                          Text('${m['price'] ?? ''} ₽', style: const TextStyle(fontWeight: FontWeight.w600)),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            );
          }),
          const SizedBox(height: 8),
          Text('Итого: $total ₽', style: AppTextStyles.price),
          const SizedBox(height: 16),
          PrimaryButton(
            label: 'Купить всё',
            icon: Icons.shopping_bag_outlined,
            onPressed: () async {
              for (final slot in slots) {
                final p = pmap[slot.$1];
                if (p is! Map) continue;
                final id = (p['id'] ?? '').toString();
                if (id.isEmpty) continue;
                try {
                  final res = await controller.api.affiliateClick(id);
                  final url = res['url']?.toString();
                  if (url != null && url.isNotEmpty) {
                    await launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
                  }
                } catch (_) {}
              }
              if (context.mounted) {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text('Открыты ссылки на товары образа')),
                );
              }
            },
          ),
          const SizedBox(height: 10),
          SecondaryButton(
            label: 'Сохранить образ',
            onPressed: controller.isLoading
                ? null
                : () => controller.saveOutfit('${outfit['id']}'),
          ),
        ],
      ),
    );
  }
}
