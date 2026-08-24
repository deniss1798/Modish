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
  void initState() {
    super.initState();
    widget.controller.addListener(_rebuild);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_rebuild);
    super.dispose();
  }

  void _rebuild() {
    if (mounted) setState(() {});
  }

  Future<void> _onRefresh() async {
    final msg = await widget.controller.refreshFeedFromUser();
    if (!mounted) return;
    if (msg != null) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
    } else {
      final n = widget.controller.filteredProductFeed.length;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('Обновлено: $n вещей')));
    }
  }

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
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 12, 12, 4),
          child: Row(
            children: [
              const GoldWordmark(fontSize: 26),
              const Spacer(),
              if (controller.filteredProductFeed.isNotEmpty)
                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 10,
                    vertical: 5,
                  ),
                  decoration: BoxDecoration(
                    color: AppColors.card,
                    borderRadius: BorderRadius.circular(20),
                    border: Border.all(color: AppColors.line),
                  ),
                  child: Text(
                    '${controller.filteredProductFeed.length}',
                    style: const TextStyle(
                      fontWeight: FontWeight.w700,
                      color: AppColors.accent,
                      fontSize: 13,
                    ),
                  ),
                ),
              IconButton(
                tooltip: 'Поиск',
                icon: const Icon(Icons.search, size: 22),
                onPressed: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => SearchScreen(controller: controller),
                  ),
                ),
              ),
              IconButton(
                tooltip: 'Фильтры',
                icon: Badge(
                  isLabelVisible: _hasActiveFilters(controller),
                  smallSize: 8,
                  child: const Icon(Icons.tune, size: 22),
                ),
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
        if (card != null)
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 0, 20, 6),
            child: Align(
              alignment: Alignment.centerLeft,
              child: Text(
                'Свайп вправо — нравится · влево — пропуск',
                style: AppTextStyles.caption.copyWith(fontSize: 10),
              ),
            ),
          ),
        Expanded(
          child: RefreshIndicator(
            color: AppColors.accent,
            onRefresh: _onRefresh,
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 20),
              child: card == null
                  ? ListView(
                      physics: const AlwaysScrollableScrollPhysics(),
                      children: [
                        SizedBox(
                          height: MediaQuery.sizeOf(context).height * 0.45,
                          child: _EmptyFeed(
                            controller: controller,
                            onRefresh: _onRefresh,
                          ),
                        ),
                      ],
                    )
                  : _SwipeProductCard(controller: controller, card: card),
            ),
          ),
        ),
        if (card != null && related.isNotEmpty) ...[
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 0, 20, 4),
            child: Align(
              alignment: Alignment.centerLeft,
              child: Text('С чем носить', style: AppTextStyles.sectionTitle),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 0, 20, 8),
            child: Align(
              alignment: Alignment.centerLeft,
              child: Text(
                controller.relatedProductsHint(id!),
                style: AppTextStyles.caption,
              ),
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
          FeedActionBar(
            onSkip: () =>
                controller.sendProductEvent(context, card.product.id, 'skip'),
            onDislike: () =>
                _showDislikeReasonSheet(context, controller, card.product.id),
            onSave: () =>
                controller.sendProductEvent(context, card.product.id, 'save'),
            onOpen: () => _openProduct(context, card, controller),
            onLike: () =>
                controller.sendProductEvent(context, card.product.id, 'like'),
          ),
      ],
    );
  }
}

bool _hasActiveFilters(AppController c) {
  return c.feedMinPrice != null ||
      c.feedMaxPrice != null ||
      c.feedFilterSizes.isNotEmpty ||
      c.feedFilterColors.isNotEmpty;
}

class _EmptyFeed extends StatelessWidget {
  const _EmptyFeed({required this.controller, required this.onRefresh});
  final AppController controller;
  final Future<void> Function() onRefresh;

  @override
  Widget build(BuildContext context) {
    final err = controller.productFeedError?.trim();
    final busy = controller.feedRefreshing || controller.isLoading;
    return Center(
      child: EmptyState(
        icon: err != null && err.isNotEmpty
            ? Icons.cloud_off_outlined
            : Icons.style_outlined,
        title: err != null && err.isNotEmpty
            ? 'Не удалось загрузить'
            : 'Пока пусто',
        message: err != null && err.isNotEmpty
            ? err
            : 'Подтянем вещи с сервера или подберём после импорта каталога.',
        actionLabel: 'Обновить',
        busy: busy,
        onAction: busy ? null : onRefresh,
      ),
    );
  }
}

class _SwipeProductCard extends StatefulWidget {
  const _SwipeProductCard({required this.controller, required this.card});
  final AppController controller;
  final prod.FeedCard card;

  @override
  State<_SwipeProductCard> createState() => _SwipeProductCardState();
}

