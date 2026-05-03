import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/widgets/modish_widgets.dart';

const _maxBytes = 5 * 1024 * 1024;

class UploadScreen extends StatefulWidget {
  const UploadScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<UploadScreen> createState() => _UploadScreenState();
}

class _UploadScreenState extends State<UploadScreen> {
  final picker = ImagePicker();
  String? _localError;

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_onCtrl);
  }

  @override
  void dispose() {
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
    widget.controller.setPhotoPath(path);
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
            const Center(child: Brand()),
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
                  if (path != null && !kIsWeb)
                    ClipRRect(
                      borderRadius: BorderRadius.circular(16),
                      child: Image.file(
                        File(path),
                        height: 220,
                        fit: BoxFit.cover,
                      ),
                    )
                  else if (path != null && kIsWeb)
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
                  if (path != null) ...[
                    const SizedBox(height: 10),
                    TextButton(
                      onPressed: c.isLoading
                          ? null
                          : () {
                              c.setPhotoPath(null);
                              setState(() => _localError = null);
                            },
                      child: const Text('Удалить фото'),
                    ),
                  ],
                ],
              ),
            ),
            const SizedBox(height: 14),
            PrimaryButton(
              label: 'Начать анализ',
              icon: Icons.auto_awesome,
              onPressed: path != null && !c.isLoading ? c.analyze : null,
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
