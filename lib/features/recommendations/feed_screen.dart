import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/widgets/modish_widgets.dart';
import 'models.dart';

class FeedScreen extends StatelessWidget {
  const FeedScreen({super.key, required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final outfit = controller.currentOutfit;
    return SafeArea(
      child: Column(
        children: [
          const SizedBox(height: 22),
          const Brand(),
          const SizedBox(height: 10),
          const Text(
            'Подборка',
            style: TextStyle(fontSize: 26, color: AppColors.ink),
          ),
          const SizedBox(height: 6),
          const Text(
            'Свайп влево — не нравится · вправо — нравится',
            style: TextStyle(fontSize: 12, color: AppColors.muted),
          ),
          const SizedBox(height: 10),
          Expanded(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 18),
              child: outfit == null
                  ? _EmptyFeed(controller: controller)
                  : _SwipeCard(controller: controller, outfit: outfit),
            ),
          ),
          if (outfit != null)
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 8, 20, 18),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  IconButton.filledTonal(
                    onPressed: () =>
                        controller.sendFeedback(outfit.id, 'dislike'),
                    icon: const Icon(Icons.close),
                  ),
                  IconButton.filledTonal(
                    onPressed: () => controller.sendFeedback(outfit.id, 'save'),
                    icon: const Icon(Icons.bookmark_border),
                  ),
                  IconButton.filledTonal(
                    onPressed: () => openDetails(context, outfit, controller),
                    icon: const Icon(Icons.info_outline),
                  ),
                  IconButton.filledTonal(
                    onPressed: () => controller.sendFeedback(outfit.id, 'like'),
                    icon: const Icon(Icons.favorite_border),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

class _EmptyFeed extends StatelessWidget {
  const _EmptyFeed({required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: SoftCard(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text(
              'Карточек пока нет',
              style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 8),
            const Text(
              'Сгенерируйте новую пачку (до 10 образов за раз). Лимит Plus — 5 пачек в месяц.',
              textAlign: TextAlign.center,
              style: TextStyle(color: AppColors.muted, fontSize: 14),
            ),
            const SizedBox(height: 18),
            FilledButton.icon(
              onPressed: controller.isLoading
                  ? null
                  : () => controller.generateOutfits(scenario: 'daily'),
              icon: const Icon(Icons.auto_awesome),
              label: const Text('Сгенерировать пачку'),
            ),
            if (controller.error != null) ...[
              const SizedBox(height: 12),
              Text(
                controller.error!,
                textAlign: TextAlign.center,
                style: const TextStyle(color: AppColors.accent, fontSize: 13),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _SwipeCard extends StatelessWidget {
  const _SwipeCard({required this.controller, required this.outfit});
  final AppController controller;
  final Outfit outfit;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onHorizontalDragEnd: (details) {
        final v = details.primaryVelocity ?? 0;
        if (v > 280) {
          controller.sendFeedback(outfit.id, 'like');
        } else if (v < -280) {
          controller.sendFeedback(outfit.id, 'dislike');
        }
      },
      child: _CardView(outfit: outfit),
    );
  }
}

class _CardView extends StatelessWidget {
  const _CardView({required this.outfit});
  final Outfit outfit;

  @override
  Widget build(BuildContext context) {
    return SoftCard(
      child: ListView(
        children: [
          Text(
            outfit.title,
            style: const TextStyle(
              fontFamily: 'Georgia',
              fontSize: 34,
              color: AppColors.ink,
            ),
          ),
          const SizedBox(height: 6),
          Text(
            outfit.description,
            style: const TextStyle(color: AppColors.muted),
          ),
          const SizedBox(height: 6),
          Text(
            'Ситуация: ${outfit.occasion}',
            style: const TextStyle(color: AppColors.muted, fontSize: 13),
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            children: outfit.colors
                .map((e) => CircleAvatar(backgroundColor: e, radius: 12))
                .toList(),
          ),
          const SizedBox(height: 16),
          ...outfit.items.entries.map(
            (entry) => Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Row(
                children: [
                  SizedBox(
                    width: 60,
                    child: Text(
                      '${entry.key}:',
                      style: const TextStyle(color: AppColors.muted),
                    ),
                  ),
                  Expanded(
                    child: Text(
                      entry.value,
                      style: const TextStyle(fontWeight: FontWeight.w600),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
