import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/widgets/modish_widgets.dart';
import 'models.dart';
import '../products/models.dart' as prod;

enum _SavedFilter { all, daily, office, evening }

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

  bool _matches(Outfit o) {
    switch (_filter) {
      case _SavedFilter.all:
        return true;
      case _SavedFilter.daily:
        return o.occasionTags.contains('daily');
      case _SavedFilter.office:
        return o.occasionTags.contains('office');
      case _SavedFilter.evening:
        return o.occasionTags.contains('evening');
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    final entries = <({String savedId, String recId, String savedAt, Outfit outfit})>[];
    for (final row in c.savedRows) {
      final rec = row['recommendation'];
      if (rec is! Map<String, dynamic>) continue;
      final outfit = Outfit.fromApi(rec);
      if (!_matches(outfit)) continue;
      entries.add((
        savedId: '${row['id']}',
        recId: '${row['recommendation_id']}',
        savedAt: '${row['saved_at'] ?? ''}',
        outfit: outfit,
      ));
    }

    return SafeArea(
      child: ListView(
        padding: const EdgeInsets.fromLTRB(18, 22, 18, 20),
        children: [
          const Center(child: Brand()),
          const SizedBox(height: 16),
          const Text(
            'Сохраненное',
            style: TextStyle(
              fontFamily: 'Georgia',
              fontSize: 40,
              color: AppColors.ink,
            ),
          ),
          const SizedBox(height: 12),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                _chip('Все', _SavedFilter.all),
                _chip('Каждый день', _SavedFilter.daily),
                _chip('Офис', _SavedFilter.office),
                _chip('Вечер', _SavedFilter.evening),
              ],
            ),
          ),
          const SizedBox(height: 12),
          const SoftCard(
            child: Padding(
              padding: EdgeInsets.all(12),
              child: Text(
                'Товары (новое ядро)',
                style: TextStyle(fontWeight: FontWeight.w700),
              ),
            ),
          ),
          const SizedBox(height: 10),
          _SavedProducts(controller: c),
          const SizedBox(height: 14),
          if (entries.isEmpty)
            const SoftCard(
              child: Center(
                child: Padding(
                  padding: EdgeInsets.all(28),
                  child: Text('Сохраненных образов пока нет'),
                ),
              ),
            )
          else
            ...entries.map(
              (e) => Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: SoftCard(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      ListTile(
                        contentPadding: EdgeInsets.zero,
                        title: Text(
                          e.outfit.title,
                          style: const TextStyle(
                            fontSize: 22,
                            fontFamily: 'Georgia',
                          ),
                        ),
                        subtitle: Text(
                          '${e.outfit.description}\nСохранено: ${e.savedAt}',
                        ),
                        trailing: IconButton(
                          tooltip: 'Удалить из сохранённого',
                          icon: const Icon(Icons.bookmark_remove_outlined),
                          onPressed: c.isLoading
                              ? null
                              : () => c.sendFeedback(e.recId, 'unsave'),
                        ),
                        onTap: () => openDetails(context, e.outfit, c),
                      ),
                    ],
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }

  Widget _chip(String label, _SavedFilter value) {
    final selected = _filter == value;
    return Padding(
      padding: const EdgeInsets.only(right: 8),
      child: FilterChip(
        label: Text(label),
        selected: selected,
        onSelected: (_) => setState(() => _filter = value),
        selectedColor: AppColors.accent.withValues(alpha: .12),
      ),
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
      return const SoftCard(
        child: Padding(
          padding: EdgeInsets.all(18),
          child: Text('Сохранённых товаров пока нет', style: TextStyle(color: AppColors.muted)),
        ),
      );
    }
    return Column(
      children: rows.take(30).map((row) {
        final p = row['product'];
        final product = p is Map<String, dynamic> ? prod.Product.fromApi(p) : prod.Product.fromApi({});
        final savedAt = (row['saved_at'] ?? '').toString();
        return Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: SoftCard(
            child: ListTile(
              contentPadding: EdgeInsets.zero,
              leading: ClipRRect(
                borderRadius: BorderRadius.circular(10),
                child: product.imageUrl.isEmpty
                    ? const SizedBox(width: 54, height: 54)
                    : Image.network(product.imageUrl, width: 54, height: 54, fit: BoxFit.cover),
              ),
              title: Text(product.title.isEmpty ? 'Товар' : product.title),
              subtitle: Text('${product.brand} · ${product.price} ${product.currency}\nСохранено: $savedAt'),
              trailing: IconButton(
                tooltip: 'Убрать из сохранённого',
                icon: const Icon(Icons.bookmark_remove_outlined),
                onPressed: controller.isLoading
                    ? null
                    : () => controller.sendProductEvent(context, product.id, 'unsave'),
              ),
              onTap: () => showModalBottomSheet(
                context: context,
                showDragHandle: true,
                builder: (_) => Padding(
                  padding: const EdgeInsets.fromLTRB(18, 8, 18, 22),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        product.title,
                        style: const TextStyle(fontFamily: 'Georgia', fontSize: 22),
                      ),
                      const SizedBox(height: 6),
                      Text(
                        '${product.brand} · ${product.price} ${product.currency}',
                        style: const TextStyle(color: AppColors.muted),
                      ),
                      const SizedBox(height: 12),
                      Text('Ссылка: ${product.productUrl}', style: const TextStyle(color: AppColors.muted, fontSize: 12)),
                    ],
                  ),
                ),
              ),
            ),
          ),
        );
      }).toList(),
    );
  }
}
