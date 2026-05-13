import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';
import '../../core/widgets/product_image.dart';
import '../products/models.dart' as prod;
import '../products/product_detail_screen.dart';

class SearchScreen extends StatefulWidget {
  const SearchScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<SearchScreen> createState() => _SearchScreenState();
}

class _SearchScreenState extends State<SearchScreen> {
  final _query = TextEditingController();
  List<prod.Product> _results = [];
  bool _loading = false;

  static const _recent = ['бомбер', 'джинсы', 'сумка'];
  static const _popular = ['Верхняя одежда', 'Обувь', 'Платья', 'Сумки'];

  @override
  void dispose() {
    _query.dispose();
    super.dispose();
  }

  Future<void> _search([String? q]) async {
    final text = (q ?? _query.text).trim();
    if (text.isEmpty) return;
    setState(() => _loading = true);
    try {
      final rows = await widget.controller.api.productsList(limit: 40, brand: text.contains(' ') ? null : text);
      var products = rows.map(prod.Product.fromApi).toList();
      if (text.contains(' ')) {
        final lower = text.toLowerCase();
        products = products.where((p) {
          return p.title.toLowerCase().contains(lower) || p.brand.toLowerCase().contains(lower);
        }).toList();
      }
      if (!mounted) return;
      setState(() {
        _results = products;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      final local = widget.controller.productFeed
          .map((c) => c.product)
          .where((p) => p.title.toLowerCase().contains(text.toLowerCase()))
          .toList();
      setState(() {
        _results = local;
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        backgroundColor: AppColors.bg,
        elevation: 0,
        leading: IconButton(icon: const Icon(Icons.arrow_back), onPressed: () => Navigator.pop(context)),
        title: TextField(
          controller: _query,
          autofocus: true,
          decoration: const InputDecoration(
            hintText: 'Поиск товаров',
            border: InputBorder.none,
          ),
          onSubmitted: _search,
        ),
        actions: [
          IconButton(icon: const Icon(Icons.search), onPressed: () => _search()),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          if (_results.isEmpty && !_loading) ...[
            Text('Недавние', style: AppTextStyles.sectionTitle),
            const SizedBox(height: 10),
            Wrap(
              spacing: 8,
              children: _recent.map((r) => ActionChip(label: Text(r), onPressed: () {
                _query.text = r;
                _search(r);
              })).toList(),
            ),
            const SizedBox(height: 24),
            Text('Популярное', style: AppTextStyles.sectionTitle),
            const SizedBox(height: 10),
            ..._popular.map((p) => ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: Text(p),
                  trailing: const Icon(Icons.north_west, size: 18, color: AppColors.muted),
                  onTap: () {
                    _query.text = p;
                    _search(p);
                  },
                )),
          ],
          if (_loading) const Center(child: Padding(padding: EdgeInsets.all(24), child: CircularProgressIndicator())),
          if (_results.isNotEmpty)
            ..._results.map((p) {
              return Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: SoftCard(
                  padding: const EdgeInsets.all(12),
                  child: InkWell(
                    onTap: () => Navigator.push(
                      context,
                      MaterialPageRoute(
                        builder: (_) => ProductDetailScreen(
                          controller: widget.controller,
                          card: prod.FeedCard(product: p, reason: ''),
                        ),
                      ),
                    ),
                    child: Row(
                      children: [
                        SizedBox(
                          width: 72,
                          height: 90,
                          child: ProductFillImage(imageUrl: p.imageUrl, borderRadius: BorderRadius.circular(12)),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(p.brand, style: AppTextStyles.caption),
                              Text(p.title, maxLines: 2, overflow: TextOverflow.ellipsis, style: AppTextStyles.displaySm.copyWith(fontSize: 16)),
                              Text('${p.price} ${p.currency}', style: AppTextStyles.price.copyWith(fontSize: 16)),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              );
            }),
        ],
      ),
    );
  }
}
