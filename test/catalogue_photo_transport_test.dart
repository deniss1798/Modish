import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/core/network/api_client.dart';

import 'api_session_lifecycle_test.dart'
    show DeferredAdapter, waitForRequests, isCancelled;

class UploadRequest {
  UploadRequest(this.path, this.key, this.body, this.contentType);
  final String path;
  final String? key;
  final List<int> body;
  final String? contentType;
  final response = Completer<ResponseBody>();
  void complete(Map<String, dynamic> data, [int status = 200]) =>
      response.complete(
        ResponseBody.fromString(
          jsonEncode(data),
          status,
          headers: {
            Headers.contentTypeHeader: [Headers.jsonContentType],
          },
        ),
      );
}

class UploadAdapter implements HttpClientAdapter {
  final requests = <UploadRequest>[];
  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? stream,
    Future<void>? cancelFuture,
  ) async {
    final bytes = <int>[];
    if (stream != null) {
      await for (final chunk in stream) {
        bytes.addAll(chunk);
      }
    }
    final request = UploadRequest(
      options.path,
      options.headers['Idempotency-Key'] as String?,
      bytes,
      options.contentType,
    );
    requests.add(request);
    return request.response.future;
  }

  @override
  void close({bool force = false}) {}
}

Future<void> waitUploads(UploadAdapter adapter, int count) async {
  final deadline = DateTime.now().add(const Duration(seconds: 3));
  while (adapter.requests.length < count && DateTime.now().isBefore(deadline)) {
    await Future<void>.delayed(const Duration(milliseconds: 10));
  }
  expect(adapter.requests.length, greaterThanOrEqualTo(count));
}

void main() {
  test(
    'Discovery query/detail use their dedicated authorized endpoints',
    () async {
      final adapter = DeferredAdapter();
      final api = ApiClient(
        dio: Dio(BaseOptions(baseUrl: 'https://modish.test'))
          ..httpClientAdapter = adapter,
      )..applyToken('fixture');
      final list = api.catalogueDiscovery(
        category: ' shoes ',
        query: ' ботинки ',
        source: 'BAON',
        minPrice: 1000,
        maxPrice: 12000,
        cursor: 'opaque+/=',
        limit: 20,
      );
      await waitForRequests(adapter, 1);
      expect(adapter.requests.first.options.path, '/catalog/discovery');
      expect(adapter.requests.first.options.queryParameters, {
        'limit': 20,
        'category': 'shoes',
        'q': 'ботинки',
        'source': 'BAON',
        'min_price': 1000,
        'max_price': 12000,
        'cursor': 'opaque+/=',
      });
      adapter.requests.first.complete({'products': []});
      expect((await list)['products'], isEmpty);
      final detail = api.catalogueDiscoveryProduct('sku/a');
      await waitForRequests(adapter, 2);
      expect(adapter.requests.last.options.path, '/catalog/discovery/sku%2Fa');
      adapter.requests.last.complete({'id': 'sku/a'});
      expect((await detail)['id'], 'sku/a');
      api.dispose();
    },
  );

  test(
    'Discovery reads retain protected stale-response cancellation',
    () async {
      final adapter = DeferredAdapter();
      final api = ApiClient(
        dio: Dio(BaseOptions(baseUrl: 'https://modish.test'))
          ..httpClientAdapter = adapter,
      )..applyToken('fixture');
      final pending = api.catalogueDiscovery();
      final rejected = expectLater(pending, throwsA(isCancelled));
      await waitForRequests(adapter, 1);
      api.clearToken();
      api.applyToken('another-fixture');
      adapter.requests.single.complete({
        'products': ['old-account'],
      });
      await rejected;
      api.dispose();
    },
  );

  for (final onboarding in [false, true]) {
    final label = onboarding ? 'onboarding' : 'style profile';
    test(
      '$label upload replays complete multipart data and one idempotency key after 401',
      () async {
        final adapter = UploadAdapter();
        final api =
            ApiClient(
                dio: Dio(BaseOptions(baseUrl: 'https://modish.test'))
                  ..httpClientAdapter = adapter,
              )
              ..applyToken('expired')
              ..refreshToken = 'refresh-fixture';
        final path = File('test/fixtures/build12-blouse.jpg').absolute.path;
        final first = onboarding
            ? api.onboardingPhoto(path)
            : api.analyzeStyleProfile(path);
        await waitUploads(adapter, 1);
        final original = adapter.requests[0];
        expect(original.key, matches(RegExp(r'^photo-[0-9a-f]{32}$')));
        expect(original.body.length, greaterThan(1000));
        original.complete({'detail': 'expired'}, 401);
        await waitUploads(adapter, 2);
        expect(adapter.requests[1].path, '/auth/refresh');
        adapter.requests[1].complete({
          'access_token': 'rotated',
          'refresh_token': 'rotated-refresh',
        });
        await waitUploads(adapter, 3);
        final retry = adapter.requests[2];
        expect(retry.path, original.path);
        expect(retry.key, original.key);
        expect(
          retry.body,
          original.body,
          reason: 'Multipart photo is replayed, not an exhausted stream',
        );
        retry.complete({'status': 'ok'});
        await first;
        final second = onboarding
            ? api.onboardingPhoto(path)
            : api.analyzeStyleProfile(path);
        await waitUploads(adapter, 4);
        expect(
          adapter.requests[3].key,
          isNot(original.key),
          reason: 'A new user action is a new job',
        );
        adapter.requests[3].complete({'status': 'ok'});
        await second;
        api.dispose();
      },
    );

    test(
      '$label upload preparation cannot dispatch into a replacement session',
      () async {
        final adapter = UploadAdapter();
        final api = ApiClient(
          dio: Dio(BaseOptions(baseUrl: 'https://modish.test'))
            ..httpClientAdapter = adapter,
        )..applyToken('old-fixture');
        final path = File('test/fixtures/build12-blouse.jpg').absolute.path;
        final pending = onboarding
            ? api.onboardingPhoto(path)
            : api.analyzeStyleProfile(path);
        final rejected = expectLater(pending, throwsA(isCancelled));
        api.clearToken();
        api.applyToken('new-fixture');
        await rejected;
        expect(adapter.requests, isEmpty);
        api.dispose();
      },
    );
  }
}
