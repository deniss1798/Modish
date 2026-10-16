import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/features/recommendations/models.dart';

void _expectNoInventedContent(Outfit outfit) {
  expect(outfit.items, isEmpty);
  expect(outfit.icons, isEmpty);
  expect(outfit.why, isEmpty);
  expect(outfit.alternatives, isEmpty);
  expect(outfit.colors, isEmpty);
  expect(outfit.description, isEmpty);
  expect(outfit.occasionTags, isEmpty);
  expect(outfit.occasion, 'Не указано');
}

void main() {
  test(
    'A real dress and shoes retain their supplied names and Russian slots',
    () {
      final outfit = Outfit.fromApi({
        'id': 'real-legacy-look',
        'title': 'На выход',
        'description': 'Комплект из двух товаров каталога.',
        'content_json': {
          'schema_version': 2,
          'outfit_id': 'catalogue-look',
          'product_ids': {'one_piece': 'dress-sku', 'shoes': 'shoes-sku'},
          'items': {'one_piece': 'Платье миди', 'shoes': 'Лоферы'},
          'why_it_fits': ['Размеры вещей указаны в наличии.'],
          'alternatives': <String>[],
        },
        'tags_json': {
          'occasion': ['evening'],
          'colors': ['navy', 'white'],
        },
      });
      expect(outfit.id, 'real-legacy-look');
      expect(outfit.title, 'На выход');
      expect(outfit.description, 'Комплект из двух товаров каталога.');
      expect(outfit.items, {
        'Платье / комплект': 'Платье миди',
        'Обувь': 'Лоферы',
      });
      expect(outfit.items.keys, isNot(contains('Верх')));
      expect(outfit.items.keys, isNot(contains('Низ')));
      expect(outfit.icons, [
        Icons.checkroom_outlined,
        Icons.directions_walk_outlined,
      ]);
      expect(outfit.why, ['Размеры вещей указаны в наличии.']);
      expect(outfit.alternatives, isEmpty);
      expect(outfit.occasion, 'Вечер');
      expect(outfit.occasionTags, ['evening']);
      expect(outfit.colors, [const Color(0xFF1C2A44), const Color(0xFFF4F4F4)]);
    },
  );

  test('Explicit separates, reasons and alternatives are preserved', () {
    final outfit = Outfit.fromApi({
      'id': 'separates-look',
      'title': 'Для офиса',
      'description': 'Описание из ответа.',
      'content_json': {
        'product_ids': {
          'top': 'shirt-sku',
          'bottom': 'trousers-sku',
          'shoes': 'loafer-sku',
        },
        'items': {'top': 'Рубашка', 'bottom': 'Брюки', 'shoes': 'Лоферы'},
        'why_it_fits': ['Подтверждённые размеры.', 'Общая стоимость 6000 ₽.'],
        'alternatives': ['Лоферы другого цвета из этого комплекта.'],
      },
      'tags_json': {
        'occasion': ['OFFICE'],
      },
    });
    expect(outfit.items, {
      'Верх': 'Рубашка',
      'Низ': 'Брюки',
      'Обувь': 'Лоферы',
    });
    expect(outfit.icons, hasLength(3));
    expect(outfit.why, ['Подтверждённые размеры.', 'Общая стоимость 6000 ₽.']);
    expect(outfit.alternatives, ['Лоферы другого цвета из этого комплекта.']);
    expect(outfit.description, 'Описание из ответа.');
    expect(outfit.occasion, 'Офис');
    expect(outfit.occasionTags, ['office']);
    expect(outfit.colors, isEmpty);
  });

  test('A missing or SKU-less empty payload invents no outfit content', () {
    for (final payload in <Map<String, dynamic>>[
      {},
      {'id': 'no-content'},
      {
        'id': 'sku-less',
        'content_json': {'items': <String, String>{}},
        'tags_json': <String, dynamic>{},
      },
      {
        'content_json': {
          'items': null,
          'why_it_fits': null,
          'alternatives': null,
        },
        'tags_json': {'occasion': null, 'colors': null},
      },
    ]) {
      _expectNoInventedContent(Outfit.fromApi(payload));
    }
    expect(Outfit.fromApi({}).id, isEmpty);
  });

  for (final malformed in <Object>['invalid-container', const [], 12, true]) {
    test('Malformed container $malformed is empty instead of fabricated', () {
      _expectNoInventedContent(
        Outfit.fromApi({
          'content_json': malformed,
          'tags_json': malformed,
          'description': {'invalid': malformed},
        }),
      );
    });
  }

  test(
    'Invalid field containers and item values are not stringified as clothes',
    () {
      final outfit = Outfit.fromApi({
        'content_json': {
          'items': {
            'top': null,
            'bottom': 1200,
            'shoes': {'id': 'not-a-name'},
            'layer': '   ',
          },
          'why_it_fits': 'not-a-list',
          'alternatives': {'not': 'a-list'},
        },
        'tags_json': {'occasion': 'not-a-list', 'colors': 123},
      });
      _expectNoInventedContent(outfit);
    },
  );

  test('Mixed malformed fields keep only explicitly supplied text', () {
    final outfit = Outfit.fromApi({
      'content_json': {
        'items': {'top': 'Рубашка SKU', 'bottom': null, 'shoes': 'Лоферы SKU'},
        'why_it_fits': [null, 'Проверенная причина.', 12, ''],
        'alternatives': ['Подтверждённая замена.', <String>[]],
      },
      'tags_json': {
        'occasion': [false, 'wedding'],
        'colors': ['unmapped-color', ' black ', null, 12],
      },
    });
    expect(outfit.items, {'Верх': 'Рубашка SKU', 'Обувь': 'Лоферы SKU'});
    expect(outfit.icons, hasLength(2));
    expect(outfit.why, ['Проверенная причина.']);
    expect(outfit.alternatives, ['Подтверждённая замена.']);
    expect(outfit.occasion, 'wedding');
    expect(outfit.colors, [const Color(0xFF111318)]);
    expect(outfit.description, isEmpty);
  });

  test('Unknown or missing colors never become a default neutral palette', () {
    for (final colors in [
      null,
      <Object>[],
      ['unsupported-color'],
      [null, 123],
    ]) {
      final outfit = Outfit.fromApi({
        'tags_json': {'colors': colors},
      });
      expect(outfit.colors, isEmpty);
      expect(outfit.items, isEmpty);
    }
  });
}
