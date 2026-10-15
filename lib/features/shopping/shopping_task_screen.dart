import 'dart:async';
import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/presentation/price_format.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';
import '../../core/widgets/product_image.dart';
import '../products/models.dart';
import '../products/product_detail_screen.dart';
import 'visible_task_product.dart';

class ShoppingTaskScreen extends StatefulWidget {
  const ShoppingTaskScreen({super.key, required this.controller});
  final AppController controller;
  @override
  State<ShoppingTaskScreen> createState() => _ShoppingTaskScreenState();
}

class _ShoppingTaskScreenState extends State<ShoppingTaskScreen> {
  final _text = TextEditingController();
  final _refinement = TextEditingController();
  final _scroll = ScrollController();
  bool _editing = false;
  bool _showRefinement = false;
  @override
  void initState() {
    super.initState();
    widget.controller.shoppingTask.discardExpired();
    _text.text =
        widget.controller.shoppingTask.result?['text'] as String? ?? '';
  }

  @override
  void dispose() {
    _text.dispose();
    _refinement.dispose();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _submit({String? refinement, String? reference}) async {
    if ((refinement ?? _text.text).trim().isEmpty) return;
    FocusScope.of(context).unfocus();
    await widget.controller.shoppingTask.submit(
      refinement ?? _text.text,
      refine: refinement != null,
      referenceProductId: reference,
    );
    if (mounted && widget.controller.shoppingTask.error == null) {
      setState(() {
        _editing = false;
        _showRefinement = false;
        _refinement.clear();
      });
      if (_scroll.hasClients) _scroll.jumpTo(0);
    }
  }

  Future<void> _closeTask(String outcome) async {
    final closed = await widget.controller.shoppingTask.close(outcome);
    if (!mounted || !closed) return;
    FocusScope.of(context).unfocus();
    setState(() {
      _text.clear();
      _refinement.clear();
      _editing = false;
      _showRefinement = false;
    });
    if (_scroll.hasClients) _scroll.jumpTo(0);
  }

  @override
  Widget build(BuildContext context) {
    final task = widget.controller.shoppingTask;
    return AnimatedBuilder(
      animation: task,
      builder: (context, _) {
        final result = task.result;
        final rows = (result?['products'] as List? ?? [])
            .cast<Map<String, dynamic>>();
        final constraints = result?['constraints'] as Map? ?? {};
        final outfits = (result?['outfits'] as List? ?? [])
            .cast<Map<String, dynamic>>();
        return Scaffold(
          appBar: AppBar(
            title: const Text('Ваш запрос', style: AppTextStyles.sectionTitle),
            actions: [
              if (result != null)
                TextButton(
                  onPressed: task.busy ? null : () => _closeTask('completed'),
                  child: const Text('Готово'),
                ),
              if (result != null)
                PopupMenuButton<String>(
                  tooltip: 'Действия запроса',
                  enabled: !task.busy,
                  onSelected: (_) => _closeTask('abandoned'),
                  itemBuilder: (_) => const [
                    PopupMenuItem(value: 'new', child: Text('Новый запрос')),
                  ],
                ),
            ],
          ),
          body: SafeArea(
            child: ListView(
              controller: _scroll,
              padding: const EdgeInsets.all(20),
              children: [
                if (result == null || _editing) ...[
                  const Text('Что вы ищете?', style: AppTextStyles.screenTitle),
                  const SizedBox(height: 12),
                  TextField(
                    key: const ValueKey('shopping-request'),
                    controller: _text,
                    maxLength: 500,
                    minLines: 2,
                    maxLines: 4,
                    textInputAction: TextInputAction.search,
                    decoration: const InputDecoration(
                      hintText: 'Чёрные брюки в офис до 7 000 ₽',
                    ),
                    onSubmitted: (_) => task.busy ? null : _submit(),
                  ),
                  const SizedBox(height: 8),
                  PrimaryButton(
                    label: task.busy ? 'Подбираем…' : 'Найти подходящее',
                    onPressed: task.busy ? null : () => _submit(),
                  ),
                  if (result != null)
                    TextButton(
                      onPressed: () {
                        FocusScope.of(context).unfocus();
                        setState(() {
                          _text.text = '${result['text'] ?? ''}';
                          _editing = false;
                        });
                        if (_scroll.hasClients) _scroll.jumpTo(0);
                      },
                      child: const Text('Отменить изменение'),
                    ),
                  const SizedBox(height: 12),
                  const Text(
                    'Условия этого запроса не меняют ваши постоянные предпочтения.',
                    style: AppTextStyles.caption,
                  ),
                ] else ...[
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(
                        child: Text(
                          '${result['text'] ?? _text.text}',
                          maxLines: 3,
                          overflow: TextOverflow.ellipsis,
                          style: AppTextStyles.sectionTitle,
                        ),
                      ),
                      IconButton(
                        key: const ValueKey('edit-shopping-request'),
                        tooltip: 'Изменить запрос',
                        onPressed: task.busy
                            ? null
                            : () => setState(() => _editing = true),
                        icon: const Icon(Icons.edit_outlined, size: 20),
                      ),
                    ],
                  ),
                ],
                if (task.error != null)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 16),
                    child: Text(
                      task.error!,
                      style: const TextStyle(color: AppColors.error),
                    ),
                  ),
                if (result == null && !task.busy) ...[
                  const SizedBox(height: 20),
                  for (final example in [
                    'Брюки в офис до 7000',
                    'Платье до 8000, желательно чёрное',
                    'Образ на свадьбу до 20000 без платья и каблуков',
                  ])
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      title: Text(example),
                      trailing: const Icon(Icons.north_west),
                      onTap: () {
                        _text.text = example;
                        _submit();
                      },
                    ),
                ],
                if (task.busy)
                  const Padding(
                    padding: EdgeInsets.all(32),
                    child: Center(child: CircularProgressIndicator()),
                  ),
                if (result != null && !task.busy) ...[
                  const SizedBox(height: 12),
                  Wrap(
                    spacing: 12,
                    runSpacing: 6,
                    children: [
                      for (final label in constraintLabels(constraints))
                        Text(label, style: AppTextStyles.bodyMuted),
                    ],
                  ),
                  const SizedBox(height: 16),
                  if (result['message'] != null)
                    Text(
                      '${result['message']}',
                      style: AppTextStyles.bodyMuted,
                    ),
                  if (result['effective_budget_max'] != null &&
                      result['effective_budget_max'] !=
                          constraints['budget_max'])
                    Text(
                      'Бюджет профиля: до ${result['effective_budget_max']} ₽',
                      style: AppTextStyles.caption,
                    ),
                  if (result['state'] == 'ready' ||
                      result['state'] == 'needs_clarification') ...[
                    if (_showRefinement ||
                        result['state'] == 'needs_clarification')
                      TextField(
                        key: const ValueKey('refine-shopping-request'),
                        controller: _refinement,
                        maxLength: 500,
                        decoration: InputDecoration(
                          labelText: 'Уточнить запрос',
                          hintText: result['state'] == 'needs_clarification'
                              ? 'Например: платье до 8 000 ₽'
                              : 'Например: дешевле',
                          suffixIcon: IconButton(
                            tooltip: 'Уточнить',
                            icon: const Icon(Icons.arrow_forward),
                            onPressed: () =>
                                _submit(refinement: _refinement.text),
                          ),
                        ),
                        onSubmitted: (value) => _submit(refinement: value),
                      )
                    else
                      Align(
                        alignment: Alignment.centerLeft,
                        child: TextButton.icon(
                          key: const ValueKey('expand-shopping-refinement'),
                          onPressed: () =>
                              setState(() => _showRefinement = true),
                          icon: const Icon(Icons.tune, size: 18),
                          label: const Text('Уточнить запрос'),
                        ),
                      ),
                    const SizedBox(height: 6),
                  ],
                  if (rows.isNotEmpty) ...[
                    Text(
                      _countLabel(
                        rows.length,
                        'подходящая вещь',
                        'подходящие вещи',
                        'подходящих вещей',
                      ),
                      style: AppTextStyles.sectionTitle,
                    ),
                    const SizedBox(height: 8),
                    Wrap(
                      spacing: 8,
                      children: [
                        for (final label in ['Дешевле', 'Только не оверсайз'])
                          ActionChip(
                            label: Text(label),
                            onPressed: () => _submit(refinement: label),
                          ),
                      ],
                    ),
                  ],
                  for (final row in rows)
                    VisibleTaskProduct(
                      key: ValueKey(
                        '${result['version']}:${row['product']['id']}',
                      ),
                      task: task,
                      productId: '${row['product']['id']}',
                      child: _product(FeedCard.fromApi(row)),
                    ),
                  if (outfits.isNotEmpty)
                    Text(
                      _countLabel(
                        outfits.length,
                        'полный образ',
                        'полных образа',
                        'полных образов',
                      ),
                      style: AppTextStyles.sectionTitle,
                    ),
                  for (final outfit in outfits) _outfit(outfit),
                ],
              ],
            ),
          ),
        );
      },
    );
  }

  Future<void> _feedback(String productId) async {
    const reasons = {
      'not_for_context': 'Не подходит для этой задачи',
      'too_expensive': 'Слишком дорого',
      'not_my_style': 'Не мой стиль',
      'dont_like_color': 'Не мой цвет',
      'dont_like_fit': 'Не моя посадка',
      'dont_like_brand': 'Не нравится бренд',
      'already_have_similar': 'Уже есть похожее',
    };
    final reason = await showModalBottomSheet<String>(
      context: context,
      builder: (context) => SafeArea(
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Padding(
                padding: EdgeInsets.all(20),
                child: Text(
                  'Что не подошло?',
                  style: AppTextStyles.sectionTitle,
                ),
              ),
              const Text(
                'Причина необязательна.',
                style: AppTextStyles.caption,
              ),
              ListTile(
                title: const Text('Без уточнения'),
                onTap: () => Navigator.pop(context),
              ),
              for (final entry in reasons.entries)
                ListTile(
                  title: Text(entry.value),
                  onTap: () => Navigator.pop(context, entry.key),
                ),
            ],
          ),
        ),
      ),
    );
    if (mounted) {
      await widget.controller.shoppingTask.feedback(productId, reason);
    }
  }

  Widget _positiveActions(String productId, {String? outfitId}) {
    final task = widget.controller.shoppingTask;
    return Wrap(
      spacing: 8,
      children: [
        for (final entry in {'like': 'Нравится', 'save': 'Сохранить'}.entries)
          TextButton.icon(
            onPressed: task.busy || task.hasReacted(productId, entry.key)
                ? null
                : () async {
                    await task.react(productId, entry.key, outfitId: outfitId);
                    if (mounted && entry.key == 'save') {
                      await widget.controller.refreshSavedItems();
                    }
                  },
            icon: Icon(
              entry.key == 'like'
                  ? Icons.favorite_border
                  : Icons.bookmark_border,
              size: 18,
            ),
            label: Text(
              task.hasReacted(productId, entry.key)
                  ? (entry.key == 'like' ? 'Нравится ✓' : 'Сохранено')
                  : entry.value,
            ),
          ),
      ],
    );
  }

  String? _outfitSizeLabel(Map<String, dynamic> outfit, String slot) {
    final matches = outfit['size_matches'];
    final evidence = matches is Map ? matches[slot] : null;
    if (evidence is! Map) return null;
    final size = evidence['normalized_size'];
    final system = evidence['size_system'];
    if (size is! String ||
        size.trim().isEmpty ||
        !{'RU', 'EU', 'UK', 'US', 'WAIST', 'LETTER'}.contains(system) ||
        evidence['availability'] != 'available' ||
        evidence['reason'] != null) {
      return null;
    }
    return 'Размер магазина: $size${system == 'LETTER' ? '' : ' ($system)'}';
  }

  String _countLabel(int count, String one, String few, String many) {
    final tail = count % 100;
    final label = tail >= 11 && tail <= 14
        ? many
        : switch (count % 10) {
            1 => one,
            >= 2 && <= 4 => few,
            _ => many,
          };
    return '$count $label';
  }

  void _openOutfitProduct(
    Map<String, dynamic> outfit,
    String slot,
    Map<String, dynamic> product,
  ) {
    final task = widget.controller.shoppingTask;
    final outfitId = '${outfit['id']}';
    unawaited(task.recordOpenProduct('${product['id']}', outfitId: outfitId));
    final sizeLabel = _outfitSizeLabel(outfit, slot);
    final card = FeedCard(
      product: Product.fromApi(product),
      reason: sizeLabel ?? '',
      reasons: sizeLabel == null ? const [] : [sizeLabel],
    );
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => ProductDetailScreen(
          controller: widget.controller,
          card: card,
          eventMeta: task.eventMetaFor(outfitId: outfitId),
        ),
      ),
    );
  }

  Widget _outfitActions(String productId, String outfitId, String slot) {
    final task = widget.controller.shoppingTask;
    return Row(
      children: [
        for (final event in ['like', 'save'])
          IconButton(
            key: ValueKey('outfit-$outfitId-$slot-$event'),
            tooltip: event == 'like'
                ? (task.hasReacted(productId, event)
                      ? 'Вам нравится'
                      : 'Нравится')
                : (task.hasReacted(productId, event)
                      ? 'Сохранено'
                      : 'Сохранить'),
            onPressed: task.busy || task.hasReacted(productId, event)
                ? null
                : () async {
                    await task.react(productId, event, outfitId: outfitId);
                    if (mounted && event == 'save') {
                      await widget.controller.refreshSavedItems();
                    }
                  },
            icon: Icon(
              event == 'like'
                  ? (task.hasReacted(productId, event)
                        ? Icons.favorite
                        : Icons.favorite_border)
                  : (task.hasReacted(productId, event)
                        ? Icons.bookmark
                        : Icons.bookmark_border),
            ),
          ),
        Expanded(
          child: TextButton(
            key: ValueKey('replace-outfit-$outfitId-$slot'),
            onPressed: task.busy
                ? null
                : () => task.replaceItem(outfitId, slot),
            child: const Text('Заменить', maxLines: 1),
          ),
        ),
        IconButton(
          key: ValueKey('outfit-$outfitId-$slot-feedback'),
          tooltip: 'Не подходит',
          onPressed: task.busy ? null : () => _feedback(productId),
          icon: const Icon(Icons.thumb_down_alt_outlined, size: 20),
        ),
      ],
    );
  }

  Widget _outfit(Map<String, dynamic> outfit) {
    const labels = {
      'top': 'Верх',
      'bottom': 'Низ',
      'one_piece': 'Платье',
      'shoes': 'Обувь',
    };
    final task = widget.controller.shoppingTask;
    final products = Map<String, dynamic>.from(outfit['products'] as Map);
    return Padding(
      padding: const EdgeInsets.only(top: 16),
      child: SoftCard(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Итого ${formatPriceAmount((outfit['total_price'] as num?) ?? 0)} ₽',
              style: AppTextStyles.sectionTitle,
            ),
            const SizedBox(height: 8),
            Text('${outfit['reason'] ?? ''}', style: AppTextStyles.caption),
            for (final entry in products.entries)
              Padding(
                padding: const EdgeInsets.only(top: 16),
                child: VisibleTaskProduct(
                  key: ValueKey(
                    '${task.result?['version']}:${outfit['id']}:${entry.key}',
                  ),
                  task: task,
                  productId: '${entry.value['id']}',
                  outfitId: '${outfit['id']}',
                  child: Column(
                    children: [
                      InkWell(
                        key: ValueKey(
                          'detail-outfit-${outfit['id']}-${entry.key}',
                        ),
                        borderRadius: BorderRadius.circular(12),
                        onTap: () => _openOutfitProduct(
                          outfit,
                          entry.key,
                          Map<String, dynamic>.from(entry.value as Map),
                        ),
                        child: Row(
                          children: [
                            SizedBox(
                              width: 96,
                              height: 128,
                              child: ProductFillImage(
                                imageUrl: '${entry.value['image_url'] ?? ''}',
                                borderRadius: BorderRadius.circular(12),
                              ),
                            ),
                            const SizedBox(width: 12),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    labels[entry.key] ?? entry.key,
                                    style: AppTextStyles.caption,
                                  ),
                                  const SizedBox(height: 4),
                                  Text(
                                    '${entry.value['title']}',
                                    maxLines: 3,
                                    overflow: TextOverflow.ellipsis,
                                  ),
                                  const SizedBox(height: 8),
                                  Text(
                                    '${formatPriceAmount((entry.value['price'] as num?) ?? 0)} ₽',
                                    style: const TextStyle(
                                      fontWeight: FontWeight.w600,
                                    ),
                                  ),
                                  if (_outfitSizeLabel(outfit, entry.key)
                                      case final String sizeLabel)
                                    Text(
                                      sizeLabel,
                                      style: AppTextStyles.caption,
                                    ),
                                  if ('${entry.value['shop_label'] ?? ''}'
                                      .isNotEmpty)
                                    Text(
                                      '${entry.value['shop_label']}',
                                      style: AppTextStyles.caption,
                                    ),
                                ],
                              ),
                            ),
                            const Icon(
                              Icons.chevron_right,
                              size: 20,
                              color: AppColors.muted,
                            ),
                          ],
                        ),
                      ),
                      _outfitActions(
                        '${entry.value['id']}',
                        '${outfit['id']}',
                        entry.key,
                      ),
                    ],
                  ),
                ),
              ),
            const SizedBox(height: 12),
            const Text(
              'При замене остальные вещи сохраняются.',
              style: AppTextStyles.caption,
            ),
            const SizedBox(height: 12),
            SecondaryButton(
              label: outfit['is_saved'] == true
                  ? 'Образ сохранён'
                  : 'Сохранить образ',
              onPressed: outfit['is_saved'] == true || task.busy
                  ? null
                  : () async {
                      await task.saveOutfit('${outfit['id']}');
                      if (!mounted) return;
                      await widget.controller.refreshSavedItems();
                    },
            ),
          ],
        ),
      ),
    );
  }

  Widget _product(FeedCard card) => Padding(
    padding: const EdgeInsets.only(top: 16),
    child: SoftCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          InkWell(
            onTap: () {
              unawaited(
                widget.controller.shoppingTask.recordOpenProduct(
                  card.product.id,
                ),
              );
              Navigator.push(
                context,
                MaterialPageRoute(
                  builder: (_) => ProductDetailScreen(
                    controller: widget.controller,
                    card: card,
                    eventMeta: widget.controller.shoppingTask.eventMeta,
                  ),
                ),
              );
            },
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SizedBox(
                  width: 100,
                  height: 140,
                  child: ProductFillImage(imageUrl: card.product.imageUrl),
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        card.product.title,
                        maxLines: 3,
                        overflow: TextOverflow.ellipsis,
                        style: AppTextStyles.sectionTitle,
                      ),
                      const SizedBox(height: 8),
                      Text('${formatPriceAmount(card.product.price)} ₽'),
                      if (card.product.shopLabel.isNotEmpty)
                        Text(
                          card.product.shopLabel,
                          style: AppTextStyles.caption,
                        ),
                      for (final reason in card.reasons.take(2))
                        Padding(
                          padding: const EdgeInsets.only(top: 6),
                          child: Text(reason, style: AppTextStyles.caption),
                        ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 10),
          _positiveActions(card.product.id),
          Wrap(
            spacing: 8,
            children: [
              TextButton(
                onPressed: () => _submit(
                  refinement: 'Другого цвета',
                  reference: card.product.id,
                ),
                child: const Text('Другой цвет'),
              ),
              TextButton(
                onPressed: () =>
                    _submit(refinement: 'Дешевле', reference: card.product.id),
                child: const Text('Дешевле этой'),
              ),
              TextButton(
                onPressed: () => _feedback(card.product.id),
                child: const Text('Не подходит'),
              ),
            ],
          ),
        ],
      ),
    ),
  );
}

