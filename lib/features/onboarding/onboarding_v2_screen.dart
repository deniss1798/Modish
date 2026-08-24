import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';

/// Онбординг v2: 3 шага + Wow (60–90 сек).
class OnboardingV2Screen extends StatefulWidget {
  const OnboardingV2Screen({super.key, required this.controller});

  final AppController controller;

  @override
  State<OnboardingV2Screen> createState() => _OnboardingV2ScreenState();
}

class _OnboardingV2ScreenState extends State<OnboardingV2Screen> {
  int _sub =
      0; // 0 step1, 1 photo, 2 confirm, 3 step3, 4 wow progress, 5 wow result

  String _gender = 'female';
  String? _ageGroup;
  final _picker = ImagePicker();
  String? _photoPath;

  Map<String, dynamic>? _photoPending;
  String? _bodyShape;
  String? _colorType;
  String? _heightCategory;

  final Set<String> _styles = {};
  String _priceSegment = 'mass';

  int _wowMsg = 0;
  Timer? _wowTimer;
  static const _wowMessages = [
    'Анализирую тип фигуры...',
    'Подбираю цвета под цветотип...',
    'Ищу вещи в 10+ магазинах...',
    'Собираю образы...',
  ];

  static const _ageGroups = ['18-24', '25-34', '35-44', '45+'];

  static const _styleCards = {
    'casual': 'Casual',
    'minimalism': 'Minimal',
    'smart_casual': 'Smart casual',
    'elegant': 'Elegant',
    'streetwear': 'Street',
    'romantic': 'Romantic',
    'business': 'Business',
    'sporty': 'Sporty',
  };

