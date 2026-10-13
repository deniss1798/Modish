import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/storage/feed_history.dart';
import 'package:modish/features/products/models.dart' as prod;
import 'app_regressions_test.dart' show FakeApi, product;

class MemoryHistory extends FeedHistory {
  final accounts = <String, List<Map<String, dynamic>>>{};
  @override
  Future<List<Map<String, dynamic>>> read(String account) async =>
      accounts[account] ?? [];
  @override
  Future<void> write(String account, List<Map<String, dynamic>> rows) async {
    accounts[account] = rows;
  }
}

void main() {
  test(
    'Merchant identity ignores tracking/size but preserves product query and colour',
    () {
      prod.Product item(String id, String url) =>
          prod.Product.fromApi({...product(id), 'original_url': url});
      final first = item(
        'a',
        'https://www.shop.test/p/coat/?size=M&utm_source=feed&color=black',
      );
      final variant = item('b', 'https://shop.test/p/coat?color=black&size=L');
      final other = item('c', 'https://shop.test/p/coat?color=red');
      expect(first.identityKeys.intersection(variant.identityKeys), isNotEmpty);
      expect(first.identityKeys.intersection(other.identityKeys), isEmpty);
      expect(
        item('d', 'https://shop.test/product?id=1').identityKeys.intersection(
          item('e', 'https://shop.test/product?id=2').identityKeys,
        ),
        isEmpty,
      );
    },
  );

  testWidgets(
    'Dismissed models stay hidden after restart and are scoped to the account',
    (tester) async {
      final history = MemoryHistory();
      final api = FakeApi()
        ..rows = List.generate(
          12,
          (i) => {
            'product': {
              ...product('p$i'),
              'original_url': 'https://shop.test/item/$i?size=M',
            },
          },
        );
      final first = AppController(apiClient: api, feedHistory: history)
        ..email = 'one@example.test';
      late BuildContext ctx;
      await tester.pumpWidget(
        MaterialApp(
          home: Builder(
            builder: (context) {
              ctx = context;
              return const SizedBox();
            },
          ),
        ),
      );
      await first.refreshFeedFromUser();
      await first.sendProductEvent(ctx, 'p0', 'like');
      await tester.pump();
      first.dispose();
      api.rows = [
        {
          'product': {
            ...product('new-size'),
            'original_url': 'https://shop.test/item/0?size=L&utm_source=x',
          },
        },
        {'product': product('fresh')},
      ];
      final restart = AppController(apiClient: api, feedHistory: history)
        ..email = 'one@example.test';
      await restart.refreshFeedFromUser();
      expect(restart.productFeed.map((c) => c.product.id), ['fresh']);
      expect(api.lastFilters['excludeIds'], contains('p0'));
      final other = AppController(apiClient: api, feedHistory: history)
        ..email = 'two@example.test';
      await other.refreshFeedFromUser();
      expect(other.productFeed.map((c) => c.product.id), ['new-size', 'fresh']);
      restart.dispose();
      other.dispose();
    },
  );
}
