import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/presentation/price_format.dart';
import 'package:modish/features/outfits/outfit_detail_screen.dart';
import 'package:modish/features/outfits/outfits_screen.dart';
import 'package:modish/features/products/models.dart';
import 'package:modish/features/products/product_detail_screen.dart';

import 'app_regressions_test.dart' show FakeApi;

Map<String, dynamic> _product(num price, {num? oldPrice}) => {
  'id': 'price-product',
  'title': 'Брюки',
  'brand': 'Brand',
  'category': 'брюки',
  'price': price,
  'old_price': ?oldPrice,
  'currency': 'RUB',
  'image_url': '',
};

class _PriceApi extends FakeApi {
  _PriceApi(this.latest);

  final Map<String, dynamic> latest;

  @override
  Future<Map<String, dynamic>> productById(String id) async => latest;
}

void main() {
  test('Product preserves numeric rubles and kopecks without rescaling', () {
    final json =
        jsonDecode('{"id":"decimal","price":1229.50,"old_price":1299.99}')
            as Map<String, dynamic>;
    final product = Product.fromApi(json);
    expect(product.price, 1229.50);
    expect(product.oldPrice, 1299.99);
    expect(product.recommendationFacts['price'], 1229.50);
    expect(product.recommendationFacts['old_price'], 1299.99);
  });

  test('Integer JSON prices and absent old prices remain compatible', () {
    final product = Product.fromApi(_product(1229));
    expect(product.price, 1229);
    expect(product.oldPrice, isNull);
    expect(formatPriceAmount(product.price), '1229');
    expect(formatPriceAmount(Product.fromApi(_product(1229.0)).price), '1229');
    final sale = Product.fromApi(_product(1229.0, oldPrice: 1299.0));
    expect(formatPriceAmount(sale.oldPrice!), '1299');
  });

  test('Price labels retain two kopeck digits and no whole-ruble decimal', () {
    for (final entry in <num, String>{
      0: '0',
      1229: '1229',
      1229.50: '1229,50',
      1229.05: '1229,05',
      0.01: '0,01',
      999999999999.99: '999999999999,99',
    }.entries) {
      expect(formatPriceAmount(entry.key), entry.value);
    }
    expect(formatPriceAmount(1229.0), '1229');
  });

  testWidgets('Product detail shows a sale differing by less than one ruble', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(360, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final payload = _product(1229.50, oldPrice: 1229.99);
    final api = _PriceApi(payload);
    final controller = AppController(apiClient: api);
    addTearDown(controller.dispose);
    addTearDown(api.dispose);
    await tester.pumpWidget(
      MaterialApp(
        builder: (context, child) => MediaQuery(
          data: MediaQuery.of(
            context,
          ).copyWith(textScaler: const TextScaler.linear(1.3)),
          child: child!,
        ),
        home: ProductDetailScreen(
          controller: controller,
          card: FeedCard.fromApi({'product': payload}),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('1229,50 RUB'), findsOneWidget);
    final oldPrice = find.text('1229,99 RUB');
    expect(oldPrice, findsOneWidget);
    expect(
      tester.widget<Text>(oldPrice).style?.decoration,
      TextDecoration.lineThrough,
    );
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });

  for (final total in <num>[3688.48, 3000, 3000.0]) {
    testWidgets('Outfit card and detail format server RUB total $total', (
      tester,
    ) async {
      final api = FakeApi();
      final controller = AppController(apiClient: api);
      addTearDown(controller.dispose);
      addTearDown(api.dispose);
      final outfit = <String, dynamic>{
        'id': 'price-outfit',
        'style_direction': 'daily',
        'total_price': total,
        'products': {
          'top': _product(1229.50),
          'bottom': {..._product(2458.98), 'id': 'second-product'},
        },
      };
      final label = total == 3688.48 ? '3688,48' : '3000';
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: OutfitCard(outfit: outfit, controller: controller),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('2 вещи · $label ₽'), findsOneWidget);
      await tester.tap(find.byType(OutfitCard));
      await tester.pumpAndSettle();
      expect(find.byType(OutfitDetailScreen), findsOneWidget);
      expect(find.text('Итого: $label ₽'), findsOneWidget);
      expect(find.text('Brand · 1229,50 ₽'), findsOneWidget);
      expect(find.text('Brand · 2458,98 ₽'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
    });
  }
}
