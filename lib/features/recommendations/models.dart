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

Color? _colorFromName(String raw) {
  final key = raw.toLowerCase().trim();
  return _namedColors[key];
}

List<String> _strings(Object? value) => value is List
    ? value.whereType<String>().where((item) => item.trim().isNotEmpty).toList()
    : const [];

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
    final content = json['content_json'] is Map
        ? json['content_json'] as Map
        : const {};
    final items = <String, String>{};
    final suppliedItems = content['items'];
    if (suppliedItems is Map) {
      for (final entry in suppliedItems.entries) {
        if (entry.key is String &&
            entry.value is String &&
            (entry.value as String).trim().isNotEmpty) {
          items[_labelForKey(entry.key as String)] = entry.value as String;
        }
      }
    }
    final tags = json['tags_json'] is Map ? json['tags_json'] as Map : const {};
    final rawOccasions = _strings(tags['occasion']);
    return Outfit(
      id: json['id']?.toString() ?? '',
      title: json['title'] is String ? json['title'] as String : 'Образ',
      description: json['description'] is String
          ? json['description'] as String
          : '',
      occasion: _occasionRuFromTags(rawOccasions),
      occasionTags: rawOccasions
          .map((e) => e.toString().toLowerCase())
          .toList(),
      items: items,
      colors: _strings(
        tags['colors'],
      ).map(_colorFromName).whereType<Color>().toList(),
      why: _strings(content['why_it_fits']),
      alternatives: _strings(content['alternatives']),
      icons: [
        for (final label in items.keys)
          switch (label) {
            'Низ' => Icons.view_week_outlined,
            'Слой' => Icons.dry_cleaning_outlined,
            'Обувь' => Icons.directions_walk_outlined,
            _ => Icons.checkroom_outlined,
          },
      ],
    );
  }

  static String _occasionRuFromTags(List<String> occasions) {
    if (occasions.isEmpty) return 'Не указано';
    switch (occasions.first.toLowerCase()) {
      case 'office':
        return 'Офис';
      case 'evening':
        return 'Вечер';
      case 'daily':
        return 'Каждый день';
      default:
        return occasions.first;
    }
  }

  static String _labelForKey(String key) {
    return switch (key) {
      'top' => 'Верх',
      'bottom' => 'Низ',
      'layer' => 'Слой',
      'one_piece' => 'Платье / комплект',
      'shoes' => 'Обувь',
      _ => key,
    };
  }
}
