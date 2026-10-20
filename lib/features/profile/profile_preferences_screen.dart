import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/presentation/catalog_labels.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';

/// Read-only, sourced preferences. Analysis summaries are not evidence of taste.
class ProfilePreferencesData {
  const ProfilePreferencesData({
    required this.chosenStyles,
    required this.styles,
    required this.colors,
    required this.scenarios,
  });
  final List<String> chosenStyles;
  final List<String> styles;
  final List<String> colors;
  final List<String> scenarios;
  bool get hasLearned => styles.isNotEmpty || colors.isNotEmpty;
  bool get hasAny =>
      hasLearned || chosenStyles.isNotEmpty || scenarios.isNotEmpty;

  factory ProfilePreferencesData.fromController(AppController controller) {
    final weights = controller.tasteProfile['weights'];
    return ProfilePreferencesData(
      chosenStyles: _displayValues(
        controller.activationStatus['style_preferences'],
        _styleLabels,
      ),
      styles: _displayValues(
        _positiveWeights(weights is Map ? weights['styles'] : null),
        _styleLabels,
      ),
      colors: _displayValues(
        _positiveWeights(weights is Map ? weights['colors'] : null),
        catalogColorLabels,
      ),
      scenarios: _displayValues(
        controller.fitProfile['style_scenarios'],
        _styleLabels,
      ),
    );
  }
}

class ProfilePreferencesScreen extends StatefulWidget {
  const ProfilePreferencesScreen({super.key, required this.controller});
  final AppController controller;
  @override
  State<ProfilePreferencesScreen> createState() =>
      _ProfilePreferencesScreenState();
}

class _ProfilePreferencesScreenState extends State<ProfilePreferencesScreen> {
  late final AppController _owner = widget.controller;
  late final int _session = _owner.navigationSessionEpoch;

  @override
  Widget build(BuildContext context) => Scaffold(
    backgroundColor: AppColors.bg,
    appBar: AppBar(
      title: const Text('Ваши предпочтения', style: AppTextStyles.sectionTitle),
    ),
    body: SafeArea(
      top: false,
      child: ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) {
          final changed =
              widget.controller != _owner ||
              _session != _owner.navigationSessionEpoch;
          final preferences = ProfilePreferencesData.fromController(
            widget.controller,
          );
          return Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 640),
              child: ListView(
                padding: const EdgeInsets.all(20),
                children: changed
                    ? const [
                        Text(
                          'Аккаунт изменился. Вернитесь в профиль и откройте предпочтения заново.',
                          style: AppTextStyles.bodyMuted,
                        ),
                      ]
                    : [
                        const Text(
                          'Ваш выбор и оценки вещей помогают уточнять подборку.',
                          style: AppTextStyles.bodyMuted,
                        ),
                        const SizedBox(height: 20),
                        if (!preferences.hasAny)
                          const SoftCard(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Icon(
                                  Icons.favorite_border,
                                  color: AppColors.accent,
                                ),
                                SizedBox(height: 14),
                                Text(
                                  'Пока мало данных',
                                  style: AppTextStyles.sectionTitle,
                                ),
                                SizedBox(height: 8),
                                Text(
                                  'Оценивайте вещи — здесь появятся ваши предпочтения.',
                                  style: AppTextStyles.bodyMuted,
                                ),
                              ],
                            ),
                          ),
                        if (preferences.chosenStyles.isNotEmpty)
                          _PreferenceSection(
                            title: 'Вы выбрали при знакомстве',
                            values: preferences.chosenStyles,
                          ),
                        if (preferences.styles.isNotEmpty)
                          _PreferenceSection(
                            title: 'Стили по вашим оценкам',
                            values: preferences.styles,
                          ),
                        if (preferences.colors.isNotEmpty)
                          _PreferenceSection(
                            title: 'Цвета по вашим оценкам',
                            values: preferences.colors,
                          ),
                        if (preferences.scenarios.isNotEmpty)
                          _PreferenceSection(
                            title: 'В параметрах подборки',
                            values: preferences.scenarios,
                          ),
                      ],
              ),
            ),
          );
        },
      ),
    ),
  );
}

class _PreferenceSection extends StatelessWidget {
  const _PreferenceSection({required this.title, required this.values});
  final String title;
  final List<String> values;
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(bottom: 12),
    child: SoftCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: AppTextStyles.caption),
          const SizedBox(height: 12),
          for (var index = 0; index < values.length; index++)
            Padding(
              padding: EdgeInsets.only(top: index == 0 ? 0 : 10),
              child: Text(values[index], style: AppTextStyles.body),
            ),
        ],
      ),
    ),
  );
}

const _styleLabels = {
  'daily': 'Каждый день',
  'office': 'В офис',
  'evening': 'Вечер',
  'casual': 'Повседневный',
  'smart_casual': 'Smart casual',
  'minimal': 'Минимализм',
  'minimalism': 'Минимализм',
  'romantic': 'Романтичный',
  'elegant': 'Элегантный',
  'streetwear': 'Городской',
  'classic': 'Классический',
  'sport': 'Спортивный',
  'sporty': 'Спортивный',
  'boho': 'Бохо',
};

List<String> _displayValues(dynamic raw, Map<String, String> labels) =>
    (raw is List ? raw : const [])
        .map((value) {
          final text = '$value'.trim();
          return labels[text.toLowerCase()] ??
              (labels.containsValue(text) ? text : '');
        })
        .where((value) => value.isNotEmpty)
        .toSet()
        .take(5)
        .toList();

List<String> _positiveWeights(dynamic raw) {
  if (raw is! Map) return const [];
  final entries =
      raw.entries.where((entry) {
        final weight = num.tryParse('${entry.value}');
        return weight != null && weight.isFinite && weight > 0;
      }).toList()..sort(
        (a, b) => num.parse('${b.value}').compareTo(num.parse('${a.value}')),
      );
  return entries.map((entry) => '${entry.key}').toList();
}
