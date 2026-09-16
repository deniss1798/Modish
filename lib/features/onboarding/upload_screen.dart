import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';

class UploadScreen extends StatefulWidget {
  const UploadScreen({super.key, required this.controller});
  final AppController controller;
  @override
  State<UploadScreen> createState() => _UploadScreenState();
}

class _UploadScreenState extends State<UploadScreen> {
  final _picker = ImagePicker();
  String? _localError;
  bool _picking = false;
  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_onChange);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onChange);
    super.dispose();
  }

  void _onChange() {
    if (mounted) setState(() {});
  }

  Future<void> _pick(ImageSource source) async {
    setState(() {
      _picking = true;
      _localError = null;
    });
    try {
      final photo = await _picker.pickImage(
        source: source,
        imageQuality: 88,
        maxWidth: 1800,
      );
      if (photo == null || !mounted) return;
      if (await photo.length() > 5 * 1024 * 1024) {
        if (mounted) {
          setState(() => _localError = 'Выберите фото размером до 5 МБ');
        }
        return;
      }
      if (mounted) widget.controller.setPhotoPath(photo.path);
    } catch (_) {
      if (mounted) {
        setState(
          () => _localError =
              'Не удалось открыть фото. Проверьте доступ к камере или галерее.',
        );
      }
    } finally {
      if (mounted) setState(() => _picking = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    final path = c.selectedPhotoPath;
    final busy = c.isLoading || _picking;
    final error = _localError ?? c.error;
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) c.exitUploadFlow();
      },
      child: MobileViewport(
        child: SafeArea(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(20, 12, 20, 28),
            children: [
              Align(
                alignment: Alignment.centerLeft,
                child: IconButton(
                  tooltip: 'Вернуться в профиль',
                  onPressed: c.exitUploadFlow,
                  icon: const Icon(Icons.arrow_back),
                ),
              ),
              const SizedBox(height: 16),
              const Text('Анализ по фото', style: AppTextStyles.screenTitle),
              const SizedBox(height: 10),
              const Text(
                'Добавьте новое фото, чтобы уточнить рекомендации по стилю. Параметры вашего профиля уже учтены.',
                style: AppTextStyles.bodyMuted,
              ),
              const SizedBox(height: 24),
              SoftCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    if (path != null)
                      ClipRRect(
                        borderRadius: BorderRadius.circular(16),
                        child: SizedBox(
                          height: 280,
                          child: kIsWeb
                              ? Image.network(path, fit: BoxFit.contain)
                              : Image.file(File(path), fit: BoxFit.contain),
                        ),
                      )
                    else ...[
                      const SizedBox(height: 24),
                      const Icon(
                        Icons.add_photo_alternate_outlined,
                        color: AppColors.accent,
                        size: 52,
                      ),
                      const SizedBox(height: 16),
                      const Text(
                        'Выберите фото в полный рост',
                        textAlign: TextAlign.center,
                        style: AppTextStyles.sectionTitle,
                      ),
                      const SizedBox(height: 10),
                      const Text(
                        'Хорошее освещение и простой фон помогут рассмотреть ваш образ.',
                        textAlign: TextAlign.center,
                        style: AppTextStyles.bodyMuted,
                      ),
                      const SizedBox(height: 24),
                    ],
                    const SizedBox(height: 16),
                    Row(
                      children: [
                        Expanded(
                          child: OutlinedButton.icon(
                            onPressed: busy
                                ? null
                                : () => _pick(ImageSource.gallery),
                            icon: const Icon(Icons.photo_library_outlined),
                            label: const Text('Галерея'),
                          ),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: OutlinedButton.icon(
                            onPressed: busy
                                ? null
                                : () => _pick(ImageSource.camera),
                            icon: const Icon(Icons.camera_alt_outlined),
                            label: const Text('Камера'),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 12),
                    const Text(
                      'JPG, PNG или WebP · до 5 МБ',
                      textAlign: TextAlign.center,
                      style: AppTextStyles.caption,
                    ),
                  ],
                ),
              ),
              if (error != null)
                Padding(
                  padding: const EdgeInsets.only(top: 16),
                  child: Text(
                    error,
                    style: const TextStyle(color: AppColors.error),
                  ),
                ),
              const SizedBox(height: 24),
              PrimaryButton(
                label: busy ? 'Подождите…' : 'Обновить анализ',
                icon: Icons.auto_awesome_outlined,
                onPressed: busy || path == null ? null : c.analyze,
              ),
              const SizedBox(height: 12),
              TextButton(
                onPressed: c.exitUploadFlow,
                child: const Text('Отмена'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
