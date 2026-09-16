from pathlib import Path
root = Path(__file__).resolve().parents[1]
path = root/'lib/features/recommendations/feed_screen.dart'
text = path.read_text(encoding='utf-8')
start = text.index('class _ProductCardView extends StatelessWidget')
end = text.index('\nvoid _openProduct(', start)
text = text[:start] + '''class _ProductCardView extends StatelessWidget {
  const _ProductCardView({required this.controller, required this.card});
  final AppController controller;
  final prod.FeedCard card;

  @override
  Widget build(BuildContext context) {
    final p = card.product;
    final currency = {'RUB', 'RUR'}.contains(p.currency) ? '₽' : p.currency;
    return SoftCard(
      padding: const EdgeInsets.all(10),
      child: LayoutBuilder(builder: (context, bounds) {
        final compact = bounds.maxHeight < 350;
        return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Expanded(child: Stack(children: [
            Positioned.fill(child: _FeedImageCarousel(
              urls: p.galleryUrls, borderRadius: BorderRadius.circular(16))),
            if ((p.discountPercent ?? 0) > 0)
              Positioned(top: 10, right: 10, child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                decoration: BoxDecoration(color: AppColors.bg.withValues(alpha: 0.88),
                  borderRadius: BorderRadius.circular(20)),
                child: Text('−${p.discountPercent}%', style: const TextStyle(
                  color: AppColors.accentSoft, fontSize: 12, fontWeight: FontWeight.w700)))),
          ])),
          Padding(padding: const EdgeInsets.fromLTRB(6, 12, 6, 4), child: Column(
            crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Expanded(child: Text(p.brand.toUpperCase(), maxLines: 1,
                  overflow: TextOverflow.ellipsis, style: const TextStyle(
                    color: AppColors.accentSoft, fontSize: 10, letterSpacing: 1.8, fontWeight: FontWeight.w600))),
                const SizedBox(width: 8),
                Flexible(child: Text(p.shopLabel, maxLines: 1, overflow: TextOverflow.ellipsis,
                  style: AppTextStyles.caption)),
              ]),
              const SizedBox(height: 6),
              Text(p.title.isEmpty ? 'Товар' : p.title, maxLines: compact ? 1 : 2,
                overflow: TextOverflow.ellipsis, style: const TextStyle(
                  fontSize: 16, height: 1.25, fontWeight: FontWeight.w600)),
              const SizedBox(height: 8),
              Row(children: [
                Text('${p.price} $currency', style: AppTextStyles.price.copyWith(fontSize: 20)),
                if (p.oldPrice != null && p.oldPrice! > p.price) ...[
                  const SizedBox(width: 10),
                  Text('${p.oldPrice} $currency', style: const TextStyle(fontSize: 12,
                    color: AppColors.muted, decoration: TextDecoration.lineThrough)),
                ],
                const Spacer(),
                if (p.availableSizes.isNotEmpty && !compact)
                  Flexible(child: Text(p.availableSizes.take(3).join(' · '), maxLines: 1,
                    overflow: TextOverflow.ellipsis, style: AppTextStyles.caption)),
              ]),
              if (!compact && card.reasons.isNotEmpty) ...[
                const SizedBox(height: 9),
                Row(children: [
                  const Icon(Icons.auto_awesome_outlined, size: 13, color: AppColors.accent),
                  const SizedBox(width: 6),
                  Expanded(child: Text(card.reasons.first, maxLines: 1,
                    overflow: TextOverflow.ellipsis, style: AppTextStyles.caption)),
                ]),
              ],
            ])),
        ]);
      }),
    );
  }
}
''' + text[end:]
text = text.replace("'${controller.filteredProductFeed.length}'", "'Для вас'")
text = text.replace('const EdgeInsets.fromLTRB(20, 12, 12, 4)', 'const EdgeInsets.fromLTRB(20, 0, 12, 4)')
text = text.replace('height: 120,', 'height: MediaQuery.sizeOf(context).height < 760 ? 84 : 104,')
text = text.replace("padding: const EdgeInsets.fromLTRB(20, 0, 20, 4),", "padding: const EdgeInsets.fromLTRB(20, 10, 20, 4),", 1)
path.write_text(text, encoding='utf-8')
path = root/'lib/app.dart'
text = path.read_text(encoding='utf-8')
start = text.index('class SplashScreen extends StatefulWidget')
end = text.index('\nclass HomeScreen', start)
text = text[:start] + '''class SplashScreen extends StatelessWidget {
  const SplashScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return const MobileViewport(minimalBackdrop: true, child: Stack(
      fit: StackFit.expand, children: [
        HeroFashionBackdrop(),
        SafeArea(child: Align(alignment: Alignment.bottomCenter,
          child: Padding(padding: EdgeInsets.only(bottom: 48),
            child: SizedBox(width: 22, height: 22,
              child: CircularProgressIndicator(strokeWidth: 2, color: AppColors.accentSoft))))),
      ],
    ));
  }
}
''' + text[end:]
text = text.replace('borderRadius: BorderRadius.circular(8)', 'borderRadius: BorderRadius.circular(16)')
start = text.index('  Future<void> _clearSession() async {')
end = text.index('  Future<void> _routeAfterAuth()', start)
text = text[:start] + text[end:]
path.write_text(text, encoding='utf-8')
