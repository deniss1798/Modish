import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/theme/app_theme.dart';
import 'package:modish/core/widgets/related_product_tile.dart';
import 'package:modish/features/products/models.dart';
import 'package:modish/features/products/product_detail_screen.dart';
import 'package:modish/features/recommendations/feed_screen.dart';

import 'catalog_interactions_test.dart' show CatalogueApi, catalogueProduct;

Widget scaledApp(Widget child, {double scale = 2}) => MaterialApp(
  theme: AppTheme.dark,
  builder: (context, body) => MediaQuery(
    data: MediaQuery.of(context).copyWith(textScaler: TextScaler.linear(scale)),
    child: body!,
  ),
  home: child,
);

void setViewport(WidgetTester tester, Size size) {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
}

void main() {
  for (final size in [const Size(360, 780), const Size(800, 400)]) {
    testWidgets('Complement title, price and tap fit at $size / 200%', (
      tester,
    ) async {
      setViewport(tester, size);
      final semantics = tester.ensureSemantics();
      const title = 'Юбка мини с драпировкой и декоративной пряжкой';
      var opened = 0;
      await tester.pumpWidget(
        scaledApp(
          Scaffold(
            body: Center(
              child: Builder(
                builder: (context) => SizedBox(
                  height: RelatedProductTile.heightFor(context),
                  child: ListView(
                    scrollDirection: Axis.horizontal,
                    children: [
                      RelatedProductTile(
                        title: title,
                        imageUrl: '',
                        price: 2599.50,
                        currency: 'RUB',
                        onTap: () => opened++,
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(tester.widget<Text>(find.text(title)).maxLines, 2);
      expect(find.text('2599,50 ₽'), findsOneWidget);
      expect(
        find.bySemanticsLabel('Открыть $title, 2599,50 ₽'),
        findsOneWidget,
      );
      await tester.tap(find.byType(RelatedProductTile));
      await tester.pump();
      expect(opened, 1);
      semantics.dispose();
      expect(tester.takeException(), isNull);
    });

    testWidgets('Detail complement opens the named garment at $size / 200%', (
      tester,
    ) async {
      setViewport(tester, size);
      final api = CatalogueApi();
      final controller = AppController(apiClient: api);
      addTearDown(controller.dispose);
      addTearDown(api.dispose);
      await tester.pumpWidget(
        scaledApp(
          ProductDetailScreen(
            controller: controller,
            card: FeedCard(
              product: Product.fromApi(catalogueProduct('anchor')),
              reason: '',
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      final related = find.byKey(const ValueKey('related-product-related'));
      await tester.scrollUntilVisible(
        related,
        250,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.pumpAndSettle();
      expect(
        find.descendant(
          of: related,
          matching: find.text('Брюки второго товара'),
        ),
        findsOneWidget,
      );
      expect(
        find.descendant(of: related, matching: find.text('5000 ₽')),
        findsOneWidget,
      );
      await tester.tap(related);
      await tester.pumpAndSettle();
      expect(api.details, ['anchor', 'related']);
      expect(api.events.single['reference_product_id'], 'anchor');
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
    });
  }

  for (final (size, scale, hasRail) in [
    (const Size(360, 780), 1.0, true),
    (const Size(360, 900), 2.0, true),
    (const Size(360, 640), 2.0, false),
    (const Size(800, 400), 2.0, false),
  ]) {
    testWidgets('Feed reserves room for complements at $size / $scale', (
      tester,
    ) async {
      setViewport(tester, size);
      final api = CatalogueApi()
        ..rows = [
          {'product': catalogueProduct('anchor')},
        ];
      final controller = AppController(apiClient: api);
      addTearDown(controller.dispose);
      addTearDown(api.dispose);
      await controller.refreshFeedFromUser();
      await tester.pumpWidget(
        scaledApp(
          Scaffold(body: FeedScreen(controller: controller)),
          scale: scale,
        ),
      );
      await tester.pumpAndSettle();
      expect(
        find.byType(RelatedProductTile),
        hasRail ? findsOneWidget : findsNothing,
      );
      if (hasRail) {
        expect(find.text('Брюки второго товара'), findsOneWidget);
        await tester.tap(find.byType(RelatedProductTile));
        await tester.pumpAndSettle();
        expect(api.details, ['related']);
      }
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
    });
  }
}
