import 'dart:convert';

import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/widgets/modish_widgets.dart';

class VisualAnalysisScreen extends StatelessWidget {
  const VisualAnalysisScreen({super.key, required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final url = controller.visualImageUrl;
    Uint8List? bytes;
    if (url != null && url.startsWith('data:image')) {
      final idx = url.indexOf('base64,');
      if (idx != -1) {
        final b64 = url.substring(idx + 'base64,'.length);
        try {
          bytes = base64Decode(b64);
        } catch (_) {
          bytes = null;
        }
      }
    }

    return Scaffold(
      backgroundColor: AppColors.bg,
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 20),
          children: [
            Row(
              children: [
                IconButton(
                  onPressed: () => Navigator.pop(context),
                  icon: const Icon(Icons.arrow_back),
                ),
                const Spacer(),
                const Brand(size: 40),
                const Spacer(),
                const SizedBox(width: 48),
              ],
            ),
            const SizedBox(height: 8),
            const Text(
              'Visual Analysis',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 28, color: AppColors.ink, fontFamily: 'Georgia'),
            ),
            const SizedBox(height: 14),
            SoftCard(
              child: bytes == null
                  ? const Padding(
                      padding: EdgeInsets.all(16),
                      child: Text(
                        'Картинка не готова. Попробуйте повторить позже.',
                        style: TextStyle(color: AppColors.muted),
                      ),
                    )
                  : ClipRRect(
                      borderRadius: BorderRadius.circular(18),
                      child: Image.memory(bytes, fit: BoxFit.cover),
                    ),
            ),
          ],
        ),
      ),
    );
  }
}

