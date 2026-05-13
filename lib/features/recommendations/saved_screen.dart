import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';
import '../../core/widgets/product_image.dart';
import '../collections/collections_screen.dart';
import '../products/models.dart' as prod;

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

    if (_filter == _SavedFilter.collections) {
      return CollectionsScreen(controller: c);
    }

    return ListView(
      padding: const EdgeInsets.only(bottom: 20),
      children: [
        const ScreenHeader(title: 'Сохраненное'),
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: 20),
          child: Row(
            children: [
              ModishChip(label: 'Все', selected: _filter == _SavedFilter.all, onTap: () => setState(() => _filter = _SavedFilter.all)),
              ModishChip(label: 'Товары', selected: _filter == _SavedFilter.products, onTap: () => setState(() => _filter = _SavedFilter.products)),
              ModishChip(label: 'Образы', selected: _filter == _SavedFilter.looks, onTap: () => setState(() => _filter = _SavedFilter.looks)),
              ModishChip(label: 'Коллекции', selected: _filter == _SavedFilter.collections, onTap: () => setState(() => _filter = _SavedFilter.collections)),
            ],
          ),
        ),
        const SizedBox(height: 12),
        if (_filter == _SavedFilter.all || _filter == _SavedFilter.products) _SavedProducts(controller: c),
        if (_filter == _SavedFilter.all || _filter == _SavedFilter.looks) _SavedOutfits(controller: c),
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
        child: SoftCard(child: Text('Сохранённых товаров пока нет', style: AppTextStyles.bodyMuted)),
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
          final product = p is Map ? prod.Product.fromApi(Map<String, dynamic>.from(p)) : prod.Product.fromApi({});
          return SoftCard(
            padding: const EdgeInsets.all(10),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: ProductFillImage(imageUrl: product.imageUrl, borderRadius: BorderRadius.circular(12)),
                ),
                const SizedBox(height: 8),
                Text(product.brand, style: AppTextStyles.caption, maxLines: 1, overflow: TextOverflow.ellipsis),
                Text(
                  product.title,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w500),
                ),
                Text('${product.price} ₽', style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
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
    final rows = controller.savedRows;
    if (rows.isEmpty) return const SizedBox.shrink();
    return Column(
      children: rows.map((row) {
        final rec = row['recommendation'];
        final title = rec is Map ? '${rec['title'] ?? 'Образ'}' : 'Образ';
        return Padding(
          padding: const EdgeInsets.fromLTRB(20, 0, 20, 10),
          child: SoftCard(
            child: ListTile(
              contentPadding: EdgeInsets.zero,
              title: Text(title, style: AppTextStyles.displaySm.copyWith(fontSize: 18)),
              trailing: IconButton(
                icon: const Icon(Icons.bookmark_remove_outlined),
                onPressed: controller.isLoading
                    ? null
                    : () => controller.sendFeedback('${row['recommendation_id']}', 'unsave'),
              ),
            ),
          ),
        );
      }).toList(),
    );
  }
}
