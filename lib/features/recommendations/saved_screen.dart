import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';
import '../../core/widgets/product_image.dart';
import '../outfits/outfit_detail_screen.dart';
import '../products/models.dart' as prod;
import '../products/product_detail_screen.dart';

enum _SavedFilter { all, products, looks, collections }

class SavedScreen extends StatefulWidget {
  const SavedScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<SavedScreen> createState() => _SavedScreenState();
}

class _SavedScreenState extends State<SavedScreen> {
  _SavedFilter _filter = _SavedFilter.all;

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_onCtrl);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onCtrl);
    super.dispose();
  }

  void _onCtrl() => setState(() {});

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;

    return ListView(
      padding: const EdgeInsets.only(bottom: 20),
      children: [
        const ScreenHeader(title: 'Сохраненное'),
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: 20),
          child: Row(
            children: [
              ModishChip(
                label: 'Все',
                selected: _filter == _SavedFilter.all,
                onTap: () => setState(() => _filter = _SavedFilter.all),
              ),
              ModishChip(
                label: 'Товары',
                selected: _filter == _SavedFilter.products,
                onTap: () => setState(() => _filter = _SavedFilter.products),
              ),
              ModishChip(
                label: 'Образы',
                selected: _filter == _SavedFilter.looks,
                onTap: () => setState(() => _filter = _SavedFilter.looks),
              ),
              ModishChip(
                label: 'Коллекции',
                selected: _filter == _SavedFilter.collections,
                onTap: () => setState(() => _filter = _SavedFilter.collections),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        if (_filter == _SavedFilter.all || _filter == _SavedFilter.products)
          _SavedProducts(controller: c),
        if (_filter == _SavedFilter.all || _filter == _SavedFilter.looks)
          _SavedOutfits(controller: c),
        if (_filter == _SavedFilter.all || _filter == _SavedFilter.collections)
          _SavedCollections(controller: c),
      ],
    );
  }
}

class _SavedProducts extends StatelessWidget {
  const _SavedProducts({required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final rows = controller.savedProductRows;
    if (rows.isEmpty) {
      return const Padding(
        padding: EdgeInsets.all(20),
        child: SoftCard(
          child: Text('Сохранённых товаров пока нет', style: AppTextStyles.bodyMuted),
        ),
      );
    }
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 20),
      child: GridView.builder(
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
          crossAxisCount: 2,
          mainAxisSpacing: 12,
          crossAxisSpacing: 12,
          childAspectRatio: 0.72,
        ),
        itemCount: rows.length,
        itemBuilder: (context, i) {
          final p = rows[i]['product'];
          final product = p is Map
              ? prod.Product.fromApi(Map<String, dynamic>.from(p))
              : prod.Product.fromApi({});
          return SoftCard(
            padding: const EdgeInsets.all(10),
            child: Stack(
              children: [
                InkWell(
                  onTap: () => Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => ProductDetailScreen(
                        controller: controller,
                        card: prod.FeedCard(product: product, reason: ''),
                      ),
                    ),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(
                        child: ProductFillImage(
                          imageUrl: product.imageUrl,
                          borderRadius: BorderRadius.circular(12),
                        ),
                      ),
                      const SizedBox(height: 8),
                      Text(
                        product.brand,
                        style: AppTextStyles.caption,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                      Text(
                        product.title,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w500),
                      ),
                      Text(
                        '${product.price} ₽',
                        style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
                      ),
                    ],
                  ),
                ),
                Positioned(
                  top: 0,
                  right: 0,
                  child: IconButton(
                    icon: const Icon(Icons.close, size: 20),
                    tooltip: 'Убрать из сохранённого',
                    visualDensity: VisualDensity.compact,
                    onPressed: controller.isLoading
                        ? null
                        : () => controller.unsaveProduct(product.id),
                  ),
                ),
              ],
            ),
          );
        },
      ),
    );
  }
}

