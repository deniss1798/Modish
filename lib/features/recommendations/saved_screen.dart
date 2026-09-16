import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';
import '../../core/widgets/product_image.dart';
import '../outfits/outfits_screen.dart';
import '../products/models.dart' as prod;
import '../products/product_detail_screen.dart';

class SavedScreen extends StatefulWidget {
  const SavedScreen({super.key, required this.controller});
  final AppController controller;
  @override
  State<SavedScreen> createState() => _SavedScreenState();
}

class _SavedScreenState extends State<SavedScreen> {
  bool _looks = false;
  @override
  void initState() {
    super.initState();
    _looks =
        widget.controller.savedProductRows.isEmpty &&
        widget.controller.savedOutfits.isNotEmpty;
    widget.controller.addListener(_onChange);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onChange);
    super.dispose();
  }

  void _onChange() {
    if (mounted) setState(() {});
  }

  Future<void> _remove(String id, {bool outfit = false}) async {
    final c = widget.controller;
    if (outfit) {
      await c.unsaveOutfit(id);
    } else {
      await c.unsaveProduct(id);
    }
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(c.error ?? 'Удалено из сохранённого')),
    );
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    final empty = _looks ? c.savedOutfits.isEmpty : c.savedProductRows.isEmpty;
    return ListView(
      key: PageStorageKey('saved-$_looks'),
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 24),
      children: [
        const Text('Сохранённое', style: AppTextStyles.screenTitle),
        const SizedBox(height: 20),
        Row(
          children: [
            Expanded(
              child: _tab(
                'Вещи',
                c.savedProductRows.length,
                false,
                Icons.bookmark_outline,
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: _tab(
                'Образы',
                c.savedOutfits.length,
                true,
                Icons.checkroom_outlined,
              ),
            ),
          ],
        ),
        const SizedBox(height: 20),
        if (empty)
          EmptyState(
            icon: _looks ? Icons.checkroom_outlined : Icons.bookmark_border,
            title: _looks ? 'Здесь будут ваши образы' : 'Здесь будут ваши вещи',
            message: _looks
                ? 'Сохраните целый образ, чтобы все его вещи были под рукой.'
                : 'Нажмите на закладку у понравившейся вещи. Её легко найти здесь и открыть в магазине.',
            actionLabel: _looks ? 'Подобрать образ' : 'Найти вещи',
            onAction: () => c.setTab(_looks ? 1 : 0),
          )
        else if (_looks)
          ...c.savedOutfits.map(
            (outfit) => Column(
              children: [
                OutfitCard(
                  outfit: outfit,
                  controller: c,
                  padding: EdgeInsets.zero,
                ),
                Align(
                  alignment: Alignment.centerRight,
                  child: TextButton.icon(
                    onPressed: c.isLoading
                        ? null
                        : () => _remove('${outfit['id']}', outfit: true),
                    icon: const Icon(Icons.bookmark_remove_outlined, size: 18),
                    label: const Text('Убрать из сохранённого'),
                  ),
                ),
                const SizedBox(height: 12),
              ],
            ),
          )
        else
          ...c.savedProductRows.map((row) {
            final product = prod.Product.fromApi(
              Map<String, dynamic>.from(row['product'] as Map),
            );
            return Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: SoftCard(
                padding: const EdgeInsets.all(12),
                child: InkWell(
                  borderRadius: BorderRadius.circular(16),
                  onTap: () => Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => ProductDetailScreen(
                        controller: c,
                        card: prod.FeedCard(product: product, reason: ''),
                      ),
                    ),
                  ),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      SizedBox(
                        width: 104,
                        height: 142,
                        child: ProductFillImage(
                          imageUrl: product.imageUrl,
                          borderRadius: BorderRadius.circular(12),
                        ),
                      ),
                      const SizedBox(width: 14),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                Expanded(
                                  child: Text(
                                    product.brand,
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                    style: AppTextStyles.caption,
                                  ),
                                ),
                                PopupMenuButton<String>(
                                  tooltip: 'Действия с вещью',
                                  enabled: !c.isLoading,
                                  padding: EdgeInsets.zero,
                                  constraints: const BoxConstraints(
                                    minWidth: 40,
                                    minHeight: 40,
                                  ),
                                  icon: const Icon(Icons.more_horiz, size: 22),
                                  onSelected: (_) => _remove(product.id),
                                  itemBuilder: (_) => [
                                    const PopupMenuItem(
                                      value: 'remove',
                                      child: Text('Убрать из сохранённого'),
                                    ),
                                  ],
                                ),
                              ],
                            ),
                            Text(
                              product.title,
                              maxLines: 3,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                fontSize: 15,
                                fontWeight: FontWeight.w500,
                                height: 1.3,
                              ),
                            ),
                            const SizedBox(height: 10),
                            Row(
                              children: [
                                Expanded(
                                  child: Text(
                                    '${product.price} ₽',
                                    style: const TextStyle(
                                      fontSize: 16,
                                      fontWeight: FontWeight.w700,
                                    ),
                                  ),
                                ),
                                const Icon(
                                  Icons.chevron_right,
                                  color: AppColors.muted,
                                  size: 20,
                                ),
                              ],
                            ),
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
    );
  }

  Widget _tab(String label, int count, bool looks, IconData icon) {
    final selected = _looks == looks;
    return Semantics(
      selected: selected,
      button: true,
      child: Material(
        color: selected ? AppColors.accent : AppColors.card,
        borderRadius: BorderRadius.circular(16),
        child: InkWell(
          borderRadius: BorderRadius.circular(16),
          onTap: () => setState(() => _looks = looks),
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 16, horizontal: 8),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(
                  icon,
                  size: 18,
                  color: selected ? AppColors.onAccent : AppColors.muted,
                ),
                const SizedBox(width: 8),
                Flexible(
                  child: Text(
                    '$label · $count',
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontWeight: FontWeight.w600,
                      color: selected ? AppColors.onAccent : AppColors.ink,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
