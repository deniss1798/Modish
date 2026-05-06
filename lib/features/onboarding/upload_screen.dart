import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/widgets/modish_widgets.dart';

const _maxBytes = 5 * 1024 * 1024;

/// Ключи совпадают с `normalize_category` на бэкенде (фильтр ленты).
const _interestOptions = <String, String>{
  'футболки': 'Футболки',
  'рубашки': 'Рубашки',
  'джинсы': 'Джинсы',
  'брюки': 'Брюки',
  'обувь': 'Обувь',
  'верхний_слой': 'Верх',
};

const _scenarioOptions = <String, String>{
  'daily': 'Повседневно',
  'office': 'Офис',
  'evening': 'Вечер',
};

class UploadScreen extends StatefulWidget {
  const UploadScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<UploadScreen> createState() => _UploadScreenState();
}

class _UploadScreenState extends State<UploadScreen> {
  final picker = ImagePicker();
  String? _localError;
  final _height = TextEditingController(text: '170');
  final _size = TextEditingController(text: 'M');
  final _budgetMax = TextEditingController(text: '10000');
  String _genderTarget = 'unisex';
  final Set<String> _interestCategories = {};
  final Set<String> _styleScenarios = {};

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_onCtrl);
  }

  @override
  void dispose() {
    _height.dispose();
    _size.dispose();
    _budgetMax.dispose();
    widget.controller.removeListener(_onCtrl);
    super.dispose();
  }

  void _onCtrl() => setState(() {});

  Future<void> _validateAndSet(String path) async {
    _localError = null;
    if (!kIsWeb) {
      final len = await File(path).length();
      if (len > _maxBytes) {
        setState(() => _localError = 'Размер файла не более 5 MB');
        return;
      }
    }
    widget.controller.addPhotoPath(path);
    setState(() {});
  }

  Future<void> _pick(ImageSource source) async {
    final image = await picker.pickImage(
      source: source,
      imageQuality: 88,
      maxWidth: 1800,
    );
    if (image == null) return;
    await _validateAndSet(image.path);
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    final path = c.selectedPhotoPath;
    return MobileViewport(
      child: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(22, 36, 22, 24),
          children: [
            Row(
              children: [
                IconButton(
                  onPressed: () => widget.controller.exitUploadFlow(),
                  icon: const Icon(Icons.arrow_back),
                ),
                const Spacer(),
                const Brand(),
                const Spacer(),
                const SizedBox(width: 48),
              ],
            ),
            const SizedBox(height: 24),
            const Text(
              'Создадим ваш\nпервый стиль-профиль',
              textAlign: TextAlign.center,
              style: TextStyle(
                fontFamily: 'Georgia',
                fontSize: 42,
                color: AppColors.ink,
              ),
            ),
            const SizedBox(height: 20),
            SoftCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Какие рекомендации показывать?',
                    style: TextStyle(fontWeight: FontWeight.w700, fontSize: 18),
                  ),
                  const SizedBox(height: 12),
                  Wrap(
                    spacing: 10,
                    runSpacing: 10,
                    children:
                        const [
                          ('menswear', 'Мужские вещи'),
                          ('womenswear', 'Женские вещи'),
                          ('unisex', 'Универсальные вещи'),
                          ('unknown', 'Пока не знаю'),
                        ].map((value) {
                          final selected = c.styleTarget == value.$1;
                          return ChoiceChip(
                            label: Text(value.$2),
                            selected: selected,
                            onSelected: (_) => c.setStyleTarget(value.$1),
                            selectedColor: AppColors.accent.withValues(
                              alpha: .1,
                            ),
                          );
                        }).toList(),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 14),
            SoftCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (c.photoPaths.isNotEmpty && !kIsWeb)
                    ClipRRect(
                      borderRadius: BorderRadius.circular(16),
                      child: Image.file(
                        File(c.photoPaths.first),
                        height: 220,
                        fit: BoxFit.cover,
                      ),
                    )
                  else if (c.photoPaths.isNotEmpty && kIsWeb)
                    const Padding(
                      padding: EdgeInsets.all(16),
                      child: Text('Превью выбрано (web)'),
                    )
                  else ...[
                    const SizedBox(height: 24),
                    Icon(Icons.person_outline, size: 76, color: AppColors.muted),
                    const SizedBox(height: 12),
                    const Text(
                      'Загрузите или снимите фото',
                      textAlign: TextAlign.center,
                      style: TextStyle(fontSize: 18),
                    ),
                    const SizedBox(height: 8),
                    const Text(
                      'Форматы: JPG, PNG, WebP · до 5 MB',
                      textAlign: TextAlign.center,
                      style: TextStyle(color: AppColors.muted, fontSize: 13),
                    ),
                  ],
                  const SizedBox(height: 16),
                  if (c.photoPaths.length > 1)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 10),
                      child: Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: c.photoPaths
                            .map(
                              (p) => Chip(
                                label: Text(p.split(RegExp(r'[/\\]')).last),
                                onDeleted: c.isLoading ? null : () => c.removePhotoPath(p),
                              ),
                            )
                            .toList(),
                      ),
                    ),
                  Row(
                    children: [
                      Expanded(
                        child: OutlinedButton.icon(
                          onPressed: c.isLoading ? null : () => _pick(ImageSource.gallery),
                          icon: const Icon(Icons.image_outlined),
                          label: const Text('Галерея'),
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: OutlinedButton.icon(
                          onPressed: c.isLoading ? null : () => _pick(ImageSource.camera),
                          icon: const Icon(Icons.photo_camera_outlined),
                          label: const Text('Камера'),
                        ),
                      ),
                    ],
                  ),
                  if (c.photoPaths.isNotEmpty) ...[
                    const SizedBox(height: 10),
                    TextButton(
                      onPressed: c.isLoading
                          ? null
                          : () {
                              c.photoPaths.toList().forEach(c.removePhotoPath);
                              setState(() => _localError = null);
                            },
                      child: const Text('Удалить фото'),
                    ),
                  ],
                ],
              ),
            ),
            const SizedBox(height: 14),
            SoftCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Параметры (быстро)',
                    style: TextStyle(fontWeight: FontWeight.w700, fontSize: 18),
                  ),
                  const SizedBox(height: 12),
                  Row(
                    children: [
                      Expanded(
                        child: TextField(
                          controller: _height,
                          keyboardType: TextInputType.number,
                          decoration: const InputDecoration(
                            labelText: 'Рост (см)',
                            prefixIcon: Icon(Icons.height),
                          ),
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: TextField(
                          controller: _size,
                          decoration: const InputDecoration(
                            labelText: 'Размер',
                            prefixIcon: Icon(Icons.straighten),
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  TextField(
                    controller: _budgetMax,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(
                      labelText: 'Бюджет до (₽)',
                      prefixIcon: Icon(Icons.payments_outlined),
                    ),
                  ),
                  const SizedBox(height: 12),
                  Wrap(
                    spacing: 10,
                    runSpacing: 10,
                    children: [
                      ChoiceChip(
                        label: const Text('Мужское'),
                        selected: _genderTarget == 'menswear',
                        onSelected: (_) => setState(() => _genderTarget = 'menswear'),
                      ),
                      ChoiceChip(
                        label: const Text('Женское'),
                        selected: _genderTarget == 'womenswear',
                        onSelected: (_) => setState(() => _genderTarget = 'womenswear'),
                      ),
                      ChoiceChip(
                        label: const Text('Универсальное'),
                        selected: _genderTarget == 'unisex',
                        onSelected: (_) => setState(() => _genderTarget = 'unisex'),
                      ),
                    ],
                  ),
                  const SizedBox(height: 16),
                  Text(
                    'Категории в ленте (необязательно)',
                    style: TextStyle(
                      fontWeight: FontWeight.w600,
                      fontSize: 14,
                      color: AppColors.ink.withValues(alpha: 0.85),
                    ),
                  ),
                  const SizedBox(height: 8),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: _interestOptions.entries.map((e) {
                      final sel = _interestCategories.contains(e.key);
                      return FilterChip(
                        label: Text(e.value),
                        selected: sel,
                        onSelected: (v) => setState(() {
                          if (v) {
                            _interestCategories.add(e.key);
                          } else {
                            _interestCategories.remove(e.key);
                          }
                        }),
                      );
                    }).toList(),
                  ),
                  const SizedBox(height: 12),
                  Text(
                    'Сценарии (необязательно)',
                    style: TextStyle(
                      fontWeight: FontWeight.w600,
                      fontSize: 14,
                      color: AppColors.ink.withValues(alpha: 0.85),
                    ),
                  ),
                  const SizedBox(height: 8),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: _scenarioOptions.entries.map((e) {
                      final sel = _styleScenarios.contains(e.key);
                      return FilterChip(
                        label: Text(e.value),
                        selected: sel,
                        onSelected: (v) => setState(() {
                          if (v) {
                            _styleScenarios.add(e.key);
                          } else {
                            _styleScenarios.remove(e.key);
                          }
                        }),
                      );
                    }).toList(),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 14),
            PrimaryButton(
              label: 'Начать анализ',
              icon: Icons.auto_awesome,
              onPressed: c.photoPaths.isNotEmpty && !c.isLoading
                  ? () async {
                      final h = int.tryParse(_height.text.trim()) ?? 170;
                      final bMax = int.tryParse(_budgetMax.text.trim()) ?? 10000;
                      await c.updateFitProfile(
                        height: h,
                        genderTarget: _genderTarget,
                        clothingSize: _size.text.trim().isEmpty ? 'M' : _size.text.trim(),
                        budgetMin: 0,
                        budgetMax: bMax,
                        interestCategories: _interestCategories.toList(),
                        styleScenarios: _styleScenarios.toList(),
                      );
                      await c.analyze();
                    }
                  : null,
            ),
            if (_localError != null) ...[
              const SizedBox(height: 12),
              Text(_localError!, style: const TextStyle(color: AppColors.accent)),
            ],
            if (c.error != null) ...[
              const SizedBox(height: 12),
              Text(c.error!, style: const TextStyle(color: AppColors.accent)),
            ],
          ],
        ),
      ),
    );
  }
}
