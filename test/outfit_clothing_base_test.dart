import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/features/outfits/outfit_detail_screen.dart';
import 'package:modish/features/outfits/outfit_presentation.dart';
import 'package:modish/features/outfits/outfits_screen.dart';

import 'app_regressions_test.dart' show FakeApi;

Map<String, dynamic> clothingBase() => {
  'id': 'verified-clothing-base',
  'style_direction': 'daily',
  'completeness': 'clothing_base',
  'missing_slots': ['shoes'],
  'reason': 'Каждый день: основа образа. $clothingBaseMessage',
  'total_price': 4200,
  'items': {'top': 'blouse', 'bottom': 'skirt'},
  'products': {
    'top': {'id': 'blouse', 'title': 'Блузка', 'price': 2000},
    'bottom': {'id': 'skirt', 'title': 'Юбка', 'price': 2200},
  },
};

void main() {
  test(
    'Clothing base slots contain actual garments and no phantom footwear',
    () {
      final outfit = clothingBase();
      expect(isClothingBaseOutfit(outfit), isTrue);
      final slots = outfitSlots(
        Map<String, dynamic>.from(outfit['products'] as Map),
        includeMissingShoes: false,
      );
      expect(slots.map((s) => s.$1), ['top', 'bottom']);
    },
  );

  for (final size in [const Size(360, 780), const Size(800, 400)]) {
    testWidgets('Base is labelled once and remains readable at $size / 200%', (
      tester,
    ) async {
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final api = FakeApi();
      final controller = AppController(apiClient: api);
      addTearDown(controller.dispose);
      addTearDown(api.dispose);
      Widget app(Widget child) => MaterialApp(
        builder: (context, body) => MediaQuery(
          data: MediaQuery.of(
            context,
          ).copyWith(textScaler: const TextScaler.linear(2)),
          child: body!,
        ),
        home: child,
      );
      await tester.pumpWidget(
        app(
          Scaffold(
            body: SingleChildScrollView(
              child: OutfitCard(outfit: clothingBase(), controller: controller),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text(clothingBaseMessage), findsOneWidget);
      expect(find.text('Обувь'), findsNothing);
      expect(tester.takeException(), isNull);

      await tester.pumpWidget(
        app(OutfitDetailScreen(outfit: clothingBase(), controller: controller)),
      );
      await tester.pumpAndSettle();
      expect(find.text(clothingBaseMessage), findsOneWidget);
      expect(find.textContaining('engine:'), findsNothing);
      expect(tester.takeException(), isNull);
    });
  }
}
