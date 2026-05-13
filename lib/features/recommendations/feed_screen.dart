import 'dart:async';

import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';
import '../../core/widgets/product_image.dart';
import '../catalog/filters_screen.dart';
import '../catalog/search_screen.dart';
import '../products/models.dart' as prod;
import '../products/product_detail_screen.dart';

class FeedScreen extends StatefulWidget {
  const FeedScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<FeedScreen> createState() => _FeedScreenState();
}

class _FeedScreenState extends State<FeedScreen> {
  String? _lastViewRecordedId;

  @override
  Widget build(BuildContext context) {
    final controller = widget.controller;
    final card = controller.currentProduct;
    final id = card?.product.id;
    if (id != null && id != _lastViewRecordedId) {
      _lastViewRecordedId = id;
      unawaited(controller.recordProductViewIfNeeded(id));
    }
    if (id == null) {
      _lastViewRecordedId = null;
    }
    final related = id == null
        ? <prod.FeedCard>[]
        : controller.relatedProducts(id);

    return Column(
      children: [
        ScreenHeader(
          showBrand: true,
          title: 'Подборка для вас',
          subtitle:
              'Сегодня подобрали ${controller.filteredProductFeed.length} вещей',
          trailing: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              IconButton(
                icon: const Icon(Icons.search),
                onPressed: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => SearchScreen(controller: controller),
                  ),
                ),
              ),
              IconButton(
                icon: const Icon(Icons.tune),
                onPressed: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => FiltersScreen(controller: controller),
                  ),
                ),
              ),
            ],
          ),
        ),
        Expanded(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 20),
            child: card == null
                ? _EmptyFeed(controller: controller)
                : _SwipeProductCard(controller: controller, card: card),
          ),
        ),
        if (card != null && related.isNotEmpty) ...[
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 0, 20, 8),
            child: Align(
              alignment: Alignment.centerLeft,
              child: Text('С чем носить', style: AppTextStyles.sectionTitle),
            ),
          ),
          SizedBox(
            height: 120,
            child: ListView.separated(
              padding: const EdgeInsets.symmetric(horizontal: 20),
              scrollDirection: Axis.horizontal,
              itemCount: related.length,
              separatorBuilder: (context, index) => const SizedBox(width: 10),
              itemBuilder: (context, i) {
                final r = related[i].product;
                return GestureDetector(
                  onTap: () => _openProduct(context, related[i], controller),
                  child: SizedBox(
                    width: 90,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Expanded(
                          child: ProductFillImage(
                            imageUrl: r.imageUrl,
                            borderRadius: BorderRadius.circular(12),
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          r.brand,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: AppTextStyles.caption,
                        ),
                        Text(
                          '${r.price} ₽',
                          style: const TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ],
                    ),
                  ),
                );
              },
            ),
          ),
        ],
        if (card != null)
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 8, 20, 12),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceEvenly,
              children: [
                _ActionBtn(
                  icon: Icons.close,
                  onTap: () => controller.sendProductEvent(
                    context,
                    card.product.id,
                    'skip',
                  ),
                ),
                _ActionBtn(
                  icon: Icons.bookmark_border,
                  onTap: () => controller.sendProductEvent(
                    context,
                    card.product.id,
                    'save',
                  ),
                ),
                _ActionBtn(
                  icon: Icons.info_outline,
                  onTap: () => _openProduct(context, card, controller),
                ),
                _ActionBtn(
                  icon: Icons.favorite_border,
                  onTap: () => controller.sendProductEvent(
                    context,
                    card.product.id,
                    'like',
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }
}

class _ActionBtn extends StatelessWidget {
  const _ActionBtn({required this.icon, required this.onTap});
  final IconData icon;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: AppColors.card,
      borderRadius: BorderRadius.circular(24),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(24),
        child: SizedBox(
          width: 52,
          height: 46,
          child: Icon(icon, color: AppColors.ink, size: 22),
        ),
      ),
    );
  }
}

class _EmptyFeed extends StatelessWidget {
  const _EmptyFeed({required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final err = controller.productFeedError?.trim();
    return Center(
      child: SoftCard(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              err != null && err.isNotEmpty
                  ? 'Не удалось загрузить ленту'
                  : 'Карточек пока нет',
              style: AppTextStyles.sectionTitle,
            ),
            const SizedBox(height: 8),
            Text(
              err != null && err.isNotEmpty
                  ? err
                  : 'Импортируйте каталог или обновите экран.',
              textAlign: TextAlign.center,
              style: AppTextStyles.bodyMuted,
            ),
            const SizedBox(height: 18),
            PrimaryButton(
              label: 'Обновить',
              icon: Icons.refresh,
              expanded: false,
              onPressed: controller.isLoading
                  ? null
                  : () => controller.refreshRemoteData(),
            ),
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
      onTap: () => _openProduct(context, card, controller),
      child: _ProductCardView(controller: controller, card: card),
    );
  }
}

class _FeedImageCarousel extends StatefulWidget {
  const _FeedImageCarousel({required this.urls, required this.borderRadius});
  final List<String> urls;
  final BorderRadius borderRadius;

