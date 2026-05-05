import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/widgets/modish_widgets.dart';
import 'models.dart';
import 'models.dart' as prod;

class ProductDetailScreen extends StatelessWidget {
  const ProductDetailScreen({
    super.key,
    required this.controller,
    required this.card,
  });

  final AppController controller;
  final prod.FeedCard card;

  @override
  Widget build(BuildContext context) {
    final p = card.product;
    final sizes = p.availableSizes.isEmpty ? '—' : p.availableSizes.take(12).join(', ');
    final colors = p.colors.isEmpty ? '—' : p.colors.take(12).join(', ');
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
                const Spacer(),
                const Brand(size: 40),
                const Spacer(),
                const SizedBox(width: 48),
              ],
            ),
            SoftCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (p.imageUrl.isNotEmpty)
                    ClipRRect(
                      borderRadius: BorderRadius.circular(18),
                      child: Image.network(p.imageUrl, height: 260, fit: BoxFit.cover),
                    ),
                  const SizedBox(height: 12),
                  Text(
                    p.title.isEmpty ? 'Товар' : p.title,
                    style: const TextStyle(fontFamily: 'Georgia', fontSize: 28, color: AppColors.ink),
                  ),
                  const SizedBox(height: 6),
                  Text('${p.brand} · ${p.price} ${p.currency}', style: const TextStyle(color: AppColors.muted)),
                  const SizedBox(height: 10),
                  Text('Категория: ${p.category}', style: const TextStyle(color: AppColors.muted)),
                  const SizedBox(height: 8),
                  Text('Размеры: $sizes', style: const TextStyle(color: AppColors.muted)),
                  const SizedBox(height: 6),
                  Text('Цвета: $colors', style: const TextStyle(color: AppColors.muted)),
                  const Divider(height: 26),
                  const Text('Почему рекомендовано', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
                  const SizedBox(height: 8),
                  Text(card.reason, style: const TextStyle(height: 1.35)),
                  const SizedBox(height: 14),
                  Row(
                    children: [
                      Expanded(
                        child: FilledButton.icon(
                          onPressed: controller.isLoading
                              ? null
                              : () => controller.sendProductEvent(context, p.id, 'like'),
                          icon: const Icon(Icons.favorite_border),
                          label: const Text('Like'),
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: OutlinedButton.icon(
                          onPressed: controller.isLoading
                              ? null
                              : () => controller.sendProductEvent(context, p.id, 'dislike'),
                          icon: const Icon(Icons.thumb_down_outlined),
                          label: const Text('Dislike'),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  Row(
                    children: [
                      Expanded(
                        child: OutlinedButton.icon(
                          onPressed: controller.isLoading
                              ? null
                              : () => controller.sendProductEvent(context, p.id, 'save'),
                          icon: const Icon(Icons.bookmark_border),
                          label: const Text('Save'),
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: OutlinedButton.icon(
                          onPressed: controller.isLoading
                              ? null
                              : () => controller.sendProductEvent(context, p.id, 'open_product'),
                          icon: const Icon(Icons.open_in_new),
                          label: const Text('Open'),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 6),
                  Text('Ссылка: ${p.productUrl}', style: const TextStyle(color: AppColors.muted, fontSize: 12)),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

