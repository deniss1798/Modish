import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../app.dart';
import '../../core/network/api_client.dart';
import '../../core/theme/app_colors.dart';
import '../../core/widgets/modish_widgets.dart';
import '../../core/widgets/product_image.dart';
import 'models.dart' as prod;

class ProductDetailScreen extends StatelessWidget {
  const ProductDetailScreen({
    super.key,
    required this.controller,
    required this.card,
  });

  final AppController controller;
  final prod.FeedCard card;

  @override
  Widget build(BuildContext context) {
    final p = card.product;
    final sizes = p.availableSizes.isEmpty ? '—' : p.availableSizes.take(12).join(', ');
    final colors = p.colors.isEmpty ? '—' : p.colors.take(12).join(', ');
    return Scaffold(
      backgroundColor: AppColors.bg,
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 20),
          children: [
            Row(
              children: [
                IconButton(
                  onPressed: () => Navigator.pop(context),
                  icon: const Icon(Icons.arrow_back),
                ),
                const Spacer(),
                const Brand(size: 40),
                const Spacer(),
                const SizedBox(width: 48),
              ],
            ),
            SoftCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SizedBox(
                    height: 280,
                    width: double.infinity,
                    child: ProductFillImage(
                      imageUrl: p.imageUrl,
                      borderRadius: BorderRadius.circular(18),
                    ),
                  ),
                  const SizedBox(height: 12),
                  Text(
                    p.title.isEmpty ? 'Товар' : p.title,
                    style: const TextStyle(fontFamily: 'Georgia', fontSize: 28, color: AppColors.ink),
                  ),
                  const SizedBox(height: 6),
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      Expanded(
                        child: Text(
                          p.brand,
                          style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 16, color: AppColors.ink),
                        ),
                      ),
                      if (p.oldPrice != null && p.oldPrice! > p.price) ...[
                        Padding(
                          padding: const EdgeInsets.only(right: 8, bottom: 2),
                          child: Text(
                            '${p.oldPrice} ${p.currency}',
                            style: const TextStyle(
                              color: AppColors.muted,
                              decoration: TextDecoration.lineThrough,
                              fontSize: 15,
                            ),
                          ),
                        ),
                      ],
                      Text(
                        '${p.price} ${p.currency}',
                        style: const TextStyle(
                          fontFamily: 'Georgia',
                          fontSize: 22,
                          fontWeight: FontWeight.w600,
                          color: AppColors.accent,
                        ),
                      ),
                    ],
                  ),
                  if (p.discountPercent != null && p.discountPercent! > 0) ...[
                    const SizedBox(height: 4),
                    Text(
                      '−${p.discountPercent}%',
                      style: const TextStyle(color: AppColors.accent, fontWeight: FontWeight.w600),
                    ),
                  ],
                  const SizedBox(height: 10),
                  Text('Категория: ${p.category}', style: const TextStyle(color: AppColors.muted)),
                  const SizedBox(height: 8),
                  Text('Размеры: $sizes', style: const TextStyle(color: AppColors.muted)),
                  const SizedBox(height: 6),
                  Text('Цвета: $colors', style: const TextStyle(color: AppColors.muted)),
                  const Divider(height: 26),
                  const Text('Почему рекомендовано', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
                  const SizedBox(height: 8),
                  Text(card.reason, style: const TextStyle(height: 1.35)),
                  const SizedBox(height: 16),
                  FilledButton.icon(
                    onPressed: controller.isLoading
                        ? null
                        : () => _openAffiliateShop(context, controller, p.id),
                    icon: const Icon(Icons.storefront_outlined),
                    label: const Padding(
                      padding: EdgeInsets.symmetric(vertical: 4),
                      child: Text('Перейти в магазин', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
                    ),
                    style: FilledButton.styleFrom(
                      backgroundColor: AppColors.accent,
                      foregroundColor: Colors.white,
                      minimumSize: const Size(double.infinity, 48),
                    ),
                  ),
                  const SizedBox(height: 12),
                  Row(
                    children: [
                      Expanded(
                        child: FilledButton.icon(
                          onPressed: controller.isLoading
                              ? null
                              : () => controller.sendProductEvent(context, p.id, 'like'),
                          icon: const Icon(Icons.favorite_border),
                          label: const Text('Like'),
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: OutlinedButton.icon(
                          onPressed: controller.isLoading
                              ? null
                              : () => controller.sendProductEvent(context, p.id, 'dislike'),
                          icon: const Icon(Icons.thumb_down_outlined),
                          label: const Text('Dislike'),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  OutlinedButton.icon(
                    onPressed: controller.isLoading
                        ? null
                        : () => controller.sendProductEvent(context, p.id, 'save'),
                    icon: const Icon(Icons.bookmark_border),
                    label: const Text('Сохранить'),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'Витрина: ${p.outboundUrl}',
                    style: const TextStyle(color: AppColors.muted, fontSize: 11),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

Future<void> _openAffiliateShop(BuildContext context, AppController controller, String productId) async {
  try {
    final res = await controller.api.affiliateClick(productId);
    final url = res['url']?.toString();
    if (url == null || url.isEmpty) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Нет ссылки магазина')),
        );
      }
      return;
    }
    final uri = Uri.parse(url);
    if (!await canLaunchUrl(uri)) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Ссылку нельзя открыть на этом устройстве')),
        );
      }
      return;
    }
    final ok = await launchUrl(uri, mode: LaunchMode.externalApplication);
    if (!ok && context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Не удалось открыть браузер')),
      );
      return;
    }
    if (context.mounted) {
      try {
        await controller.api.recordRecommendationEvent(
          eventType: 'buy_click',
          productId: productId,
        );
      } catch (_) {}
      if (!context.mounted) return;
      await controller.sendProductEvent(context, productId, 'open_product');
    }
  } catch (e) {
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(ApiClient.formatError(e))),
      );
    }
  }
}
