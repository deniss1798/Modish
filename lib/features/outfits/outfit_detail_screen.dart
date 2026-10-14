import 'package:flutter/material.dart';
import '../../core/network/merchant_link.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';
import '../../core/widgets/product_image.dart';
import '../products/models.dart' as prod;
import '../products/product_detail_screen.dart';
import 'outfit_presentation.dart';

class OutfitDetailScreen extends StatefulWidget {
  const OutfitDetailScreen({
    super.key,
    required this.outfit,
    required this.controller,
  });
  final Map<String, dynamic> outfit;
  final AppController controller;

  @override
  State<OutfitDetailScreen> createState() => _OutfitDetailScreenState();
}

class _OutfitDetailScreenState extends State<OutfitDetailScreen> {
  AppController get controller => widget.controller;
  Map<String, dynamic> get outfit => widget.outfit;
  late bool _saved;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _saved = outfit['is_saved'] == true;
  }

  @override
  void didUpdateWidget(covariant OutfitDetailScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.outfit != outfit) _saved = outfit['is_saved'] == true;
  }

  static const _slots = [
    ('one_piece', 'Платье / комплект'),
    ('top', 'Верх'),
    ('bottom', 'Низ'),
    ('shoes', 'Обувь'),
  ];

  Future<void> _toggleSaved(BuildContext context) async {
    if (_saving || controller.isLoading) return;
    final id = '${outfit['id']}';
    final saved = _saved;
    final actionController = controller;
    final session = actionController.navigationSessionEpoch;
    final route = ModalRoute.of(context);
    setState(() => _saving = true);
    try {
      final confirmed = saved
          ? await actionController.unsaveOutfit(id)
          : await actionController.saveOutfit(id);
      if (!context.mounted ||
          !identical(controller, actionController) ||
          session != actionController.navigationSessionEpoch ||
          id != '${outfit['id']}') {
        return;
      }
      final error = actionController.error;
      if (confirmed) setState(() => _saved = !saved);
      // Opening a product or another route while this request completes must
      // not let the old detail pop that newer screen or show its old message.
      if (route?.isCurrent != true) return;
      if (!confirmed) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              error ?? 'Не удалось изменить сохранённое. Попробуйте ещё раз.',
            ),
          ),
        );
        return;
      }
      final message = error != null
          ? '${saved ? 'Образ убран из сохранённого' : 'Образ сохранён'}, но список не удалось обновить. '
                'Проверьте интернет и откройте «Сохранённое» снова.'
          : saved
          ? 'Образ убран из сохранённого'
          : 'Образ сохранён';
      final messenger = ScaffoldMessenger.of(context);
      messenger.hideCurrentSnackBar();
      messenger.showSnackBar(SnackBar(content: Text(message)));
      if (saved) {
        Navigator.pop(context);
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  void _openProduct(BuildContext context, Map<String, dynamic> m) {
    final product = prod.Product.fromApi(m);
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => ProductDetailScreen(
          controller: controller,
          card: prod.FeedCard(product: product, reason: ''),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final scenario = '${outfit['style_direction'] ?? 'Образ'}';
    final reason = (outfit['reason'] ?? '')
        .toString()
        .replaceAll(RegExp(r'engine:.*'), '')
        .trim();
    final total = (outfit['total_price'] ?? 0).toString();
    final saved = _saved;
    final pmap = outfit['products'] is Map
        ? Map<String, dynamic>.from(outfit['products'] as Map)
        : <String, dynamic>{};

    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        backgroundColor: AppColors.bg,
        elevation: 0,
        leading: IconButton(
          icon: const Icon(Icons.arrow_back),
          onPressed: () => Navigator.pop(context),
        ),
        actions: [
          IconButton(
            icon: Icon(saved ? Icons.bookmark : Icons.bookmark_border),
            tooltip: saved ? 'Убрать из сохранённого' : 'Сохранить образ',
            onPressed: _saving || controller.isLoading
                ? null
                : () => _toggleSaved(context),
          ),
        ],
      ),
      body: SafeArea(
        top: false,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 0, 20, 24),
          children: [
            Text(outfitScenarioLabel(scenario), style: AppTextStyles.displaySm),
            const SizedBox(height: 6),
            Text(reason, style: AppTextStyles.bodyMuted),
            const SizedBox(height: 16),
            ..._slots.map((slot) {
              final p = pmap[slot.$1];
              if (p is! Map) return const SizedBox.shrink();
              final m = Map<String, dynamic>.from(p);
              return Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: SoftCard(
                  padding: const EdgeInsets.all(12),
                  child: InkWell(
                    borderRadius: BorderRadius.circular(8),
                    onTap: () => _openProduct(context, m),
                    child: Row(
                      children: [
                        SizedBox(
                          width: 76,
                          height: 96,
                          child: ProductFillImage(
                            fit: BoxFit.contain,
                            imageUrl: (m['image_url'] ?? '').toString(),
                            borderRadius: BorderRadius.circular(10),
                          ),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(slot.$2, style: AppTextStyles.caption),
                              Text(
                                (m['title'] ?? 'Товар').toString(),
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                              const SizedBox(height: 4),
                              Text(
                                '${m['brand'] ?? ''} · ${m['price'] ?? ''} ₽',
                                style: AppTextStyles.bodyMuted,
                              ),
                            ],
                          ),
                        ),
                        const Icon(Icons.chevron_right, color: AppColors.muted),
                      ],
                    ),
                  ),
                ),
              );
            }),
            Text('Итого: $total ₽', style: AppTextStyles.price),
            const SizedBox(height: 16),
            PrimaryButton(
              label: 'Купить всё',
              icon: Icons.shopping_bag_outlined,
              onPressed: () => _buyAll(context),
            ),
            const SizedBox(height: 10),
            SecondaryButton(
              label: saved ? 'Убрать из сохранённого' : 'Сохранить образ',
              onPressed: _saving || controller.isLoading
                  ? null
                  : () => _toggleSaved(context),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _buyAll(BuildContext context) async {
    final pmap = outfit['products'] is Map
        ? Map<String, dynamic>.from(outfit['products'] as Map)
        : <String, dynamic>{};
    var opened = 0;
    for (final slot in _slots) {
      final p = pmap[slot.$1];
      if (p is! Map) continue;
      final id = (p['id'] ?? '').toString();
      if (id.isEmpty) continue;
      try {
        if (await openMerchantLink(
          controller.api,
          id,
          surface: 'outfit_detail',
        )) {
          opened++;
        }
      } catch (_) {}
    }
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            opened > 0
                ? 'Открыто ссылок: $opened'
                : 'Не удалось открыть магазины. Попробуйте ещё раз или откройте вещи по отдельности.',
          ),
        ),
      );
    }
  }
}
