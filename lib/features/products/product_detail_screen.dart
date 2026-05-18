import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../app.dart';
import '../../core/network/api_client.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';
import '../../core/widgets/product_image.dart';
import 'models.dart' as prod;

class ProductDetailScreen extends StatefulWidget {
  const ProductDetailScreen({
    super.key,
    required this.controller,
    required this.card,
  });

  final AppController controller;
  final prod.FeedCard card;

  @override
  State<ProductDetailScreen> createState() => _ProductDetailScreenState();
}

class _ProductDetailScreenState extends State<ProductDetailScreen> {
  late prod.FeedCard _card;
  bool _refreshing = true;
  String? _refreshError;
  String? _selectedSize;
  String? _selectedColor;

  @override
  void initState() {
    super.initState();
    _card = widget.card;
    _selectedSize = _card.product.availableSizes.isNotEmpty
        ? _card.product.availableSizes.first
        : null;
    _selectedColor = _card.product.colors.isNotEmpty
        ? _card.product.colors.first
        : null;
    _pullLatest();
  }

  Future<void> _pullLatest() async {
    setState(() {
      _refreshing = true;
      _refreshError = null;
    });
    try {
      final m = await widget.controller.api.productById(_card.product.id);
      if (!mounted) return;
      setState(() {
        _card = prod.FeedCard(
          product: prod.Product.fromApi(m),
          reason: _card.reason,
        );
        _refreshing = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _refreshError = ApiClient.formatError(e);
        _refreshing = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = _card.product;
    final gallery = p.galleryUrls;
    final sizes = p.availableSizes;
    final colors = p.colors;
    return Scaffold(
      backgroundColor: AppColors.bg,
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(0, 8, 0, 24),
          children: [
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: Row(
                children: [
                  IconButton(
                    onPressed: () => Navigator.pop(context),
                    icon: const Icon(Icons.arrow_back),
                  ),
                  const Spacer(),
                  IconButton(
                    onPressed: widget.controller.isLoading
                        ? null
                        : () => widget.controller.sendProductEvent(
                            context,
                            p.id,
                            'like',
                          ),
                    icon: const Icon(Icons.favorite_border),
                  ),
                  IconButton(
                    onPressed: _refreshing ? null : _pullLatest,
                    icon: _refreshing
                        ? const SizedBox(
                            width: 22,
                            height: 22,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.ios_share),
                  ),
                ],
              ),
            ),
            if (_refreshError != null)
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 0, 20, 8),
                child: Text(
                  _refreshError!,
                  style: const TextStyle(
                    color: AppColors.accentSoft,
                    fontSize: 13,
                  ),
                ),
              ),
            SizedBox(height: 360, child: _ProductImageCarousel(urls: gallery)),
            Transform.translate(
              offset: const Offset(0, -22),
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                child: SoftCard(
                  padding: const EdgeInsets.fromLTRB(18, 16, 18, 18),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        p.brand,
                        style: AppTextStyles.caption.copyWith(
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                      const SizedBox(height: 6),
                      Text(
                        p.title,
                        style: AppTextStyles.displaySm.copyWith(fontSize: 21),
                      ),
                      const SizedBox(height: 10),
                      Row(
                        children: [
                          Text(
                            '${p.price} ${p.currency}',
                            style: AppTextStyles.price,
                          ),
                          if (p.oldPrice != null && p.oldPrice! > p.price) ...[
                            const SizedBox(width: 10),
                            Text(
                              '${p.oldPrice} ${p.currency}',
                              style: const TextStyle(
                                color: AppColors.muted,
                                decoration: TextDecoration.lineThrough,
                              ),
                            ),
                            if (p.discountPercent != null &&
                                p.discountPercent! > 0)
                              Padding(
                                padding: const EdgeInsets.only(left: 8),
                                child: Text(
                                  '−${p.discountPercent}%',
                                  style: const TextStyle(
                                    color: AppColors.accent,
                                    fontWeight: FontWeight.w700,
                                  ),
                                ),
                              ),
                          ],
                          const Spacer(),
                          const Icon(
                            Icons.star,
                            size: 16,
                            color: Color(0xFFD4A017),
                          ),
                          const SizedBox(width: 3),
                          Text(
                            '4.8',
                            style: AppTextStyles.caption.copyWith(
                              color: AppColors.ink,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 20),
                      Text(
                        'Размер',
                        style: AppTextStyles.sectionTitle.copyWith(
                          fontSize: 14,
                        ),
                      ),
                      const SizedBox(height: 10),
                      if (sizes.isEmpty)
                        Text(
                          'Размеры уточняйте на сайте магазина',
                          style: AppTextStyles.bodyMuted,
                        )
                      else
                        Wrap(
                          spacing: 8,
                          children: sizes.map((s) {
                            final on = _selectedSize == s;
                            return ChoiceChip(
                              label: Text(s),
                              selected: on,
                              onSelected: (_) =>
                                  setState(() => _selectedSize = s),
                              selectedColor: AppColors.accent,
                              labelStyle: TextStyle(
                                color: on ? AppColors.onAccent : AppColors.ink,
                              ),
                            );
                          }).toList(),
                        ),
                      const SizedBox(height: 18),
                      Text(
                        'Цвет',
                        style: AppTextStyles.sectionTitle.copyWith(
                          fontSize: 14,
                        ),
                      ),
                      const SizedBox(height: 10),
                      if (colors.isEmpty)
                        Text(
                          'Цвет не указан в каталоге',
                          style: AppTextStyles.bodyMuted,
                        )
                      else
                        Wrap(
                          spacing: 8,
                          children: colors.map((c) {
                            final on = _selectedColor == c;
                            return ChoiceChip(
                              label: Text(c),
                              selected: on,
                              onSelected: (_) =>
                                  setState(() => _selectedColor = c),
                            );
                          }).toList(),
                        ),
                      if (_card.reasons.isNotEmpty || _card.reason.trim().isNotEmpty) ...[
                        const SizedBox(height: 20),
                        Text('Почему рекомендовано', style: AppTextStyles.sectionTitle.copyWith(fontSize: 14)),
                        const SizedBox(height: 8),
                        Wrap(
                          spacing: 8,
                          runSpacing: 8,
                          children: (_card.reasons.isNotEmpty ? _card.reasons : [_card.reason])
                              .take(4)
                              .map((r) => Chip(label: Text(r), backgroundColor: AppColors.chipBg, side: BorderSide.none))
                              .toList(),
                        ),
                      ],
                      Builder(
                        builder: (context) {
                          final related = widget.controller.relatedProducts(p.id, limit: 4);
                          if (related.isEmpty) return const SizedBox.shrink();
                          return Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const SizedBox(height: 20),
                              Text('С чем носить', style: AppTextStyles.sectionTitle.copyWith(fontSize: 14)),
                              const SizedBox(height: 10),
                              SizedBox(
                                height: 110,
                                child: ListView.separated(
                                  scrollDirection: Axis.horizontal,
                                  itemCount: related.length,
                                  separatorBuilder: (context, index) =>
                                      const SizedBox(width: 10),
                                  itemBuilder: (context, i) {
                                    final rp = related[i].product;
                                    return SizedBox(
                                      width: 80,
                                      child: Column(
                                        crossAxisAlignment: CrossAxisAlignment.start,
                                        children: [
                                          Expanded(
                                            child: ProductFillImage(
                                              imageUrl: rp.imageUrl,
                                              borderRadius: BorderRadius.circular(10),
                                            ),
                                          ),
                                          Text(rp.brand, maxLines: 1, overflow: TextOverflow.ellipsis, style: AppTextStyles.caption),
                                        ],
                                      ),
                                    );
                                  },
                                ),
                              ),
                            ],
                          );
                        },
                      ),
                      if (_descriptionWidgets(p.description).isNotEmpty) ...[
                        const SizedBox(height: 20),
                        Text(
                          'Описание',
                          style: AppTextStyles.sectionTitle.copyWith(
                            fontSize: 14,
                          ),
                        ),
                        const SizedBox(height: 8),
                        ..._descriptionWidgets(p.description),
                      ],
                      const SizedBox(height: 24),
                      PrimaryButton(
                        label: 'Перейти в магазин',
                        onPressed: widget.controller.isLoading
                            ? null
                            : () => _openAffiliateShop(
                                context,
                                widget.controller,
                                p.id,
                              ),
                      ),
                      const SizedBox(height: 10),
                      Center(
                        child: Text(
                          'Мы получим комиссию с покупки',
                          style: AppTextStyles.caption,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

List<Widget> _descriptionWidgets(String? raw) {
  if (raw == null) return const [];
  final text = raw.trim();
  if (text.isEmpty) return const [];
  return [Text(text, style: AppTextStyles.body)];
}

class _ProductImageCarousel extends StatefulWidget {
  const _ProductImageCarousel({required this.urls});
  final List<String> urls;

  @override
  State<_ProductImageCarousel> createState() => _ProductImageCarouselState();
}

class _ProductImageCarouselState extends State<_ProductImageCarousel> {
  late final PageController _controller;
  int _page = 0;

  @override
  void initState() {
    super.initState();
    _controller = PageController();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (widget.urls.isEmpty) {
      return ProductFillImage(imageUrl: '', borderRadius: BorderRadius.zero);
    }
    return ClipRRect(
      borderRadius: BorderRadius.zero,
      child: Stack(
        fit: StackFit.expand,
        children: [
          PageView.builder(
            controller: _controller,
            itemCount: widget.urls.length,
            onPageChanged: (i) => setState(() => _page = i),
            itemBuilder: (context, i) => ProductFillImage(
              imageUrl: widget.urls[i],
              borderRadius: BorderRadius.zero,
            ),
          ),
          if (widget.urls.length > 1)
            Positioned(
              bottom: 12,
              left: 0,
              right: 0,
              child: Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: List.generate(widget.urls.length, (i) {
                  return Container(
                    margin: const EdgeInsets.symmetric(horizontal: 3),
                    width: i == _page ? 18 : 6,
                    height: 6,
                    decoration: BoxDecoration(
                      color: i == _page ? AppColors.accent : AppColors.muted,
                      borderRadius: BorderRadius.circular(3),
                    ),
                  );
                }),
              ),
            ),
        ],
      ),
    );
  }
}

Future<void> _openAffiliateShop(
  BuildContext context,
  AppController controller,
  String productId,
) async {
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
    if (!await canLaunchUrl(uri)) return;
    await launchUrl(uri, mode: LaunchMode.externalApplication);
    if (context.mounted) {
      try {
        await controller.api.recordRecommendationEvent(
          eventType: 'buy_click',
          productId: productId,
        );
      } catch (_) {}
    }
  } catch (e) {
    if (context.mounted) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(ApiClient.formatError(e))));
    }
  }
}
