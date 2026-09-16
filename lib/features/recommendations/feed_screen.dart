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
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) unawaited(controller.loadRelatedProducts(id));
      });
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
          padding: const EdgeInsets.fromLTRB(20, 0, 12, 4),
          child: Row(
            children: [
              const GoldWordmark(fontSize: 26),
              const Spacer(),
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
                  : _SwipeProductCard(
                      key: ValueKey(card.product.id),
                      controller: controller,
                      card: card,
                    ),
            ),
          ),
        ),
        if (card != null && related.isNotEmpty) ...[
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 10, 20, 4),
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
            height: MediaQuery.sizeOf(context).height < 760 ? 84 : 104,
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
  return c.feedFilterCategories.isNotEmpty ||
      c.feedMinPrice != null ||
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
    final busy = controller.feedRefreshing;
    if (busy) {
      return const Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            CircularProgressIndicator(color: AppColors.accent, strokeWidth: 2),
            SizedBox(height: 24),
            Text(
              'Ищем следующие вещи',
              style: TextStyle(fontSize: 19, fontWeight: FontWeight.w600),
            ),
            SizedBox(height: 8),
            Text(
              'Подбираем варианты под ваш стиль',
              style: TextStyle(color: AppColors.muted),
            ),
          ],
        ),
      );
    }
    return Center(
      child: EmptyState(
        icon: err != null && err.isNotEmpty
            ? Icons.cloud_off_outlined
            : Icons.style_outlined,
        title: err != null && err.isNotEmpty
            ? 'Не удалось загрузить'
            : 'Подходящих вещей пока нет',
        message: err != null && err.isNotEmpty
            ? err
            : (_hasActiveFilters(controller)
                  ? 'Попробуйте убрать часть условий или сбросить фильтры.'
                  : 'Вы просмотрели доступные вещи. Попробуйте обновить ленту позже.'),
        actionLabel: _hasActiveFilters(controller)
            ? 'Сбросить фильтры'
            : 'Обновить',
        busy: busy,
        onAction: busy
            ? null
            : (_hasActiveFilters(controller)
                  ? () => controller.applyFeedFilters()
                  : onRefresh),
      ),
    );
  }
}

class _SwipeProductCard extends StatefulWidget {
  const _SwipeProductCard({
    super.key,
    required this.controller,
    required this.card,
  });
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
    final productId = widget.card.product.id;
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
    await widget.controller.sendProductEvent(context, productId, event);
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
                  fallbackImageUrls: urls,
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
    final currency = {'RUB', 'RUR'}.contains(p.currency) ? '₽' : p.currency;
    return SoftCard(
      padding: const EdgeInsets.all(10),
      child: LayoutBuilder(
        builder: (context, bounds) {
          final compact = bounds.maxHeight < 350;
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Expanded(
                child: Stack(
                  children: [
                    Positioned.fill(
                      child: _FeedImageCarousel(
                        urls: p.galleryUrls,
                        borderRadius: BorderRadius.circular(16),
                      ),
                    ),
                    if ((p.discountPercent ?? 0) > 0)
                      Positioned(
                        top: 10,
                        right: 10,
                        child: Container(
                          padding: const EdgeInsets.symmetric(
                            horizontal: 10,
                            vertical: 6,
                          ),
                          decoration: BoxDecoration(
                            color: AppColors.bg.withValues(alpha: 0.88),
                            borderRadius: BorderRadius.circular(20),
                          ),
                          child: Text(
                            '−${p.discountPercent}%',
                            style: const TextStyle(
                              color: AppColors.accentSoft,
                              fontSize: 12,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                        ),
                      ),
                  ],
                ),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(6, 12, 6, 4),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            p.brand.toUpperCase(),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              color: AppColors.accentSoft,
                              fontSize: 10,
                              letterSpacing: 1.8,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ),
                        const SizedBox(width: 8),
                        Flexible(
                          child: Text(
                            p.shopLabel,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: AppTextStyles.caption,
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 6),
                    Text(
                      p.title.isEmpty ? 'Товар' : p.title,
                      maxLines: compact ? 1 : 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        fontSize: 16,
                        height: 1.25,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        Text(
                          '${p.price} $currency',
                          style: AppTextStyles.price.copyWith(fontSize: 20),
                        ),
                        if (p.oldPrice != null && p.oldPrice! > p.price) ...[
                          const SizedBox(width: 10),
                          Text(
                            '${p.oldPrice} $currency',
                            style: const TextStyle(
                              fontSize: 12,
                              color: AppColors.muted,
                              decoration: TextDecoration.lineThrough,
                            ),
                          ),
                        ],
                        const Spacer(),
                        if (p.availableSizes.isNotEmpty && !compact)
                          Flexible(
                            child: Text(
                              p.availableSizes.take(3).join(' · '),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: AppTextStyles.caption,
                            ),
                          ),
                      ],
                    ),
                    if (!compact && card.reasons.isNotEmpty) ...[
                      const SizedBox(height: 9),
                      Row(
                        children: [
                          const Icon(
                            Icons.auto_awesome_outlined,
                            size: 13,
                            color: AppColors.accent,
                          ),
                          const SizedBox(width: 6),
                          Expanded(
                            child: Text(
                              card.reasons.first,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: AppTextStyles.caption,
                            ),
                          ),
                        ],
                      ),
                    ],
                  ],
                ),
              ),
            ],
          );
        },
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
