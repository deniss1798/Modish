import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';
import 'fit_sizes.dart';

/// Редактирование параметров подборки (без фото).
class EditProfileScreen extends StatefulWidget {
  const EditProfileScreen({
    super.key,
    required this.controller,
    this.setupMode = false,
  });
  final AppController controller;
  final bool setupMode;

  @override
  State<EditProfileScreen> createState() => _EditProfileScreenState();
}

class _EditProfileScreenState extends State<EditProfileScreen> {
  late TextEditingController _height;
  late TextEditingController _weight;
  late TextEditingController _size;
  late TextEditingController _topSize;
  late TextEditingController _bottomSize;
  late TextEditingController _shoe;
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
    'платья': 'Платья',
    'юбки': 'Юбки',
    'джемперы': 'Трикотаж',
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
    _height = TextEditingController(
      text: (f['size_profile'] as Map?)?['height_confirmed'] == false
          ? ''
          : '${f['height'] ?? f['height_cm'] ?? ''}',
    );
    _weight = TextEditingController(
      text: '${f['weight'] ?? f['weight_kg'] ?? ''}',
    );
    String initialSize(String slot) {
      final selected = sizeFieldValue(f, slot);
      if (f['size_profile'] != null) return selected;
      return selected.isNotEmpty ? selected : '${f['clothing_size'] ?? ''}';
    }

    _size = TextEditingController(text: initialSize('one_piece'));
    _topSize = TextEditingController(text: initialSize('top'));
    _bottomSize = TextEditingController(text: initialSize('bottom'));
    _shoe = TextEditingController(
      text: f['size_profile'] != null
          ? sizeFieldValue(f, 'shoes')
          : '${f['shoe_size'] ?? widget.controller.activationStatus['shoe_size'] ?? ''}',
    );
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
    _topSize.dispose();
    _bottomSize.dispose();
    _shoe.dispose();
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
          content: Text(
            'Выберите «Мужское» или «Женское» — так лента будет точнее',
          ),
        ),
      );
      return;
    }
    final h = int.tryParse(_height.text.trim());
    final top = sizeChoice(_topSize.text);
    final bottom = sizeChoice(_bottomSize.text);
    final dress = sizeChoice(_size.text);
    final shoes = sizeChoice(_shoe.text, footwear: true);
    if (h == null ||
        h < 50 ||
        h > 250 ||
        top == null ||
        bottom == null ||
        dress == null ||
        shoes == null ||
        top['system'] == 'WAIST' ||
        dress['system'] == 'WAIST') {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text(
            'Укажите рост и размеры верха, низа, платья и обуви. Например: M, RU 48, обувь EU 38.',
          ),
        ),
      );
      return;
    }
    final wRaw = _weight.text.trim();
    final w = wRaw.isEmpty ? null : int.tryParse(wRaw);
    final bMax = int.tryParse(_budgetMax.text.trim());
    if (bMax == null || bMax <= 0) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Укажите бюджет больше нуля.')),
      );
      return;
    }
    await widget.controller.updateFitProfile(
      height: h,
      weight: w,
      genderTarget: _gender,
      clothingSize: dress['value']!,
      shoeSize: shoes['value']!,
      sizes: {'top': top, 'bottom': bottom, 'one_piece': dress, 'shoes': shoes},
      budgetMin: 0,
      budgetMax: bMax,
      interestCategories: _categories.toList(),
      styleScenarios: _styles.toList(),
    );
    if (!mounted) return;
    if (widget.controller.error != null) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(widget.controller.error!)));
      return;
    }
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Профиль обновлён — лента пересобрана')),
    );
    if (widget.setupMode) {
      await widget.controller.finishFitConfirmation();
    } else {
      Navigator.pop(context);
    }
  }

  @override
  Widget build(BuildContext context) {
    final busy = widget.controller.isLoading;
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        title: const Text('Мои параметры', style: AppTextStyles.sectionTitle),
        automaticallyImplyLeading: false,
        leading: widget.setupMode
            ? null
            : IconButton(
                icon: const Icon(Icons.arrow_back),
                onPressed: () => Navigator.pop(context),
              ),
      ),
      bottomNavigationBar: SafeArea(
        top: false,
        minimum: const EdgeInsets.fromLTRB(20, 12, 20, 16),
        child: PrimaryButton(
          key: const ValueKey('save-profile'),
          label: busy ? 'Сохранение…' : 'Сохранить',
          onPressed: busy ? null : _save,
        ),
      ),
      body: SafeArea(
        top: false,
        bottom: false,
        child: ListView(
          padding: const EdgeInsets.all(20),
          children: [
            Text(
              widget.setupMode
                  ? 'Подтвердите размеры и рост. Сохранённые вещи и ваши предпочтения останутся на месте.'
                  : 'Укажите размеры по категориям. Фото и дополнительные мерки не обязательны.',
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
                      if (widget.controller.fitProfile['activation_segment'] ==
                              null &&
                          widget
                                  .controller
                                  .activationStatus['preferences_ready'] !=
                              true)
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
                          decoration: const InputDecoration(
                            labelText: 'Рост (см)',
                          ),
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: TextField(
                          controller: _weight,
                          keyboardType: TextInputType.number,
                          decoration: const InputDecoration(
                            labelText: 'Вес (кг)',
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    key: const ValueKey('fit-top-size'),
                    controller: _topSize,
                    decoration: const InputDecoration(
                      labelText: 'Верх (буквы / RU)',
                      hintText: 'M, RU 48 или EU 42',
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    key: const ValueKey('fit-bottom-size'),
                    controller: _bottomSize,
                    decoration: const InputDecoration(
                      labelText: 'Низ (буквы / RU / W)',
                      hintText: 'M, RU 48 или W28',
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    key: const ValueKey('fit-dress-size'),
                    controller: _size,
                    decoration: const InputDecoration(
                      labelText: 'Платья / общий (буквы / RU)',
                    ),
                  ),
                  ...[
                    const SizedBox(height: 12),
                    TextField(
                      key: const ValueKey('fit-shoe-size'),
                      controller: _shoe,
                      keyboardType: TextInputType.number,
                      decoration: const InputDecoration(
                        labelText: 'Обувь (EU)',
                        hintText: 'EU 38',
                      ),
                    ),
                  ],
                  const SizedBox(height: 12),
                  TextField(
                    controller: _budgetMax,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(
                      labelText: 'Бюджет до (₽)',
                    ),
                  ),
                  const SizedBox(height: 16),
                  const Text(
                    'Интересующие категории',
                    style: AppTextStyles.sectionTitle,
                  ),
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
          ],
        ),
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
      labelStyle: TextStyle(
        fontFamily: AppFonts.bodyFamily,
        color: on ? AppColors.onAccent : AppColors.ink,
      ),
    );
  }
}
