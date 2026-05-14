import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/widgets/modish_widgets.dart';
import 'models.dart';

class DetailScreen extends StatefulWidget {
  const DetailScreen({
    super.key,
    required this.outfit,
    required this.controller,
  });
  final Outfit outfit;
  final AppController controller;

  @override
  State<DetailScreen> createState() => _DetailScreenState();
}

class _DetailScreenState extends State<DetailScreen> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      widget.controller.recordViewDetails(widget.outfit.id);
    });
  }

  @override
  Widget build(BuildContext context) {
    final outfit = widget.outfit;
    final controller = widget.controller;
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
                const Brand(width: 160),
                const Spacer(),
                const SizedBox(width: 48),
              ],
            ),
            SoftCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    outfit.title,
                    style: const TextStyle(
                      fontFamily: 'Georgia',
                      fontSize: 34,
                      color: AppColors.ink,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'Ситуации: ${outfit.occasion}',
                    style: const TextStyle(color: AppColors.muted),
                  ),
                  const SizedBox(height: 12),
                  Wrap(
                    spacing: 8,
                    children: outfit.colors
                        .map((c) => CircleAvatar(backgroundColor: c, radius: 11))
                        .toList(),
                  ),
                  const SizedBox(height: 12),
                  ...outfit.items.entries.map(
                    (e) => ListTile(
                      contentPadding: EdgeInsets.zero,
                      title: Text('${e.key}: ${e.value}'),
                    ),
                  ),
                  const Divider(),
                  const Text(
                    'Почему подходит',
                    style: TextStyle(fontSize: 22, color: AppColors.accent),
                  ),
                  const SizedBox(height: 8),
                  ...outfit.why.map(
                    (item) => Padding(
                      padding: const EdgeInsets.only(bottom: 6),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Icon(
                            Icons.check_circle_outline,
                            color: AppColors.accent,
                            size: 22,
                          ),
                          const SizedBox(width: 8),
                          Expanded(child: Text(item)),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: 10),
                  const Text(
                    'Чем заменить',
                    style: TextStyle(
                      fontSize: 22,
                      color: AppColors.ink,
                      fontFamily: 'Georgia',
                    ),
                  ),
                  ...outfit.alternatives.map(
                    (item) => Padding(
                      padding: const EdgeInsets.only(top: 6),
                      child: Text('• $item'),
                    ),
                  ),
                  const SizedBox(height: 16),
                  PrimaryButton(
                    label: 'Сохранить образ',
                    icon: Icons.bookmark_border,
                    onPressed: controller.isLoading
                        ? null
                        : () async {
                            await controller.sendFeedback(outfit.id, 'save');
                            if (context.mounted) Navigator.pop(context);
                          },
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
