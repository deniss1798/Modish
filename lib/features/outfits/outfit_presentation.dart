import 'package:flutter/material.dart';

String outfitScenarioLabel(String key) => switch (key.toLowerCase()) {
  'daily' || 'casual' || 'minimal' => 'Каждый день',
  'office' => 'В офис',
  'evening' => 'Вечер',
  'date' => 'Свидание',
  'restaurant' => 'Ресторан',
  'wedding' => 'Свадьба',
  'party' => 'Вечеринка',
  'gym' => 'Спортзал',
  'run' => 'Пробежка',
  'vacation' => 'Отпуск',
  _ => 'Персональный образ',
};

List<(String, IconData, String)> outfitSlots(Map<String, dynamic> products) => [
  if (products['one_piece'] is Map)
    ('one_piece', Icons.checkroom_outlined, 'Платье / комплект')
  else ...[
    ('top', Icons.checkroom_outlined, 'Верх'),
    ('bottom', Icons.view_week_outlined, 'Низ'),
  ],
  ('shoes', Icons.ice_skating_outlined, 'Обувь'),
];
