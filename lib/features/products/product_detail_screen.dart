import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../app.dart';
import '../../core/network/api_client.dart';
import '../../core/theme/app_colors.dart';
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

  @override
  void initState() {
    super.initState();
    _card = widget.card;
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
    final catLabel = (p.categoryName ?? '').trim().isNotEmpty ? p.categoryName! : p.category;
    final sizeLine = (p.sizeOriginal?.trim().isNotEmpty ?? false)
        ? p.sizeOriginal!.trim()
        : (p.availableSizes.isEmpty ? '—' : p.availableSizes.take(12).join(', '));
    final colorLine = (p.colorOriginal?.trim().isNotEmpty ?? false)
        ? p.colorOriginal!.trim()
        : (p.colors.isEmpty ? '—' : p.colors.take(12).join(', '));
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
                IconButton(
                  tooltip: 'Обновить с сервера',
                  onPressed: _refreshing ? null : _pullLatest,
                  icon: _refreshing
                      ? const SizedBox(
                          width: 22,
                          height: 22,
                          child: CircularProgressIndicator(strokeWidth: 2.2, color: AppColors.accent),
                        )
                      : const Icon(Icons.refresh),
                ),
                const Spacer(),
                const Brand(size: 40),
                const Spacer(),
                const SizedBox(width: 48),
              ],
            ),
            if (_refreshError != null && _refreshError!.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: Text(
                  _refreshError!,
                  style: const TextStyle(color: AppColors.accent, fontSize: 13),
                ),
              ),
            SoftCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SizedBox(
                    height: 280,
                    width: double.infinity,
                    child: _ProductImageCarousel(urls: gallery),
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
                  Text('Категория: $catLabel', style: const TextStyle(color: AppColors.muted)),
                  const SizedBox(height: 8),
                  Text('Размеры: $sizeLine', style: const TextStyle(color: AppColors.muted)),
                  const SizedBox(height: 6),
                  Text('Цвета: $colorLine', style: const TextStyle(color: AppColors.muted)),
                  if (_descriptionWidgets(p.description).isNotEmpty) ...[
                    const Divider(height: 26),
                    const Text('Описание', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
                    const SizedBox(height: 8),
                    ..._descriptionWidgets(p.description),
                  ],
                  const Divider(height: 26),
                  const Text('Почему рекомендовано', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
                  const SizedBox(height: 8),
                  Text(_card.reason, style: const TextStyle(height: 1.35)),
                  const SizedBox(height: 16),
                  FilledButton.icon(
                    onPressed: widget.controller.isLoading
                        ? null
                        : () => _openAffiliateShop(context, widget.controller, p.id),
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
                          onPressed: widget.controller.isLoading
                              ? null
                              : () => widget.controller.sendProductEvent(context, p.id, 'like'),
                          icon: const Icon(Icons.favorite_border),
                          label: const Text('Like'),
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: OutlinedButton.icon(
                          onPressed: widget.controller.isLoading
                              ? null
                              : () => widget.controller.sendProductEvent(context, p.id, 'dislike'),
                          icon: const Icon(Icons.thumb_down_outlined),
                          label: const Text('Dislike'),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  OutlinedButton.icon(
                    onPressed: widget.controller.isLoading
                        ? null
                        : () => widget.controller.sendProductEvent(context, p.id, 'save'),
                    icon: const Icon(Icons.bookmark_border),
                    label: const Text('Сохранить'),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'Магазин: ${p.shopLabel}',
                    style: const TextStyle(color: AppColors.muted, fontSize: 13),
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

/// Строки фида «- пункт» → маркеры; иначе обычный текст.
List<Widget> _descriptionWidgets(String? raw) {
  if (raw == null) return const [];
  final text = raw.trim();
  if (text.isEmpty) return const [];
  final lines = text.split(RegExp(r'\r?\n')).map((e) => e.trim()).where((e) => e.isNotEmpty).toList();
  if (lines.isEmpty) return const [];
  final dashCount = lines.where((l) => l.startsWith('-')).length;
  final bulletLike = dashCount >= (lines.length + 1) ~/ 2;
  if (!bulletLike) {
    return [Text(text, style: const TextStyle(height: 1.4, color: AppColors.ink))];
  }
  return lines.map((line) {
    final body = line.startsWith('-') ? line.replaceFirst(RegExp(r'^-\s*'), '') : line;
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('• ', style: TextStyle(color: AppColors.accent, fontWeight: FontWeight.w700)),
          Expanded(child: Text(body, style: const TextStyle(height: 1.35, color: AppColors.ink))),
        ],
      ),
    );
  }).toList();
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
      return ProductFillImage(
        imageUrl: '',
        borderRadius: BorderRadius.circular(18),
      );
    }
    if (widget.urls.length == 1) {
      return ProductFillImage(
        imageUrl: widget.urls.first,
        borderRadius: BorderRadius.circular(18),
      );
    }
    return Column(
      children: [
        Expanded(
          child: ClipRRect(
            borderRadius: BorderRadius.circular(18),
            child: PageView.builder(
              controller: _controller,
              itemCount: widget.urls.length,
              onPageChanged: (i) => setState(() => _page = i),
              itemBuilder: (context, i) {
                return ProductFillImage(
                  imageUrl: widget.urls[i],
                  borderRadius: BorderRadius.zero,
                );
              },
            ),
          ),
        ),
        const SizedBox(height: 8),
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: List.generate(widget.urls.length, (i) {
            final on = i == _page;
            return AnimatedContainer(
              duration: const Duration(milliseconds: 200),
              margin: const EdgeInsets.symmetric(horizontal: 3),
              width: on ? 18 : 6,
              height: 6,
              decoration: BoxDecoration(
                color: on ? AppColors.accent : AppColors.line,
                borderRadius: BorderRadius.circular(4),
              ),
            );
          }),
        ),
      ],
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
