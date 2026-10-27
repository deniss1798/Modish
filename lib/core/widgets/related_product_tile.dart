import 'package:flutter/material.dart';

import '../presentation/price_format.dart';
import '../theme/app_text_styles.dart';
import 'product_image.dart';

/// A complement stays identifiable even when its photo cannot be loaded.
class RelatedProductTile extends StatelessWidget {
  const RelatedProductTile({
    super.key,
    required this.title,
    required this.imageUrl,
    required this.price,
    required this.currency,
    required this.onTap,
  });

  final String title;
  final String imageUrl;
  final num price;
  final String currency;
  final VoidCallback onTap;

  static double widthFor(BuildContext context) =>
      124 + MediaQuery.textScalerOf(context).scale(48);

  static double heightFor(BuildContext context) =>
      88 +
      (MediaQuery.textScalerOf(context).scale(13) * 2.6).ceilToDouble() +
      (MediaQuery.textScalerOf(context).scale(12) * 1.3).ceilToDouble();

  @override
  Widget build(BuildContext context) {
    final name = title.trim().isEmpty ? 'Товар' : title;
    final symbol = {'RUB', 'RUR'}.contains(currency) ? '₽' : currency;
    final priceLabel = '${formatPriceAmount(price)} $symbol';
    return Semantics(
      button: true,
      label: 'Открыть $name, $priceLabel',
      onTap: onTap,
      child: ExcludeSemantics(
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(12),
          child: SizedBox(
            width: widthFor(context),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SizedBox(
                  height: 76,
                  width: double.infinity,
                  child: ProductFillImage(
                    imageUrl: imageUrl,
                    showRetry: false,
                    borderRadius: BorderRadius.circular(12),
                  ),
                ),
                const SizedBox(height: 6),
                SizedBox(
                  height: (MediaQuery.textScalerOf(context).scale(13) * 2.6)
                      .ceilToDouble(),
                  child: Text(
                    name,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: AppTextStyles.body.copyWith(
                      fontSize: 13,
                      height: 1.3,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  priceLabel,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: AppTextStyles.price.copyWith(
                    fontSize: 12,
                    height: 1.3,
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