List<String> constraintLabels(Map constraints) {
  const names = {
    'office': 'Офис',
    'wedding': 'Свадьба',
    'evening': 'Вечер',
    'daily': 'Каждый день',
    'date': 'Свидание',
    'vacation': 'Отпуск',
    'walk': 'Прогулка',
    'верхний_слой': 'Верхняя одежда',
    'black': 'чёрный',
    'white': 'белый',
    'cream': 'бежевый',
    'navy': 'тёмно-синий',
    'blue': 'синий',
    'grey': 'серый',
    'brown': 'коричневый',
    'green': 'зелёный',
    'red': 'красный',
    'pink': 'розовый',
    'burgundy': 'бордовый',
    'yellow': 'жёлтый',
    'orange': 'оранжевый',
    'purple': 'фиолетовый',
    'multicolor': 'разноцветный',
    'oversized': 'оверсайз',
    'wide': 'широкий крой',
    'straight': 'прямой крой',
    'heels': 'каблуки',
    'cotton': 'хлопок',
    'linen': 'лён',
    'wool': 'шерсть',
    'silk': 'шёлк',
    'viscose': 'вискоза',
    'polyester': 'полиэстер',
    'leather': 'кожа',
    'mini': 'мини',
    'midi': 'миди',
    'maxi': 'макси',
    'casual': 'повседневный',
    'business': 'деловой',
    'minimalism': 'минимализм',
  };
  String name(dynamic value) => names[value] ?? '$value';
  final labels = <String>[
    for (final c in constraints['requested_categories'] as List? ?? []) name(c),
    for (final c in constraints['excluded_categories'] as List? ?? [])
      'Исключено: ${name(c)}',
    if (constraints['occasion'] != null) name(constraints['occasion']),
    if (constraints['budget_min'] != null) 'От ${constraints['budget_min']} ₽',
    if (constraints['budget_max'] != null) 'До ${constraints['budget_max']} ₽',
    for (final c in constraints['colors'] as List? ?? []) 'Цвет: ${name(c)}',
    for (final c in constraints['preferred_colors'] as List? ?? [])
      'Желательно: ${name(c)}',
    for (final c in constraints['excluded_colors'] as List? ?? [])
      'Без цвета: ${name(c)}',
    for (final c in constraints['preferred_styles'] as List? ?? [])
      'Ближе к стилю: ${name(c)}',
  ];
  for (final key in ['required_features', 'excluded_features']) {
    for (final values in (constraints[key] as Map? ?? {}).values) {
      for (final v in values as List) {
        labels.add(
          '${key == 'excluded_features' ? 'Без' : 'Нужно'}: ${name(v)}',
        );
      }
    }
  }
  return labels;
}
