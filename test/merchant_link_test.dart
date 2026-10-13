import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/core/network/merchant_link.dart';

import 'app_regressions_test.dart' show FakeApi;

class MerchantApi extends FakeApi {
  String url = 'https://merchant.example/item';
  bool eventFails = false;
  final confirmations = <Map<String, dynamic>>[];

  @override
  Future<Map<String, dynamic>> affiliateClick(String productId) async => {
    'url': url,
    'click_id': 'click-1',
  };

  @override
  Future<Map<String, dynamic>> recordRecommendationEvent({
    required String eventType,
    String? productId,
    String? outfitId,
    Map<String, dynamic>? meta,
  }) async {
    confirmations.add({'event': eventType, 'product': productId, 'meta': meta});
    if (eventFails) throw Exception('telemetry unavailable');
    return {};
  }
}

void main() {
  test(
    'Late external launch cannot record old context against a new account',
    () async {
      final api = MerchantApi();
      final launchStarted = Completer<void>();
      final launch = Completer<bool>();
      final opened = openMerchantLink(
        api,
        'old-product',
        surface: 'shopping_intent',
        contextMeta: {'intent_id': 'old-task', 'intent_version': 3},
        openUrl: (_) {
          launchStarted.complete();
          return launch.future;
        },
      );
      await launchStarted.future;
      api.clearToken();
      api.applyToken('new-account');
      launch.complete(true);
      expect(await opened, isTrue);
      expect(api.confirmations, isEmpty);
    },
  );

  test(
    'Merchant confirmation preserves task ownership and version context',
    () async {
      final api = MerchantApi();
      await openMerchantLink(
        api,
        'p1',
        surface: 'shopping_intent',
        contextMeta: {'intent_id': 'task-id', 'intent_version': 3},
        openUrl: (_) async => true,
      );
      final meta = api.confirmations.single['meta'] as Map;
      expect(meta['intent_id'], 'task-id');
      expect(meta['intent_version'], 3);
      expect(meta['confirmation'], 'external_application_opened');
    },
  );

  test(
    'URL resolution and a failed browser launch are not affiliate clicks',
    () async {
      final api = MerchantApi();
      final opened = await openMerchantLink(
        api,
        'p1',
        surface: 'product_detail',
        openUrl: (_) async => false,
      );
      expect(opened, isFalse);
      expect(api.confirmations, isEmpty);
      await expectLater(
        openMerchantLink(
          api,
          'p1',
          surface: 'product_detail',
          openUrl: (_) async => throw Exception('browser unavailable'),
        ),
        throwsException,
      );
      expect(api.confirmations, isEmpty);
    },
  );

  test(
    'Missing or unsupported URLs never launch or confirm a transition',
    () async {
      final api = MerchantApi();
      for (final url in [
        '',
        'https://',
        'file:///private',
        'javascript:alert(1)',
      ]) {
        api.url = url;
        expect(
          await openMerchantLink(
            api,
            'p1',
            surface: 'product_detail',
            openUrl: (_) async {
              fail('Invalid URL reached the browser');
            },
          ),
          isFalse,
        );
      }
      expect(api.confirmations, isEmpty);
    },
  );

  test(
    'Accepted launch records one confirmation for either product or outfit',
    () async {
      for (final surface in ['product_detail', 'outfit_detail']) {
        final api = MerchantApi();
        expect(
          await openMerchantLink(
            api,
            'p1',
            surface: surface,
            openUrl: (uri) async {
              expect(uri.toString(), api.url);
              expect(api.confirmations, isEmpty);
              return true;
            },
          ),
          isTrue,
        );
        expect(api.confirmations, [
          {
            'event': 'affiliate_click',
            'product': 'p1',
            'meta': {
              'surface': surface,
              'confirmation': 'external_application_opened',
              'click_id': 'click-1',
            },
          },
        ]);
      }
    },
  );

  test(
    'Analytics failure does not report a successfully opened link as failed',
    () async {
      final api = MerchantApi()..eventFails = true;
      expect(
        await openMerchantLink(
          api,
          'p1',
          surface: 'outfit_detail',
          openUrl: (_) async => true,
        ),
        isTrue,
      );
      expect(api.confirmations, hasLength(1));
    },
  );
}
