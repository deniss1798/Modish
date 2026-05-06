import 'package:flutter/material.dart';
import '../../app.dart';
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
      child: _ProductCardView(card: card),
    );
  }
}

class _ProductCardView extends StatelessWidget {
  const _ProductCardView({required this.card});
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
                ],
              ),
            ),
          ),
        ],
      ),
    );
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
