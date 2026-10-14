import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/core/network/api_client.dart';

class DeferredRequest {
  DeferredRequest(this.options);
  final RequestOptions options;
  final response = Completer<ResponseBody>();
  void complete(Map<String, dynamic> data, [int status = 200]) {
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
}

class DeferredAdapter implements HttpClientAdapter {
  final requests = <DeferredRequest>[];
  bool closed = false;
  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? stream,
    Future<void>? cancelFuture,
  ) {
    final request = DeferredRequest(options);
    requests.add(request);
    return request.response.future;
  }

  @override
  void close({bool force = false}) => closed = true;
}

Future<void> drain() => Future<void>.delayed(const Duration(milliseconds: 10));
Future<void> waitForRequests(DeferredAdapter adapter, int count) async {
  final deadline = DateTime.now().add(const Duration(seconds: 3));
  while (adapter.requests.length < count && DateTime.now().isBefore(deadline)) {
    await drain();
  }
  expect(adapter.requests.length, greaterThanOrEqualTo(count));
}

final isCancelled = isA<DioException>().having(
  (exception) => exception.type,
  'type',
  DioExceptionType.cancel,
);

void main() {
  ApiClient client(
    DeferredAdapter adapter, {
    void Function()? onUnauthorized,
  }) => ApiClient(
    dio: Dio(BaseOptions(baseUrl: 'https://modish.test'))
      ..httpClientAdapter = adapter,
    onUnauthorized: onUnauthorized,
  );

  for (final method in ['GET', 'POST']) {
    test(
      'Immediate logout cancels $method before interceptor dispatch',
      () async {
        final adapter = DeferredAdapter();
        final api = client(adapter)..applyToken('old');
        final read = method == 'GET'
            ? api.usersMe()
            : api.recordRecommendationEvent(
                eventType: 'save',
                productId: 'old-product',
              );
        final rejected = expectLater(read, throwsA(isCancelled));
        api.clearToken();
        await rejected;
        expect(
          adapter.requests,
          isEmpty,
          reason: 'Old authorization never reaches transport',
        );
      },
    );
  }

  for (final operation in ['login', 'register']) {
    test('Pending $operation cannot accept a token after logout', () async {
      final adapter = DeferredAdapter();
      final api = client(adapter);
      var changed = 0;
      api.onSessionChanged = (_, _) async => changed++;
      final pending = operation == 'login'
          ? api.login('test@example.test', 'test-password')
          : api.register('test@example.test', 'test-password');
      final rejected = expectLater(pending, throwsA(isCancelled));
      await waitForRequests(adapter, 1);
      api.clearToken();
      adapter.requests.single.complete({
        'access_token': 'SYNTHETIC_ACCESS_TOKEN_1',
        'refresh_token': 'SYNTHETIC_REFRESH_TOKEN_2',
      });
      await rejected;
      expect(api.refreshToken, isNull);
      expect(changed, 0);
    });
  }

  test(
    'Successful old account response is discarded after a new login',
    () async {
      final adapter = DeferredAdapter();
      var unauthorized = 0;
      final api = client(adapter, onUnauthorized: () => unauthorized++)
        ..applyToken('old');
      final old = api.usersMe();
      final rejected = expectLater(old, throwsA(isCancelled));
      await waitForRequests(adapter, 1);
      final login = api.login('new@example.test', 'test-password');
      await waitForRequests(adapter, 2);
      adapter.requests[1].complete({
        'access_token': 'SYNTHETIC_ACCESS_TOKEN_3',
        'refresh_token': 'SYNTHETIC_REFRESH_TOKEN_4',
      });
      await login;
      adapter.requests[0].complete({'email': 'old@example.test'});
      await rejected;
      expect(unauthorized, 0);
      expect(api.refreshToken, 'SYNTHETIC_REFRESH_TOKEN_4');
    },
  );

  test('Old account 401 cannot clear a newly accepted session', () async {
    final adapter = DeferredAdapter();
    var unauthorized = 0;
    final api = client(adapter, onUnauthorized: () => unauthorized++)
      ..applyToken('old')
      ..refreshToken = 'old-refresh';
    final old = api.usersMe();
    final rejected = expectLater(old, throwsA(isA<DioException>()));
    await waitForRequests(adapter, 1);
    final login = api.login('new@example.test', 'test-password');
    await waitForRequests(adapter, 2);
    adapter.requests[1].complete({
      'access_token': 'SYNTHETIC_ACCESS_TOKEN_3',
      'refresh_token': 'SYNTHETIC_REFRESH_TOKEN_4',
    });
    await login;
    adapter.requests[0].complete({'detail': 'Invalid token'}, 401);
    await rejected;
    expect(adapter.requests.length, 2);
    expect(api.refreshToken, 'SYNTHETIC_REFRESH_TOKEN_4');
    expect(unauthorized, 0);
  });

  test('Session upgrade response cannot undo logout', () async {
    final adapter = DeferredAdapter();
    final api = client(adapter)..applyToken('old');
    final upgrade = api.upgradeSession();
    final rejected = expectLater(upgrade, throwsA(isCancelled));
    await waitForRequests(adapter, 1);
    api.clearToken();
    adapter.requests.single.complete({
      'access_token': 'SYNTHETIC_ACCESS_TOKEN_1',
      'refresh_token': 'SYNTHETIC_REFRESH_TOKEN_2',
    });
    await rejected;
    expect(api.refreshToken, isNull);
  });

  test(
    'Logout cancels a delayed GET retry instead of replaying old authorization',
    () async {
      final adapter = DeferredAdapter();
      final api = client(adapter)..applyToken('old');
      final read = api.usersMe();
      final rejected = expectLater(read, throwsA(isCancelled));
      await waitForRequests(adapter, 1);
      final request = adapter.requests.single;
      request.response.completeError(
        DioException(
          requestOptions: request.options,
          type: DioExceptionType.connectionError,
        ),
      );
      await drain();
      api.clearToken();
      await rejected;
      expect(adapter.requests.length, 1);
    },
  );

  test('An obsolete refresh cannot block refresh of a new session', () async {
    final adapter = DeferredAdapter();
    final api = client(adapter)
      ..applyToken('old-expired')
      ..refreshToken = 'old-refresh';
    final old = api.usersMe();
    final oldRejected = expectLater(old, throwsA(isA<DioException>()));
    await waitForRequests(adapter, 1);
    adapter.requests[0].complete({'detail': 'expired'}, 401);
    await waitForRequests(adapter, 2);
    expect(adapter.requests[1].options.path, '/auth/refresh');
    api.clearToken();
    api.applyToken('new-expired');
    api.refreshToken = 'SYNTHETIC_REFRESH_TOKEN_4';
    final current = api.usersMe();
    await waitForRequests(adapter, 3);
    adapter.requests[2].complete({'detail': 'expired'}, 401);
    await waitForRequests(adapter, 4);
    expect(adapter.requests[3].options.path, '/auth/refresh');
    expect(adapter.requests[3].options.data['refresh_token'], 'SYNTHETIC_REFRESH_TOKEN_4');
    adapter.requests[1].complete({
      'access_token': 'SYNTHETIC_ACCESS_TOKEN_1',
      'refresh_token': 'SYNTHETIC_REFRESH_TOKEN_2',
    });
    await oldRejected;
    expect(api.refreshToken, 'SYNTHETIC_REFRESH_TOKEN_4');
    adapter.requests[3].complete({
      'access_token': 'SYNTHETIC_ACCESS_TOKEN_5',
      'refresh_token': 'SYNTHETIC_REFRESH_TOKEN_6',
    });
    await waitForRequests(adapter, 5);
    adapter.requests[4].complete({'email': 'current@example.test'});
    expect((await current)['email'], 'current@example.test');
    expect(api.refreshToken, 'SYNTHETIC_REFRESH_TOKEN_6');
  });

  test(
    'Malformed refresh completes the request without clearing the session',
    () async {
      final adapter = DeferredAdapter();
      var unauthorized = 0;
      final api = client(adapter, onUnauthorized: () => unauthorized++)
        ..applyToken('expired')
        ..refreshToken = 'refresh';
      final read = api.usersMe();
      final rejected = expectLater(read, throwsA(isA<DioException>()));
      await waitForRequests(adapter, 1);
      adapter.requests[0].complete({'detail': 'expired'}, 401);
      await waitForRequests(adapter, 2);
      adapter.requests[1].complete({});
      await rejected;
      expect(unauthorized, 0);
      expect(api.refreshToken, 'refresh');
    },
  );

  test(
    'Disposing a client closes transport and rejects late responses',
    () async {
      final adapter = DeferredAdapter();
      var notified = 0;
      final api = client(adapter, onUnauthorized: () => notified++)
        ..applyToken('old')
        ..refreshToken = 'refresh';
      api.onSessionChanged = (_, _) async => notified++;
      final read = api.usersMe();
      final rejected = expectLater(read, throwsA(isCancelled));
      await waitForRequests(adapter, 1);
      api.dispose();
      expect(adapter.closed, isTrue);
      adapter.requests.single.complete({'email': 'obsolete@example.test'});
      await rejected;
      expect(api.refreshToken, isNull);
      expect(api.onUnauthorized, isNull);
      expect(api.onSessionChanged, isNull);
      expect(notified, 0);
    },
  );
}
