import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';

/// Редактирование параметров подборки (без фото).
class EditProfileScreen extends StatefulWidget {
  const EditProfileScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<EditProfileScreen> createState() => _EditProfileScreenState();
}

class _EditProfileScreenState extends State<EditProfileScreen> {
  late TextEditingController _height;
  late TextEditingController _weight;
  late TextEditingController _size;
  late TextEditingController _budgetMax;
  String _gender = 'menswear';
  final Set<String> _categories = {};
  final Set<String> _styles = {};

  static const _categoryOptions = {
    'футболки': 'Футболки',
    'рубашки': 'Рубашки',
    'джинсы': 'Джинсы',
    'брюки': 'Брюки',
    'обувь': 'Обувь',
    'верхний_слой': 'Верх',
    'спорт': 'Спорт',
  };

  static const _stylePrefs = {
    'daily': 'Каждый день',
    'office': 'В офис',
    'evening': 'Вечер',
    'minimal': 'Минимализм',
  };

  @override
  void initState() {
    super.initState();
    final f = widget.controller.fitProfile;
    _height = TextEditingController(text: '${f['height_cm'] ?? 170}');
    _weight = TextEditingController(
      text: f['weight_kg'] != null ? '${f['weight_kg']}' : '',
    );
    _size = TextEditingController(text: '${f['clothing_size'] ?? 'M'}');
    _budgetMax = TextEditingController(text: '${f['budget_max'] ?? 10000}');
    final g = '${f['gender_target'] ?? 'menswear'}';
    if (g == 'menswear' || g == 'womenswear' || g == 'unisex') {
      _gender = g;
    }
    _categories.addAll(
      List<String>.from(f['interest_categories'] as List? ?? []),
    );
    _styles.addAll(List<String>.from(f['style_scenarios'] as List? ?? []));
    widget.controller.addListener(_rebuild);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_rebuild);
    _height.dispose();
    _weight.dispose();
    _size.dispose();
    _budgetMax.dispose();
    super.dispose();
  }

  void _rebuild() {
    if (mounted) setState(() {});
  }

  Future<void> _save() async {
    if (_gender != 'menswear' && _gender != 'womenswear') {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Выберите «Мужское» или «Женское» — так лента будет точнее'),
        ),
      );
      return;
    }
    final h = int.tryParse(_height.text.trim()) ?? 170;
    final wRaw = _weight.text.trim();
    final w = wRaw.isEmpty ? null : int.tryParse(wRaw);
    final bMax = int.tryParse(_budgetMax.text.trim()) ?? 10000;
    await widget.controller.updateFitProfile(
      height: h,
      weight: w,
      genderTarget: _gender,
      clothingSize: _size.text.trim().isEmpty ? 'M' : _size.text.trim(),
      budgetMin: 0,
      budgetMax: bMax,
      interestCategories: _categories.toList(),
      styleScenarios: _styles.toList(),
    );
    if (!mounted) return;
    if (widget.controller.error != null) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(widget.controller.error!)),
      );
      return;
    }
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Профиль обновлён — лента пересобрана')),
    );
    Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    final busy = widget.controller.isLoading;
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        title: const Text('Мои параметры', style: AppTextStyles.sectionTitle),
        leading: IconButton(
          icon: const Icon(Icons.arrow_back),
          onPressed: () => Navigator.pop(context),
        ),
      ),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Text(
            'Эти данные влияют на ленту и образы. Фото для анализа — по желанию, в профиле.',
            style: AppTextStyles.bodyMuted,
          ),
          const SizedBox(height: 16),
          SoftCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('Пол', style: AppTextStyles.sectionTitle),
                const SizedBox(height: 8),
                Text(
                  'Обязательно для персональной подборки',
                  style: AppTextStyles.caption,
                ),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  children: [
                    _genderChip('menswear', 'Мужское'),
                    _genderChip('womenswear', 'Женское'),
                  ],
                ),
                const SizedBox(height: 16),
                Row(
                  children: [
                    Expanded(
                      child: TextField(
                        controller: _height,
                        keyboardType: TextInputType.number,
                        decoration: const InputDecoration(labelText: 'Рост (см)'),
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: TextField(
                        controller: _weight,
                        keyboardType: TextInputType.number,
                        decoration: const InputDecoration(labelText: 'Вес (кг)'),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _size,
                  decoration: const InputDecoration(labelText: 'Размер одежды'),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _budgetMax,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(labelText: 'Бюджет до (₽)'),
                ),
                const SizedBox(height: 16),
                const Text('Интересующие категории', style: AppTextStyles.sectionTitle),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: _categoryOptions.entries.map((e) {
                    final sel = _categories.contains(e.key);
                    return FilterChip(
                      label: Text(e.value),
                      selected: sel,
                      onSelected: (v) => setState(() {
                        if (v) {
                          _categories.add(e.key);
                        } else {
                          _categories.remove(e.key);
                        }
                      }),
                    );
                  }).toList(),
                ),
                const SizedBox(height: 12),
                const Text('Сценарии', style: AppTextStyles.sectionTitle),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: _stylePrefs.entries.map((e) {
                    final sel = _styles.contains(e.key);
                    return FilterChip(
                      label: Text(e.value),
                      selected: sel,
                      onSelected: (v) => setState(() {
                        if (v) {
                          _styles.add(e.key);
                        } else {
                          _styles.remove(e.key);
                        }
                      }),
                    );
                  }).toList(),
                ),
              ],
            ),
          ),
          const SizedBox(height: 20),
          PrimaryButton(
            label: busy ? 'Сохранение…' : 'Сохранить',
            onPressed: busy ? null : _save,
          ),
        ],
      ),
    );
  }

  Widget _genderChip(String value, String label) {
    final on = _gender == value;
    return ChoiceChip(
      label: Text(label),
      selected: on,
      onSelected: (_) => setState(() => _gender = value),
      selectedColor: AppColors.accent,
      labelStyle: TextStyle(color: on ? AppColors.onAccent : AppColors.ink),
    );
  }
}
