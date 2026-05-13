import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';
import '../../core/widgets/product_image.dart';
import '../products/models.dart' as prod;

class CollectionsScreen extends StatelessWidget {
  const CollectionsScreen({super.key, required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final saved = controller.savedProductRows;
    final outfits = controller.savedRows;

    final collections = [
      ('Весна 2026', 'Сохранённые вещи на сезон', saved.take(4).toList()),
      (
        'Офисный гардероб',
        'Классика и минимализм',
        saved
            .where((r) {
              final p = r['product'];
              if (p is! Map) return false;
              final cat = '${p['category'] ?? ''}'.toLowerCase();
              return cat.contains('рубаш') || cat.contains('брюк');
            })
            .take(4)
            .toList(),
      ),
      ('Вечерние образы', 'Сохранённые луки', outfits.take(4).toList()),
    ];

    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        backgroundColor: AppColors.bg,
        elevation: 0,
        leading: IconButton(
          icon: const Icon(Icons.arrow_back),
          onPressed: () => Navigator.pop(context),
        ),
        title: const Text('Коллекции', style: AppTextStyles.sectionTitle),
      ),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: collections.map((c) {
          return Padding(
            padding: const EdgeInsets.only(bottom: 16),
            child: SoftCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    c.$1,
                    style: AppTextStyles.displaySm.copyWith(fontSize: 22),
                  ),
                  const SizedBox(height: 4),
                  Text(c.$2, style: AppTextStyles.bodyMuted),
                  const SizedBox(height: 14),
                  SizedBox(
                    height: 100,
                    child: ListView.separated(
                      scrollDirection: Axis.horizontal,
                      itemCount: c.$3.length,
                      separatorBuilder: (context, index) =>
                          const SizedBox(width: 8),
                      itemBuilder: (context, i) {
                        final row = c.$3[i];
                        var url = '';
                        final productRow = row['product'];
                        if (productRow is Map) {
                          final p = prod.Product.fromApi(
                            Map<String, dynamic>.from(productRow),
                          );
                          url = p.imageUrl;
                        }
                        return ClipRRect(
                          borderRadius: BorderRadius.circular(12),
                          child: SizedBox(
                            width: 80,
                            child: ProductFillImage(
                              imageUrl: url,
                              borderRadius: BorderRadius.circular(12),
                            ),
                          ),
                        );
                      },
                    ),
                  ),
                ],
              ),
            ),
          );
        }).toList(),
      ),
    );
  }
}
