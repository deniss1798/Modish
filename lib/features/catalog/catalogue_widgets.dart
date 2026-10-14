import 'package:flutter/material.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';
import '../products/models.dart';

/// Discovery is browsing the merchant's stock, not a verified fit recommendation.
class CatalogueSizeNotice extends StatelessWidget {
  const CatalogueSizeNotice({super.key});

  @override
  Widget build(BuildContext context) => SoftCard(
    padding: const EdgeInsets.all(16),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Icon(
          Icons.straighten_outlined,
          color: AppColors.accent,
          size: 24,
        ),
        const SizedBox(height: 8),
        const Text(
          'Размер нужно уточнить у магазина',
          style: AppTextStyles.sectionTitle,
        ),
        const SizedBox(height: 6),
        const Text(
          'Здесь можно посмотреть товары магазинов. Мы не проверяли, подойдёт ли вам их размер.',
          style: AppTextStyles.bodyMuted,
        ),
      ],
    ),
  );
}

String merchantSizeSummary(Product product) {
  if (product.merchantSizes.isEmpty) return 'Размеры уточните у магазина';
  final labels = product.merchantSizes.map((size) {
    final availability = switch (size.availability) {
      'unavailable' => ' · нет в наличии',
      'available' => '',
      _ => ' · наличие уточните',
    };
    return '${size.label}$availability';
  }).toSet();
  return 'Размеры магазина: ${labels.take(4).join(', ')}${labels.length > 4 ? '…' : ''}';
}

class MerchantSizeSummary extends StatelessWidget {
  const MerchantSizeSummary({super.key, required this.product});
  final Product product;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      if (product.merchantSizes.any(
        (size) => size.sizeSystem == 'UNKNOWN',
      )) ...[
        // A safety-critical unit notice must not disappear behind the summary's
        // ellipsis, especially when sizes carry long availability labels.
        Text(
          'Система размеров не указана',
          style: AppTextStyles.caption.copyWith(color: AppColors.accentSoft),
        ),
        const SizedBox(height: 6),
      ],
      Text(
        merchantSizeSummary(product),
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
        style: AppTextStyles.caption,
      ),
    ],
  );
}

class MerchantSizes extends StatelessWidget {
  const MerchantSizes({super.key, required this.product});
  final Product product;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text(
        'Размеры магазина',
        style: AppTextStyles.sectionTitle.copyWith(fontSize: 14),
      ),
      const SizedBox(height: 10),
      if (product.merchantSizes.isEmpty)
        const Text(
          'Размеры не указаны. Уточните их у магазина.',
          style: AppTextStyles.bodyMuted,
        )
      else ...[
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: product.merchantSizes.map((size) {
            final availability = switch (size.availability) {
              'unavailable' => ' · нет в наличии',
              'available' => '',
              _ => ' · наличие уточните',
            };
            return Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
              decoration: BoxDecoration(
                color: AppColors.chipBg,
                borderRadius: BorderRadius.circular(14),
              ),
              child: Text(
                '${size.label}$availability',
                style: AppTextStyles.caption,
              ),
            );
          }).toList(),
        ),
        if (product.merchantSizes.any(
          (size) => size.sizeSystem == 'UNKNOWN',
        )) ...[
          const SizedBox(height: 10),
          const Text(
            'Магазин не указал систему этих размеров. Сверьте их с таблицей размеров на его сайте.',
            style: AppTextStyles.bodyMuted,
          ),
        ],
      ],
    ],
  );
}