  @override
  State<_FeedImageCarousel> createState() => _FeedImageCarouselState();
}

class _FeedImageCarouselState extends State<_FeedImageCarousel> {
  late final PageController _pageController;
  int _page = 0;

  @override
  void initState() {
    super.initState();
    _pageController = PageController();
  }

  @override
  void dispose() {
    _pageController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final urls = widget.urls;
    if (urls.isEmpty) {
      return ProductFillImage(imageUrl: '', borderRadius: widget.borderRadius);
    }
    if (urls.length == 1) {
      return ProductFillImage(
        imageUrl: urls.first,
        borderRadius: widget.borderRadius,
      );
    }
    return Column(
      children: [
        Expanded(
          child: ClipRRect(
            borderRadius: widget.borderRadius,
            child: PageView.builder(
              controller: _pageController,
              itemCount: urls.length,
              onPageChanged: (i) => setState(() => _page = i),
              itemBuilder: (context, i) {
                return ProductFillImage(
                  imageUrl: urls[i],
                  borderRadius: BorderRadius.zero,
                );
              },
            ),
          ),
        ),
        const SizedBox(height: 6),
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: List.generate(urls.length, (i) {
            final on = i == _page;
            return AnimatedContainer(
              duration: const Duration(milliseconds: 180),
              margin: const EdgeInsets.symmetric(horizontal: 2),
              width: on ? 14 : 5,
              height: 5,
              decoration: BoxDecoration(
                color: on ? AppColors.ink : AppColors.line,
                borderRadius: BorderRadius.circular(3),
              ),
            );
          }),
        ),
      ],
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
    final gallery = p.galleryUrls;
    final radius = BorderRadius.circular(8);
    final disc = p.discountPercent;
    final showDisc = disc != null && disc > 0;
    return SoftCard(
      padding: const EdgeInsets.all(8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Expanded(
            flex: 12,
            child: Stack(
              children: [
                Positioned.fill(
                  child: _FeedImageCarousel(
                    urls: gallery,
                    borderRadius: radius,
                  ),
                ),
                Positioned(
                  top: 10,
                  right: 10,
                  child: _RoundIcon(
                    icon: Icons.favorite_border,
                    onTap: () =>
                        controller.sendProductEvent(context, p.id, 'like'),
                  ),
                ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(4, 12, 4, 4),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  p.brand,
                  style: AppTextStyles.caption.copyWith(
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  p.title.isEmpty ? 'Товар' : p.title,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 15,
                    height: 1.25,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 8),
                Row(
                  children: [
                    Text(
                      '${p.price} ${p.currency}',
                      style: AppTextStyles.price.copyWith(fontSize: 16),
                    ),
                    if (p.oldPrice != null && p.oldPrice! > p.price) ...[
                      const SizedBox(width: 8),
                      Text(
                        '${p.oldPrice} ${p.currency}',
                        style: const TextStyle(
                          fontSize: 12,
                          color: AppColors.muted,
                          decoration: TextDecoration.lineThrough,
                        ),
                      ),
                    ],
                    if (showDisc) ...[
                      const SizedBox(width: 8),
                      Text(
                        '−$disc%',
                        style: const TextStyle(
                          color: AppColors.accent,
                          fontSize: 12,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                    ],
                  ],
                ),
                if (card.reason.trim().isNotEmpty) ...[
                  const SizedBox(height: 10),
                  Wrap(
                    spacing: 6,
                    runSpacing: 6,
                    children: card.reason
                        .split(',')
                        .take(3)
                        .map(
                          (r) => Container(
                            padding: const EdgeInsets.symmetric(
                              horizontal: 9,
                              vertical: 5,
                            ),
                            decoration: BoxDecoration(
                              color: AppColors.chipBg,
                              borderRadius: BorderRadius.circular(20),
                            ),
                            child: Text(
                              r.trim(),
                              style: AppTextStyles.caption.copyWith(
                                color: AppColors.ink,
                              ),
                            ),
                          ),
                        )
                        .toList(),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _RoundIcon extends StatelessWidget {
  const _RoundIcon({required this.icon, required this.onTap});
  final IconData icon;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.white.withValues(alpha: .9),
      shape: const CircleBorder(),
      child: InkWell(
        customBorder: const CircleBorder(),
        onTap: onTap,
        child: SizedBox(
          width: 40,
          height: 40,
          child: Icon(icon, size: 20, color: AppColors.ink),
        ),
      ),
    );
  }
}

void _openProduct(
  BuildContext context,
  prod.FeedCard card,
  AppController controller,
) {
  controller.sendProductEvent(context, card.product.id, 'open_product');
  Navigator.of(context).push(
    MaterialPageRoute(
      builder: (_) => ProductDetailScreen(controller: controller, card: card),
    ),
  );
}
