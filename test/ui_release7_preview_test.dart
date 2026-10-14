import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/features/onboarding/upload_screen.dart';
import 'package:modish/features/recommendations/saved_screen.dart';

import 'app_regressions_test.dart' show FakeApi, product;
import 'ui_release6_preview_test.dart' show renderOfflinePhonePreviews;

class Preview7Api extends FakeApi {
  @override
  Future<List<Map<String, dynamic>>> savedProducts() async => [
    {
      'product': {
        ...product('saved'),
        'title': 'Джемпер кашемировый чёрный',
        'brand': 'MUNKH',
        'price': 17955,
      },
    },
  ];
}

void main() {
  testWidgets(
    'Release 7 phone layouts fit and render',
    (tester) async {
      final api = Preview7Api()
        ..rows = [
          {
            'product': {
              ...product('shirt'),
              'title': 'Рубашка из фактурного хлопка',
              'brand': 'BAON',
              'price': 4599,
              'old_price': 5999,
              'available_sizes': ['M', 'L', 'XL'],
            },
            'reasons': ['Подходит вашему стилю'],
          },
        ];
      final controller = AppController(apiClient: api)
        ..email = 'style@example.test'
        ..savedProductRows = await api.savedProducts();
      await renderOfflinePhonePreviews(
        tester,
        controller: controller,
        output: const String.fromEnvironment(
          'PREVIEW7_OUTPUT',
          defaultValue: '.tmp/alpha02-preview7',
        ),
        screens: {
          'saved': Scaffold(
            body: SafeArea(child: SavedScreen(controller: controller)),
          ),
          'feed': HomeScreen(controller: controller),
          'photo': UploadScreen(controller: controller),
        },
      );
    },
    timeout: const Timeout(Duration(minutes: 2)),
  );
}
