import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';

/// P4: параметры до регистрации — применяются к fit-profile после входа.
class FitQuizScreen extends StatefulWidget {
  const FitQuizScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<FitQuizScreen> createState() => _FitQuizScreenState();
}

class _FitQuizScreenState extends State<FitQuizScreen> {
  String _gender = 'unisex';
  String _size = 'M';
  final _budgetMax = TextEditingController(text: '10000');
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
    'minimal': 'Минимализм',
    'classic': 'Классика',
    'street': 'Стрит',
    'smart_casual': 'Smart casual',
  };

  @override
  void dispose() {
    _budgetMax.dispose();
    super.dispose();
  }

  void _continue() {
    final bMax = int.tryParse(_budgetMax.text.trim()) ?? 10000;
    widget.controller.setPendingFitPrefs(
      genderTarget: _gender,
      clothingSize: _size,
      budgetMin: 0,
      budgetMax: bMax,
      interestCategories: _categories.toList(),
      styleScenarios: _styles.toList(),
    );
    widget.controller.goToAuth(registerMode: true);
  }

  @override
  Widget build(BuildContext context) {
    return MobileViewport(
      child: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(24, 16, 24, 24),
          children: [
            const Center(child: GoldWordmark(fontSize: 32)),
            const SizedBox(height: 20),
            Text('Ваш профиль', style: AppTextStyles.display.copyWith(fontSize: 28)),
            const SizedBox(height: 8),
            Text(
              'Ответы сразу влияют на подборку в ленте',
              style: AppTextStyles.bodyMuted,
            ),
            const SizedBox(height: 20),
            SoftCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('Пол', style: AppTextStyles.sectionTitle),
                  const SizedBox(height: 8),
                  Wrap(
                    spacing: 8,
                    children: [
                      _genderChip('menswear', 'Мужское'),
                      _genderChip('womenswear', 'Женское'),
                      _genderChip('unisex', 'Универсальное'),
                    ],
                  ),
                  const SizedBox(height: 16),
                  const Text('Размер', style: AppTextStyles.sectionTitle),
                  const SizedBox(height: 8),
                  Wrap(
                    spacing: 8,
                    children: ['XS', 'S', 'M', 'L', 'XL', 'XXL'].map((s) {
                      final on = _size == s;
                      return ChoiceChip(
                        label: Text(s),
                        selected: on,
                        onSelected: (_) => setState(() => _size = s),
                        selectedColor: AppColors.accent,
                        labelStyle: TextStyle(
                          color: on ? AppColors.onAccent : AppColors.ink,
                        ),
                      );
                    }).toList(),
                  ),
                  const SizedBox(height: 16),
                  TextField(
                    controller: _budgetMax,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(
                      labelText: 'Бюджет до (₽)',
                    ),
                  ),
                  const SizedBox(height: 16),
                  const Text('Категории', style: AppTextStyles.sectionTitle),
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
                  const Text('Стиль', style: AppTextStyles.sectionTitle),
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
            PrimaryButton(label: 'Продолжить', onPressed: _continue),
            const SizedBox(height: 10),
            TextButton(
              onPressed: () => widget.controller.goToAuth(registerMode: true),
              child: const Text('Пропустить', style: TextStyle(color: AppColors.muted)),
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
      labelStyle: TextStyle(color: on ? AppColors.onAccent : AppColors.ink),
    );
  }
}