class _SwipeProductCardState extends State<_SwipeProductCard>
    with SingleTickerProviderStateMixin {
  double _dx = 0;
  bool _animating = false;

  Future<void> _flyOut(String event) async {
    if (_animating) return;
    setState(() => _animating = true);
    final target = event == 'like' ? 420.0 : -420.0;
    final start = _dx;
    const steps = 12;
    for (var i = 1; i <= steps; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 16));
      if (!mounted) return;
      setState(() => _dx = start + (target - start) * (i / steps));
    }
    if (!mounted) return;
    await widget.controller.sendProductEvent(
      context,
      widget.card.product.id,
      event,
    );
    if (mounted) {
      setState(() {
        _dx = 0;
        _animating = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final angle = (_dx / 1000).clamp(-0.12, 0.12);
    final likeOpacity = (_dx / 120).clamp(0.0, 1.0);
    final skipOpacity = (-_dx / 120).clamp(0.0, 1.0);
    return GestureDetector(
      onHorizontalDragUpdate: _animating
          ? null
          : (d) => setState(() => _dx += d.delta.dx),
      onHorizontalDragEnd: _animating
          ? null
          : (d) {
              if (_dx > 90 || (d.primaryVelocity ?? 0) > 500) {
                _flyOut('like');
              } else if (_dx < -90 || (d.primaryVelocity ?? 0) < -500) {
                _flyOut('skip');
              } else {
                setState(() => _dx = 0);
              }
            },
      onTap: () => _openProduct(context, widget.card, widget.controller),
      onLongPress: () => _showDislikeReasonSheet(
        context,
        widget.controller,
        widget.card.product.id,
      ),
      child: Stack(
        alignment: Alignment.center,
        children: [
          Transform.translate(
            offset: Offset(_dx, 0),
            child: Transform.rotate(
              angle: angle,
              child: _ProductCardView(
                controller: widget.controller,
                card: widget.card,
              ),
            ),
          ),
          if (likeOpacity > 0.05)
            Positioned(
              left: 24,
              top: 40,
              child: Opacity(
                opacity: likeOpacity,
                child: const _SwipeStamp(
                  label: 'НРАВИТСЯ',
                  color: AppColors.success,
                ),
              ),
            ),
          if (skipOpacity > 0.05)
            Positioned(
              right: 24,
              top: 40,
              child: Opacity(
                opacity: skipOpacity,
                child: const _SwipeStamp(
                  label: 'ПРОПУСК',
                  color: AppColors.muted,
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _SwipeStamp extends StatelessWidget {
  const _SwipeStamp({required this.label, required this.color});
  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      decoration: BoxDecoration(
        border: Border.all(color: color, width: 2),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: color,
          fontWeight: FontWeight.w800,
          fontSize: 13,
        ),
      ),
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
                if (p.availableSizes.isNotEmpty) ...[
                  const SizedBox(height: 6),
                  Text(
                    'Размер: ${p.availableSizes.take(6).join(', ')}',
                    style: AppTextStyles.caption,
                  ),
                ],
                if (p.shopLabel.isNotEmpty) ...[
                  const SizedBox(height: 4),
                  Text(
                    p.shopLabel.toUpperCase(),
                    style: AppTextStyles.caption.copyWith(
                      color: AppColors.accent,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ],
                const SizedBox(height: 6),
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
                if (card.reasons.isNotEmpty ||
                    card.reason.trim().isNotEmpty) ...[
                  const SizedBox(height: 10),
                  Wrap(
                    spacing: 6,
                    runSpacing: 6,
                    children:
                        (card.reasons.isNotEmpty ? card.reasons : [card.reason])
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

Future<void> _showDislikeReasonSheet(
  BuildContext context,
  AppController controller,
  String productId,
) async {
  const reasons = [
    ('not_my_style', 'Не мой стиль'),
    ('dont_like_color', 'Не нравится цвет'),
    ('dont_like_fit', 'Не нравится посадка'),
    ('dont_like_category', 'Не нужна категория'),
    ('dont_like_brand', 'Не мой бренд'),
    ('too_expensive', 'Слишком дорого'),
    ('already_have_similar', 'Похожее уже есть'),
    ('dont_like_design', 'Не нравится дизайн'),
    ('other', 'Другая причина'),
  ];
  final reason = await showModalBottomSheet<String>(
    context: context,
    backgroundColor: AppColors.card,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
    ),
    builder: (sheetContext) {
      return SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Что не подошло?',
                style: AppTextStyles.sectionTitle.copyWith(fontSize: 18),
              ),
              const SizedBox(height: 10),
              ...reasons.map(
                (r) => ListTile(
                  contentPadding: EdgeInsets.zero,
                  dense: true,
                  title: Text(r.$2),
                  onTap: () => Navigator.pop(sheetContext, r.$1),
                ),
              ),
            ],
          ),
        ),
      );
    },
  );
  if (reason == null || !context.mounted) return;
  await controller.sendProductEvent(
    context,
    productId,
    'dislike',
    meta: {'reason': reason},
  );
}
