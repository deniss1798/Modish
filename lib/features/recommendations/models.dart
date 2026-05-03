import 'package:flutter/material.dart';

const Map<String, Color> _namedColors = {
  'white': Color(0xFFF4F4F4),
  'navy': Color(0xFF1C2A44),
  'gray': Color(0xFFB8BCC4),
  'graphite': Color(0xFF4A5568),
  'black': Color(0xFF111318),
  'burgundy': Color(0xFF722F37),
  'sand': Color(0xFFD4C4B0),
  'camel': Color(0xFFC19A6B),
  'cream': Color(0xFFFFF8EE),
  'indigo': Color(0xFF3F51B5),
  'olive': Color(0xFF5C6B3A),
  'brown': Color(0xFF6D4C41),
};

Color _colorFromName(String raw) {
  final key = raw.toLowerCase().trim();
  return _namedColors[key] ?? const Color(0xFFE5E7EB);
}

class Outfit {
  const Outfit({
    required this.id,
    required this.title,
    required this.description,
    required this.occasion,
    required this.occasionTags,
    required this.items,
    required this.colors,
    required this.why,
    required this.alternatives,
    required this.icons,
  });

  final String id;
  final String title;
  final String description;
  final String occasion;
  final List<String> occasionTags;
  final Map<String, String> items;
  final List<Color> colors;
  final List<String> why;
  final List<String> alternatives;
  final List<IconData> icons;

  factory Outfit.fromApi(Map<String, dynamic> json) {
    final content = (json['content_json'] as Map?) ?? const {};
    final items = ((content['items'] as Map?) ?? const {}).map(
      (key, value) => MapEntry(_labelForKey('$key'), '$value'),
    );
    final why = List<String>.from(
      (content['why_it_fits'] as List?) ?? const ['подходит под ваш профиль'],
    );
    final alternatives = List<String>.from(
      (content['alternatives'] as List?) ??
          const ['можно адаптировать из базового гардероба'],
    );
    final tags = (json['tags_json'] as Map?) ?? const {};
    final rawOccasions = List<String>.from((tags['occasion'] as List?) ?? const []);
    final colorNames = List<String>.from(
      (tags['colors'] as List?)?.map((e) => e.toString()).toList() ?? const [],
    );
    final palette = colorNames.isNotEmpty
        ? colorNames.map(_colorFromName).toList()
        : const [
            Color(0xFF142238),
            Colors.white,
            Color(0xFFCFCBC5),
          ];
    return Outfit(
      id: json['id'].toString(),
      title: (json['title'] ?? 'Образ').toString(),
      description: (json['description'] ?? 'Минималистичный образ.').toString(),
      occasion: _occasionRuFromTags(rawOccasions),
      occasionTags: rawOccasions.map((e) => e.toString().toLowerCase()).toList(),
      items: items.isEmpty
          ? const {
              'Верх': 'базовый верх',
              'Низ': 'прямой низ',
              'Слой': 'легкий слой',
              'Обувь': 'удобная обувь',
            }
          : items,
      colors: palette,
      why: why,
      alternatives: alternatives,
      icons: const [
        Icons.checkroom_outlined,
        Icons.view_week_outlined,
        Icons.dry_cleaning_outlined,
        Icons.directions_walk_outlined,
      ],
    );
  }

  static String _occasionRuFromTags(List<String> occasions) {
    if (occasions.isEmpty) return 'Каждый день';
    switch (occasions.first.toLowerCase()) {
      case 'office':
        return 'Офис';
      case 'evening':
        return 'Вечер';
      case 'daily':
        return 'Каждый день';
      default:
        return 'Каждый день';
    }
  }

  static String _labelForKey(String key) {
    return switch (key) {
      'top' => 'Верх',
      'bottom' => 'Низ',
      'layer' => 'Слой',
      'shoes' => 'Обувь',
      _ => key,
    };
  }
}
