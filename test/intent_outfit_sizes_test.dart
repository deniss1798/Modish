import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/features/products/product_detail_screen.dart';
import 'package:modish/features/shopping/shopping_task_screen.dart';

import 'shopping_task_test.dart' show OutfitTaskApi;

Map<String, dynamic> sizeEvidence(String size, String system) => {
  'raw_size': size,
  'normalized_size': size,
  'size_system': system,
  'availability': 'available',
  'reason': null,
  'resolver_version': 'size_resolver_v2',
};

class ExactOutfitApi extends OutfitTaskApi {
  Map<String, dynamic> matches = {
    'top': sizeEvidence('L', 'LETTER'),
    'bottom': sizeEvidence('50', 'RU'),
    'shoes': sizeEvidence('38', 'EU'),
  };

  @override
  Map<String, dynamic> outfitResult(int version) {
    final result = super.outfitResult(version);
    final outfit = (result['outfits'] as List).single as Map<String, dynamic>;
    outfit['size_matches'] = matches;
    for (final entry in (outfit['products'] as Map).entries) {
      final product = entry.value as Map<String, dynamic>;
      final size = entry.key == 'top'
          ? 'L'
          : entry.key == 'bottom'
          ? '50'
          : '38';
      product['available_sizes'] = [size];
      product['recommendation_facts'] = {
        'version': 1,
        'sizes': [size],
      };
    }
    return result;
  }

  @override
  Future<Map<String, dynamic>> productById(String id) async {
    final outfit = (outfitResult(1)['outfits'] as List).single as Map;
    return Map<String, dynamic>.from(
      (outfit['products'] as Map).values.firstWhere(
            (product) => product['id'] == id,
          )
          as Map,
    );
  }
}

void main() {
  testWidgets(
    'Outfit shows merchant sizes per slot and passes confirmed shoe size to detail',
    (tester) async {
      tester.view.physicalSize = const Size(360, 780);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final controller = AppController(apiClient: ExactOutfitApi());
      await controller.shoppingTask.submit('Образ до 20000');
      await tester.pumpWidget(
        MaterialApp(home: ShoppingTaskScreen(controller: controller)),
      );
      await tester.pumpAndSettle();
      expect(find.text('Размер магазина: L'), findsOneWidget);
      expect(find.text('Размер магазина: 50 (RU)'), findsOneWidget);
      expect(find.text('Размер магазина: 38 (EU)'), findsOneWidget);
      final shoeDetail = find.byKey(
        const ValueKey('detail-outfit-look-1-shoes'),
      );
      await tester.ensureVisible(shoeDetail);
      await tester.pumpAndSettle();
      await tester.tap(shoeDetail);
      await tester.pumpAndSettle();
      final detail = tester.widget<ProductDetailScreen>(
        find.byType(ProductDetailScreen),
      );
      expect(detail.card.reason, 'Размер магазина: 38 (EU)');
      expect(detail.card.reasons, ['Размер магазина: 38 (EU)']);
      expect(
        find.text('Размер магазина: 38 (EU)').hitTestable(),
        findsOneWidget,
      );
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets(
    'Legacy raw sizes without backend matches produce no confirmed size labels',
    (tester) async {
      final api = ExactOutfitApi()..matches = {};
      final controller = AppController(apiClient: api);
      await controller.shoppingTask.submit('Образ до 20000');
      await tester.pumpWidget(
        MaterialApp(home: ShoppingTaskScreen(controller: controller)),
      );
      await tester.pumpAndSettle();
      expect(find.textContaining('Размер магазина:'), findsNothing);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );

  testWidgets(
    'Unknown system, unavailable size and conflicting evidence stay unasserted',
    (tester) async {
      final api = ExactOutfitApi()
        ..matches = {
          'top': {
            ...sizeEvidence('L', 'LETTER'),
            'reason': 'conflicting_size_system',
          },
          'bottom': {...sizeEvidence('50', 'RU'), 'size_system': null},
          'shoes': {...sizeEvidence('38', 'EU'), 'availability': 'unavailable'},
        };
      final controller = AppController(apiClient: api);
      await controller.shoppingTask.submit('Образ до 20000');
      await tester.pumpWidget(
        MaterialApp(home: ShoppingTaskScreen(controller: controller)),
      );
      await tester.pumpAndSettle();
      expect(find.textContaining('Размер магазина:'), findsNothing);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );
}
