import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/widgets/modish_widgets.dart';

class RecommendationsScreen extends StatelessWidget {
  const RecommendationsScreen({super.key, required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final s = controller.summary;
    List<String> listOf(String key) =>
        List<String>.from((s[key] as List?) ?? const []);

    final suitableColors = listOf('suitable_colors');
    final suitableSil = listOf('suitable_silhouettes');
    final recommendedItems = listOf('recommended_items');
    final styleDirs = listOf('style_direction_human');
    final cautionColors = listOf('avoid_colors');
    final cautionSil = listOf('avoid_silhouettes');
    final avoidPatterns = listOf('avoid_patterns_human');
    final reactions = listOf('reactions_insights');
    final conf = (s['confidence_score'] as num?)?.toDouble();

    return SafeArea(
      child: ListView(
        padding: const EdgeInsets.fromLTRB(18, 22, 18, 20),
        children: [
          const Center(child: Brand()),
          const SizedBox(height: 12),
          const Center(
            child: Text(
              'Рекомендации',
              style: TextStyle(fontSize: 28, color: AppColors.ink),
            ),
          ),
          const SizedBox(height: 18),
          SoftCard(
            child: _ListBlock(
              title: 'Вам подходит',
              items: [
                ...suitableColors.map((e) => 'цвет: $e'),
                ...suitableSil.map((e) => 'силуэт: $e'),
                ...recommendedItems.map((e) => 'тип вещи: $e'),
                ...styleDirs,
              ],
            ),
          ),
          const SizedBox(height: 14),
          SoftCard(
            child: _ListBlock(
              title: 'Лучше осторожнее',
              items: [
                ...cautionColors.map((e) => 'цвет: $e'),
                ...cautionSil.map((e) => 'силуэт: $e'),
                ...avoidPatterns,
              ],
            ),
          ),
          if (reactions.isNotEmpty) ...[
            const SizedBox(height: 14),
            SoftCard(
              child: _ListBlock(
                title: 'Судя по вашим реакциям',
                items: reactions,
              ),
            ),
          ],
          const SizedBox(height: 14),
          SoftCard(
            child: _ListBlock(
              title: 'Что добавить в гардероб',
              items: recommendedItems.isEmpty
                  ? const [
                      'белый лонгслив',
                      'тёмные прямые джинсы',
                      'серый жакет',
                      'минималистичные кроссовки',
                    ]
                  : recommendedItems,
            ),
          ),
          if (conf != null) ...[
            const SizedBox(height: 14),
            SoftCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Уверенность профиля',
                    style: TextStyle(
                      fontFamily: 'Georgia',
                      fontSize: 26,
                      color: AppColors.ink,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'Судя по анализу и вашим реакциям, уверенность около '
                    '${(conf * 100).round()}%. Формулировки носят вероятностный характер.',
                    style: const TextStyle(color: AppColors.muted, height: 1.4),
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _ListBlock extends StatelessWidget {
  const _ListBlock({required this.title, required this.items});
  final String title;
  final List<String> items;

  @override
  Widget build(BuildContext context) {
    final show = items.where((e) => e.trim().isNotEmpty).toList();
    if (show.isEmpty) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            title,
            style: const TextStyle(
              fontFamily: 'Georgia',
              fontSize: 28,
              color: AppColors.ink,
            ),
          ),
          const SizedBox(height: 8),
          const Text(
            'Пока мало данных — пройдите анализ и свайпните несколько карточек.',
            style: TextStyle(color: AppColors.muted),
          ),
        ],
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          title,
          style: const TextStyle(
            fontFamily: 'Georgia',
            fontSize: 28,
            color: AppColors.ink,
          ),
        ),
        const SizedBox(height: 10),
        ...show.map(
          (item) => Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Padding(
                  padding: EdgeInsets.only(top: 4),
                  child: Icon(Icons.circle, size: 8, color: AppColors.accent),
                ),
                const SizedBox(width: 8),
                Expanded(child: Text(item)),
              ],
            ),
          ),
        ),
      ],
    );
  }
}