  static const _priceSegments = {
    'economy': 'Эконом\nдо 4 000 ₽',
    'mass': 'Масс-маркет\nдо 10 000 ₽',
    'mid': 'Средний+\nдо 25 000 ₽',
    'premium': 'Премиум',
  };

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_onCtrl);
    _sub = widget.controller.onboardingV2SubStep;
    _syncWowTimer(_sub);
  }

  @override
  void dispose() {
    _wowTimer?.cancel();
    widget.controller.removeListener(_onCtrl);
    super.dispose();
  }

  void _onCtrl() {
    if (mounted) {
      final next = widget.controller.onboardingV2SubStep;
      setState(() => _sub = next);
      _syncWowTimer(next);
    }
  }

  void _syncWowTimer(int sub) {
    if (sub == 4) {
      _wowTimer ??= Timer.periodic(const Duration(milliseconds: 1400), (_) {
        if (!mounted) return;
        setState(() => _wowMsg = (_wowMsg + 1) % _wowMessages.length);
      });
    } else {
      _wowTimer?.cancel();
      _wowTimer = null;
      _wowMsg = 0;
    }
  }

  Future<void> _step1Next({bool skip = false}) async {
    await widget.controller.onboardingV2Step1(
      gender: _gender,
      ageGroup: skip ? null : _ageGroup,
    );
  }

  Future<void> _pickPhoto() async {
    final x = await _picker.pickImage(
      source: ImageSource.gallery,
      imageQuality: 88,
    );
    if (x == null) return;
    setState(() => _photoPath = x.path);
    await widget.controller.onboardingV2AnalyzePhoto(x.path);
  }

  Future<void> _confirmPhoto() async {
    await widget.controller.onboardingV2ConfirmPhoto(
      bodyShape: _bodyShape,
      colorType: _colorType,
      heightCategory: _heightCategory,
    );
  }

  Future<void> _step3Done({bool skip = false}) async {
    await widget.controller.onboardingV2Step3(
      stylePreferences: skip ? [] : _styles.toList(),
      priceSegment: skip ? null : _priceSegment,
    );
    if (!mounted || widget.controller.error != null) return;
    await widget.controller.onboardingV2CompleteWow();
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    return MobileViewport(
      child: SafeArea(
        child: switch (_sub) {
          0 => _buildStep1(c),
          1 => _buildPhoto(c),
          2 => _buildConfirm(c),
          3 => _buildStep3(c),
          4 => _buildWowProgress(c),
          _ => _buildWowResult(c),
        },
      ),
    );
  }

  Widget _buildStep1(AppController c) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(24, 12, 24, 24),
      children: [
        Row(
          children: [
            if (c.token != null)
              IconButton(
                onPressed: () => c.goToWelcome(),
                icon: const Icon(Icons.arrow_back_ios_new, size: 20),
              ),
            const Spacer(),
            Text('Шаг 1 из 3', style: AppTextStyles.caption),
          ],
        ),
        const SizedBox(height: 8),
        Text(
          'Расскажи о себе',
          style: AppTextStyles.display.copyWith(fontSize: 28),
        ),
        const SizedBox(height: 20),
        const Text('Пол', style: AppTextStyles.sectionTitle),
        const SizedBox(height: 10),
        Row(
          children: [
            Expanded(
              child: _genderCard('female', 'Женщина', Icons.woman_outlined),
            ),
            const SizedBox(width: 12),
            Expanded(child: _genderCard('male', 'Мужчина', Icons.man_outlined)),
          ],
        ),
        const SizedBox(height: 24),
        const Text('Возраст', style: AppTextStyles.sectionTitle),
        const SizedBox(height: 10),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: _ageGroups.map((a) {
            final sel = _ageGroup == a;
            return ChoiceChip(
              label: Text(a),
              selected: sel,
              onSelected: (_) => setState(() => _ageGroup = sel ? null : a),
            );
          }).toList(),
        ),
        const SizedBox(height: 32),
        if (c.error != null) ...[
          Text(c.error!, style: const TextStyle(color: AppColors.error)),
          const SizedBox(height: 12),
        ],
        PrimaryButton(
          label: c.isLoading ? 'Сохраняем...' : 'Далее',
          onPressed: c.isLoading ? null : () => _step1Next(),
        ),
        TextButton(
          onPressed: c.isLoading ? null : () => _step1Next(skip: true),
          child: const Text('Пропустить возраст'),
        ),
      ],
    );
  }

  Widget _genderCard(String value, String label, IconData icon) {
    final sel = _gender == value;
    return InkWell(
      onTap: () => setState(() => _gender = value),
      borderRadius: BorderRadius.circular(16),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        padding: const EdgeInsets.symmetric(vertical: 28),
        decoration: BoxDecoration(
          color: sel
              ? AppColors.accent.withValues(alpha: 0.15)
              : AppColors.card,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(
            color: sel ? AppColors.accent : AppColors.line,
            width: sel ? 2 : 1,
          ),
        ),
        child: Column(
          children: [
            Icon(
              icon,
              size: 40,
              color: sel ? AppColors.accent : AppColors.muted,
            ),
            const SizedBox(height: 8),
            Text(label, style: AppTextStyles.sectionTitle),
          ],
        ),
      ),
    );
  }

  Widget _buildPhoto(AppController c) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(24, 12, 24, 24),
      children: [
        const Text('Шаг 2 из 3', style: AppTextStyles.caption),
        const SizedBox(height: 8),
        Text(
          'Фото в полный рост',
          style: AppTextStyles.display.copyWith(fontSize: 28),
        ),
        const SizedBox(height: 8),
        Text(
          'Нужно для типа фигуры и цветотипа. Можно пропустить — подбор будет менее точным.',
          style: AppTextStyles.bodyMuted,
        ),
        const SizedBox(height: 20),
        SoftCard(
          child: AspectRatio(
            aspectRatio: 3 / 4,
            child: _photoPath != null && !kIsWeb
                ? ClipRRect(
                    borderRadius: BorderRadius.circular(12),
                    child: Image.file(File(_photoPath!), fit: BoxFit.cover),
                  )
                : Center(
                    child: Icon(
                      Icons.add_a_photo_outlined,
                      size: 48,
                      color: AppColors.muted,
                    ),
                  ),
          ),
        ),
        const SizedBox(height: 16),
        if (c.isLoading)
          const Column(
            children: [
              CircularProgressIndicator(color: AppColors.accent),
              SizedBox(height: 12),
              Text('Анализируем фото...', style: AppTextStyles.bodyMuted),
            ],
          )
        else ...[
          PrimaryButton(label: 'Загрузить фото', onPressed: _pickPhoto),
          TextButton(
            onPressed: () => c.onboardingV2SkipPhoto(),
            child: const Text('Пропустить фото'),
          ),
        ],
        if (c.error != null) ...[
          const SizedBox(height: 12),
          Text(c.error!, style: const TextStyle(color: AppColors.error)),
        ],
      ],
    );
  }

  Widget _buildConfirm(AppController c) {
    final pending = _photoPending ?? c.photoConfirmPending ?? {};
    _bodyShape ??= pending['body_shape'] as String?;
    _colorType ??= pending['color_type'] as String?;
    _heightCategory ??= pending['height_category'] as String?;

    final opts = pending['options'] as Map? ?? {};
    final bodyOpts =
        (opts['body_shapes'] as List?)?.cast<String>() ??
        ['Прямоугольник', 'Груша', 'Песочные часы'];
    final colorOpts =
        (opts['color_types'] as List?)?.cast<String>() ??
        ['Холодная зима', 'Тёплая весна', 'Нейтральный'];
    final heightOpts =
        (opts['height_categories'] as List?)?.cast<String>() ??
        ['Низкий', 'Средний', 'Высокий'];

    return ListView(
      padding: const EdgeInsets.fromLTRB(24, 12, 24, 24),
      children: [
        Text(
          'Проверь, правильно ли я тебя понял?',
          style: AppTextStyles.display.copyWith(fontSize: 26),
        ),
        const SizedBox(height: 20),
        _confirmTile(
          Icons.accessibility_new_outlined,
          'Тип фигуры',
          _bodyShape,
          bodyOpts,
          (v) => setState(() => _bodyShape = v),
        ),
        const SizedBox(height: 12),
        _confirmTile(
          Icons.palette_outlined,
          'Цветотип',
          _colorType,
          colorOpts,
          (v) => setState(() => _colorType = v),
        ),
        const SizedBox(height: 12),
        _confirmTile(
          Icons.height_outlined,
          'Рост (примерно)',
          _heightCategory,
          heightOpts,
          (v) => setState(() => _heightCategory = v),
        ),
        const SizedBox(height: 24),
        PrimaryButton(
          label: c.isLoading ? 'Сохраняем...' : 'Да, всё верно',
          onPressed: c.isLoading ? null : _confirmPhoto,
        ),
        if (c.error != null) ...[
          const SizedBox(height: 12),
          Text(c.error!, style: const TextStyle(color: AppColors.error)),
        ],
      ],
    );
  }

  Widget _confirmTile(
    IconData icon,
    String title,
    String? value,
    List<String> options,
    ValueChanged<String> onPick,
  ) {
    return SoftCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, color: AppColors.accent),
              const SizedBox(width: 10),
              Text(title, style: AppTextStyles.sectionTitle),
            ],
          ),
          const SizedBox(height: 8),
          Text(value ?? '—', style: AppTextStyles.body),
          const SizedBox(height: 8),
          DropdownButtonFormField<String>(
            initialValue: options.contains(value) ? value : options.first,
            items: options
                .map(
                  (o) => DropdownMenuItem(
                    value: o,
                    child: Text(o, overflow: TextOverflow.ellipsis),
                  ),
                )
                .toList(),
            onChanged: (v) {
              if (v != null) onPick(v);
            },
            decoration: const InputDecoration(labelText: 'Изменить'),
          ),
        ],
      ),
    );
  }

  Widget _buildStep3(AppController c) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(24, 12, 24, 24),
      children: [
        const Text('Шаг 3 из 3', style: AppTextStyles.caption),
        const SizedBox(height: 8),
        Text(
          'Стиль и бюджет',
          style: AppTextStyles.display.copyWith(fontSize: 28),
        ),
        const SizedBox(height: 16),
        const Text('Какие стили ближе?', style: AppTextStyles.sectionTitle),
        const SizedBox(height: 10),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: _styleCards.entries.map((e) {
            final sel = _styles.contains(e.key);
            return FilterChip(
              label: Text(e.value),
              selected: sel,
              onSelected: (v) {
                setState(() {
                  if (v) {
                    _styles.add(e.key);
                  } else {
                    _styles.remove(e.key);
                  }
                });
              },
            );
          }).toList(),
        ),
        const SizedBox(height: 24),
        const Text('Ценовой сегмент', style: AppTextStyles.sectionTitle),
        const SizedBox(height: 10),
        ..._priceSegments.entries.map((e) {
          final sel = _priceSegment == e.key;
          return Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: ListTile(
              tileColor: sel
                  ? AppColors.accent.withValues(alpha: 0.12)
                  : AppColors.card,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(12),
                side: BorderSide(
                  color: sel ? AppColors.accent : AppColors.line,
                ),
              ),
              title: Text(e.value),
              trailing: sel
                  ? const Icon(Icons.check_circle, color: AppColors.accent)
                  : null,
              onTap: () => setState(() => _priceSegment = e.key),
            ),
          );
        }),
        const SizedBox(height: 24),
        PrimaryButton(
          label: c.isLoading ? 'Собираем образы...' : 'Готово',
          onPressed: c.isLoading ? null : () => _step3Done(),
        ),
        TextButton(
          onPressed: c.isLoading ? null : () => _step3Done(skip: true),
          child: const Text('Пропустить'),
        ),
        if (c.error != null) ...[
          const SizedBox(height: 12),
          Text(c.error!, style: const TextStyle(color: AppColors.error)),
        ],
      ],
    );
  }

  Widget _buildWowProgress(AppController c) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const CircularProgressIndicator(color: AppColors.accent),
            const SizedBox(height: 24),
            Text(
              _wowMessages[_wowMsg.clamp(0, _wowMessages.length - 1)],
              textAlign: TextAlign.center,
              style: AppTextStyles.sectionTitle,
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildWowResult(AppController c) {
    final outfits = c.wowOutfits;
    return ListView(
      padding: const EdgeInsets.fromLTRB(24, 12, 24, 24),
      children: [
        Text(
          'Твой первый персональный образ',
          style: AppTextStyles.display.copyWith(fontSize: 26),
        ),
        const SizedBox(height: 8),
        Text(
          'Мы подобрали луки под твой профиль',
          style: AppTextStyles.bodyMuted,
        ),
        const SizedBox(height: 20),
        ...outfits.map(
          (o) => Padding(
            padding: const EdgeInsets.only(bottom: 16),
            child: SoftCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(o.title, style: AppTextStyles.sectionTitle),
                  if (o.explanation != null && o.explanation!.isNotEmpty) ...[
                    const SizedBox(height: 8),
                    Text(o.explanation!, style: AppTextStyles.bodyMuted),
                  ],
                  const SizedBox(height: 12),
                  Text(
                    '${o.itemCount} вещей в образе',
                    style: AppTextStyles.caption,
                  ),
                ],
              ),
            ),
          ),
        ),
        const SizedBox(height: 16),
        PrimaryButton(
          label: 'В приложение',
          onPressed: () => c.finishOnboardingV2ToHome(),
        ),
      ],
    );
  }
}