class _SavedOutfits extends StatelessWidget {
  const _SavedOutfits({required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final outfits = controller.savedOutfits;
    if (outfits.isEmpty) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Padding(
          padding: EdgeInsets.fromLTRB(20, 8, 20, 8),
          child: Text('Образы', style: AppTextStyles.sectionTitle),
        ),
        ...outfits.map((o) {
          final pmap = o['products'] is Map
              ? Map<String, dynamic>.from(o['products'] as Map)
              : <String, dynamic>{};
          final top = pmap['top'];
          String img = '';
          if (top is Map) img = (top['image_url'] ?? '').toString();
          return Padding(
            padding: const EdgeInsets.fromLTRB(20, 0, 20, 10),
            child: SoftCard(
              padding: const EdgeInsets.all(12),
              child: Row(
                children: [
                  Expanded(
                    child: InkWell(
                      onTap: () => Navigator.push(
                        context,
                        MaterialPageRoute(
                          builder: (_) => OutfitDetailScreen(outfit: o, controller: controller),
                        ),
                      ),
                      child: Row(
                        children: [
                          SizedBox(
                            width: 64,
                            height: 80,
                            child: ProductFillImage(
                              imageUrl: img,
                              borderRadius: BorderRadius.circular(10),
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  '${o['style_direction'] ?? 'Образ'}',
                                  style: AppTextStyles.displaySm.copyWith(fontSize: 18),
                                ),
                                Text(
                                  '${o['total_price'] ?? 0} ₽',
                                  style: AppTextStyles.bodyMuted,
                                ),
                              ],
                            ),
                          ),
                          const Icon(Icons.chevron_right),
                        ],
                      ),
                    ),
                  ),
                  IconButton(
                    icon: const Icon(Icons.bookmark, color: AppColors.accent),
                    tooltip: 'Убрать из сохранённого',
                    onPressed: controller.isLoading
                        ? null
                        : () => controller.unsaveOutfit('${o['id']}'),
                  ),
                ],
              ),
            ),
          );
        }),
      ],
    );
  }
}

class _SavedCollections extends StatelessWidget {
  const _SavedCollections({required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final groups = <String, List<prod.Product>>{};
    for (final row in controller.savedProductRows) {
      final p = row['product'];
      if (p is! Map) continue;
      final product = prod.Product.fromApi(Map<String, dynamic>.from(p));
      final key = (product.categoryName ?? product.category).trim();
      if (key.isEmpty) continue;
      groups.putIfAbsent(key, () => []).add(product);
    }
    if (groups.isEmpty) {
      return const Padding(
        padding: EdgeInsets.all(20),
        child: SoftCard(
          child: Text(
            'Коллекции появятся, когда вы сохраните несколько товаров одной категории.',
            style: AppTextStyles.bodyMuted,
          ),
        ),
      );
    }
    final entries = groups.entries.toList()..sort((a, b) => b.value.length.compareTo(a.value.length));
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Padding(
          padding: EdgeInsets.fromLTRB(20, 8, 20, 8),
          child: Text('Коллекции', style: AppTextStyles.sectionTitle),
        ),
        ...entries.take(6).map((e) {
          return Padding(
            padding: const EdgeInsets.fromLTRB(20, 0, 20, 10),
            child: SoftCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(e.key, style: AppTextStyles.displaySm.copyWith(fontSize: 18)),
                  Text('${e.value.length} вещей', style: AppTextStyles.bodyMuted),
                  const SizedBox(height: 10),
                  SizedBox(
                    height: 88,
                    child: ListView.separated(
                      scrollDirection: Axis.horizontal,
                      itemCount: e.value.length.clamp(0, 8),
                      separatorBuilder: (context, index) =>
                          const SizedBox(width: 8),
                      itemBuilder: (context, i) {
                        return SizedBox(
                          width: 72,
                          child: ProductFillImage(
                            imageUrl: e.value[i].imageUrl,
                            borderRadius: BorderRadius.circular(10),
                          ),
                        );
                      },
                    ),
                  ),
                ],
              ),
            ),
          );
        }),
      ],
    );
  }
}
