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

bool isClothingBaseOutfit(Map<String, dynamic> outfit) =>
    outfit['completeness'] == 'clothing_base';

const clothingBaseMessage =
    'Обувь не подобрана. Показана основа образа из одежды.';

List<(String, IconData, String)> outfitSlots(
  Map<String, dynamic> products, {
  bool includeMissingShoes = true,
}) => [
  if (products['one_piece'] is Map)
    ('one_piece', Icons.checkroom_outlined, 'Платье / комплект')
  else ...[
    ('top', Icons.checkroom_outlined, 'Верх'),
    ('bottom', Icons.view_week_outlined, 'Низ'),
  ],
  if (includeMissingShoes || products['shoes'] is Map)
    ('shoes', Icons.ice_skating_outlined, 'Обувь'),
];
