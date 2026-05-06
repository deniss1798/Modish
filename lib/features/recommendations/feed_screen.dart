import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../app.dart';
import '../../core/network/api_client.dart';
import '../../core/theme/app_colors.dart';
import '../../core/widgets/modish_widgets.dart';
import '../../core/widgets/product_image.dart';
import '../products/models.dart' as prod;
import '../products/product_detail_screen.dart';

class FeedScreen extends StatelessWidget {
  const FeedScreen({super.key, required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final card = controller.currentProduct;
    return SafeArea(
      child: Column(
        children: [
          const SizedBox(height: 22),
          const Brand(),
          const SizedBox(height: 10),
          const Text(
            'Подборка',
            style: TextStyle(fontSize: 26, color: AppColors.ink),
          ),
          const SizedBox(height: 6),
          const Text(
            'Свайп влево — пропустить · вправо — нравится',
            style: TextStyle(fontSize: 12, color: AppColors.muted),
          ),
          const SizedBox(height: 10),
          Expanded(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 18),
              child: card == null
                  ? _EmptyFeed(controller: controller)
                  : _SwipeProductCard(controller: controller, card: card),
            ),
          ),
          if (card != null)
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 8, 20, 18),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  IconButton.filledTonal(
                    onPressed: () => controller.sendProductEvent(context, card.product.id, 'skip'),
                    icon: const Icon(Icons.close),
                  ),
                  IconButton.filledTonal(
                    onPressed: () => controller.sendProductEvent(context, card.product.id, 'save'),
                    icon: const Icon(Icons.bookmark_border),
                  ),
                  IconButton.filledTonal(
                    onPressed: () => _openProduct(context, card, controller),
                    icon: const Icon(Icons.info_outline),
                  ),
                  IconButton.filledTonal(
                    onPressed: () => controller.sendProductEvent(context, card.product.id, 'like'),
                    icon: const Icon(Icons.favorite_border),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

class _EmptyFeed extends StatelessWidget {
  const _EmptyFeed({required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: SoftCard(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text(
              'Карточек пока нет',
              style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 8),
            const Text(
              'Импортируйте каталог и откройте ленту товаров.',
              textAlign: TextAlign.center,
              style: TextStyle(color: AppColors.muted, fontSize: 14),
            ),
            const SizedBox(height: 18),
            FilledButton.icon(
              onPressed: controller.isLoading
                  ? null
                  : () => controller.refreshRemoteData(),
              icon: const Icon(Icons.refresh),
              label: const Text('Обновить'),
            ),
            if (controller.error != null) ...[
              const SizedBox(height: 12),
              Text(
                controller.error!,
                textAlign: TextAlign.center,
                style: const TextStyle(color: AppColors.accent, fontSize: 13),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _SwipeProductCard extends StatelessWidget {
  const _SwipeProductCard({required this.controller, required this.card});
  final AppController controller;
  final prod.FeedCard card;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onHorizontalDragEnd: (details) {
        final v = details.primaryVelocity ?? 0;
        if (v > 280) {
          controller.sendProductEvent(context, card.product.id, 'like');
        } else if (v < -280) {
          controller.sendProductEvent(context, card.product.id, 'skip');
        }
      },
      child: _ProductCardView(controller: controller, card: card),
    );
  }
}

class _ProductCardView extends StatelessWidget {
  const _ProductCardView({required this.controller, required this.card});
  final AppController controller;
  final prod.FeedCard card;

  @override
  Widget build(BuildContext context) {
    final p = card.product;
    final palette = p.colors.isNotEmpty
        ? p.colors.take(4).map(prod.colorFromName).toList()
        : const [Color(0xFF142238), Colors.white, Color(0xFFCFCBC5)];
    final radius = BorderRadius.circular(18);
    return SoftCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Expanded(
            flex: 11,
            child: ProductFillImage(imageUrl: p.imageUrl, borderRadius: radius),
          ),
          const SizedBox(height: 14),
          Expanded(
            flex: 9,
            child: SingleChildScrollView(
              physics: const BouncingScrollPhysics(),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    p.title.isEmpty ? 'Товар' : p.title,
                    style: const TextStyle(
                      fontFamily: 'Georgia',
                      fontSize: 26,
                      height: 1.15,
                      color: AppColors.ink,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      Expanded(
                        child: Text(
                          p.brand,
                          style: const TextStyle(
                            fontWeight: FontWeight.w600,
                            fontSize: 15,
                            color: AppColors.ink,
                          ),
                        ),
                      ),
                      if (p.oldPrice != null && p.oldPrice! > p.price) ...[
                        Padding(
                          padding: const EdgeInsets.only(right: 8, bottom: 2),
                          child: Text(
                            '${p.oldPrice} ${p.currency}',
                            style: const TextStyle(
                              fontSize: 14,
                              color: AppColors.muted,
                              decoration: TextDecoration.lineThrough,
                            ),
                          ),
                        ),
                      ],
                      Text(
                        '${p.price} ${p.currency}',
                        style: const TextStyle(
                          fontFamily: 'Georgia',
                          fontSize: 20,
                          fontWeight: FontWeight.w600,
                          color: AppColors.accent,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 6),
                  Text(
                    p.category,
                    style: const TextStyle(color: AppColors.muted, fontSize: 13),
                  ),
                  if (p.availableSizes.isNotEmpty) ...[
                    const SizedBox(height: 6),
                    Text(
                      'Размеры: ${p.availableSizes.take(8).join(', ')}',
                      style: const TextStyle(color: AppColors.muted, fontSize: 13),
                    ),
                  ],
                  const SizedBox(height: 10),
                  Wrap(
                    spacing: 8,
                    runSpacing: 6,
                    children: [
                      ...palette.map(
                        (e) => CircleAvatar(backgroundColor: e, radius: 11),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  Container(
                    width: double.infinity,
                    padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
                    decoration: BoxDecoration(
                      color: AppColors.bg,
                      borderRadius: BorderRadius.circular(14),
                      border: Border.all(color: AppColors.line),
                    ),
                    child: Text(
                      card.reason,
                      style: const TextStyle(height: 1.4, fontSize: 14),
                    ),
                  ),
                  const SizedBox(height: 12),
                  FilledButton.icon(
                    onPressed: controller.isLoading
                        ? null
                        : () => _openAffiliateShop(context, controller, p.id),
                    icon: const Icon(Icons.storefront_outlined),
                    label: const Text('Перейти в магазин'),
                    style: FilledButton.styleFrom(
                      backgroundColor: AppColors.accent,
                      foregroundColor: Colors.white,
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
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
          const SnackBar(content: Text('Товар временно недоступен')),
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

void _openProduct(BuildContext context, prod.FeedCard card, AppController controller) {
  controller.sendProductEvent(context, card.product.id, 'open_product');
  Navigator.of(context).push(
    MaterialPageRoute(
      builder: (_) => ProductDetailScreen(controller: controller, card: card),
    ),
  );
}
