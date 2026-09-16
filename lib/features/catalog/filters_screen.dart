import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';

class FiltersScreen extends StatefulWidget {
  const FiltersScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<FiltersScreen> createState() => _FiltersScreenState();
}

class _FiltersScreenState extends State<FiltersScreen> {
  final _categories = ['Верхняя одежда', 'Топы', 'Низ', 'Обувь'];
  final _sizes = [
    'XS',
    'S',
    'M',
    'L',
    'XL',
    'XXL',
    'XXXL',
    '36',
    '37',
    '38',
    '39',
    '40',
    '41',
    '42',
    '43',
    '44',
    '46',
    '48',
    '50',
    '52',
    '54',
    '56',
  ];
  final _colors = [
    'Черный',
    'Белый',
    'Бежевый',
    'Серый',
    'Синий',
    'Красный',
    'Зеленый',
  ];
  final Set<String> _selCat = {};
  final Set<String> _selSize = {};
  final Set<String> _selColor = {};
  RangeValues _price = const RangeValues(0, 100000);
  bool _priceEnabled = false;
  static const categoryKeys = {
    'Верхняя одежда': 'outerwear',
    'Топы': 'tops',
    'Низ': 'bottoms',
    'Обувь': 'shoes',
  };
  @override
  void initState() {
    super.initState();
    final c = widget.controller;
    _selCat.addAll(
      categoryKeys.keys.where(
        (k) => c.feedFilterCategories.contains(categoryKeys[k]),
      ),
    );
    _selSize.addAll(c.feedFilterSizes);
    _selColor.addAll(c.feedFilterColors);
    _priceEnabled = c.feedMinPrice != null || c.feedMaxPrice != null;
    _price = RangeValues(
      (c.feedMinPrice ?? 0).toDouble().clamp(0, 100000),
      (c.feedMaxPrice ?? 100000).toDouble().clamp(0, 100000),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        backgroundColor: AppColors.bg,
        elevation: 0,
        leading: IconButton(
          icon: const Icon(Icons.arrow_back),
          onPressed: () => Navigator.pop(context),
        ),
        title: const Text('Фильтры', style: AppTextStyles.sectionTitle),
        actions: [
          TextButton(
            onPressed: () => setState(() {
              _selCat.clear();
              _selSize.clear();
              _selColor.clear();
              _price = const RangeValues(0, 100000);
              _priceEnabled = false;
            }),
            child: const Text(
              'Сбросить',
              style: TextStyle(color: AppColors.accent),
            ),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 0, 20, 100),
        children: [
          Text('Категории', style: AppTextStyles.sectionTitle),
          const SizedBox(height: 10),
          Row(
            children: _categories.map((c) {
              final on = _selCat.contains(c);
              return Expanded(
                child: Padding(
                  padding: const EdgeInsets.only(right: 6),
                  child: InkWell(
                    borderRadius: BorderRadius.circular(8),
                    onTap: () =>
                        setState(() => on ? _selCat.remove(c) : _selCat.add(c)),
                    child: Container(
                      height: 64,
                      decoration: BoxDecoration(
                        color: on ? AppColors.accent : AppColors.card,
                        border: Border.all(
                          color: on ? AppColors.accent : AppColors.line,
                        ),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Icon(
                            _categoryIcon(c),
                            size: 20,
                            color: on ? AppColors.onAccent : AppColors.ink,
                          ),
                          const SizedBox(height: 5),
                          Text(
                            c,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              fontSize: 9,
                              color: on ? AppColors.onAccent : AppColors.ink,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
              );
            }).toList(),
          ),
          const SizedBox(height: 24),
          Text('Цена', style: AppTextStyles.sectionTitle),
          RangeSlider(
            values: _price,
            min: 0,
            max: 100000,
            divisions: 200,
            labels: RangeLabels(
              '${_price.start.round()}',
              '${_price.end.round()}',
            ),
            onChanged: (v) => setState(() {
              _price = v;
              _priceEnabled = true;
            }),
          ),
          Text(
            _priceEnabled
                ? 'от ${_price.start.round()} ₽ до ${_price.end.round()} ₽'
                : 'Любая цена',
            style: AppTextStyles.bodyMuted,
          ),
          const SizedBox(height: 24),
          Text('Размер', style: AppTextStyles.sectionTitle),
          const SizedBox(height: 10),
          Wrap(
            spacing: 8,
            children: _sizes.map((s) {
              final on = _selSize.contains(s);
              return ChoiceChip(
                label: Text(s),
                selected: on,
                onSelected: (_) =>
                    setState(() => on ? _selSize.remove(s) : _selSize.add(s)),
              );
            }).toList(),
          ),
          const SizedBox(height: 24),
          Text('Цвет', style: AppTextStyles.sectionTitle),
          const SizedBox(height: 10),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: _colors.map((c) {
              final on = _selColor.contains(c);
              return InkWell(
                borderRadius: BorderRadius.circular(18),
                onTap: () =>
                    setState(() => on ? _selColor.remove(c) : _selColor.add(c)),
                child: Tooltip(
                  message: c,
                  child: Container(
                    width: 40,
                    height: 40,
                    decoration: BoxDecoration(
                      color: _swatch(c),
                      shape: BoxShape.circle,
                      border: Border.all(
                        color: on ? AppColors.accent : AppColors.line,
                        width: on ? 2 : 1,
                      ),
                    ),
                    child: on
                        ? Icon(
                            Icons.check,
                            size: 16,
                            color: c == 'Белый' ? AppColors.ink : Colors.white,
                          )
                        : null,
                  ),
                ),
              );
            }).toList(),
          ),
        ],
      ),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: PrimaryButton(
            label: 'Показать товары',
            onPressed: () {
              widget.controller.applyFeedFilters(
                minPrice: _priceEnabled ? _price.start.round() : null,
                maxPrice: _priceEnabled ? _price.end.round() : null,
                categories: _selCat.map((c) => categoryKeys[c]!).toList(),
                sizes: _selSize.toList(),
                colors: _selColor.toList(),
              );
              Navigator.pop(context);
            },
          ),
        ),
      ),
    );
  }
}

IconData _categoryIcon(String value) {
  if (value.contains('Топ')) return Icons.checkroom_outlined;
  if (value.contains('Низ')) return Icons.view_week_outlined;
  if (value.contains('Обув')) return Icons.ice_skating_outlined;
  if (value.contains('Сум')) return Icons.shopping_bag_outlined;
  return Icons.dry_cleaning_outlined;
}

Color _swatch(String value) {
  return switch (value) {
    'Черный' => Colors.black,
    'Белый' => Colors.white,
    'Бежевый' => const Color(0xFFD8D0C5),
    'Серый' => const Color(0xFF9A9A9A),
    'Синий' => const Color(0xFF45546D),
    'Красный' => const Color(0xFFB54A4A),
    'Зеленый' => const Color(0xFF4E7A55),
    _ => AppColors.chipBg,
  };
}
