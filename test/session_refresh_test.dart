import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/core/network/api_client.dart';

class SessionAdapter implements HttpClientAdapter {
  int refreshCalls = 0;
  int protectedCalls = 0;
  int refreshStatus = 200;
  bool failNetwork = false;
  Object? loginAuthorization;
  final gate = Completer<void>();
  final refreshStarted = Completer<void>();
  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? stream,
    Future<void>? cancelFuture,
  ) async {
    dynamic body = {'email': 'test@example.test'};
    var status = 200;
    if (options.path == '/auth/refresh') {
      refreshCalls++;
      if (!refreshStarted.isCompleted) refreshStarted.complete();
      await gate.future;
      if (failNetwork) {
        throw DioException(
          requestOptions: options,
          type: DioExceptionType.connectionError,
        );
      }
      status = refreshStatus;
      body = {'access_token': 'SYNTHETIC_ACCESS_TOKEN_1', 'refresh_token': 'SYNTHETIC_REFRESH_TOKEN_2'};
    } else if (options.path == '/auth/login') {
      loginAuthorization = options.headers['Authorization'];
      body = {'access_token': 'SYNTHETIC_ACCESS_TOKEN_1', 'refresh_token': 'SYNTHETIC_REFRESH_TOKEN_2'};
    } else {
      protectedCalls++;
      status = options.headers['Authorization'] == 'Bearer SYNTHETIC_ACCESS_TOKEN_1' ? 200 : 401;
    }
    return ResponseBody.fromString(
      jsonEncode(body),
      status,
      headers: {
        Headers.contentTypeHeader: [Headers.jsonContentType],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

void main() {
  test(
    'Concurrent expired requests share one refresh and retry successfully',
    () async {
      final adapter = SessionAdapter();
      final dio = Dio(BaseOptions(baseUrl: 'https://modish.test'))
        ..httpClientAdapter = adapter;
      var signedOut = 0;
      final api = ApiClient(dio: dio, onUnauthorized: () => signedOut++)
        ..applyToken('expired')
        ..refreshToken = 'SYNTHETIC_REFRESH_TOKEN_2';
      final requests = List.generate(8, (_) => api.usersMe());
      await adapter.refreshStarted.future;
      expect(adapter.refreshCalls, 1);
      adapter.gate.complete();
      expect((await Future.wait(requests)).length, 8);
      expect(signedOut, 0);
      expect(adapter.refreshCalls, 1);
    },
  );

  test('A network failure during refresh preserves the session', () async {
    final adapter = SessionAdapter()..failNetwork = true;
    adapter.gate.complete();
    final dio = Dio(BaseOptions(baseUrl: 'https://modish.test'))
      ..httpClientAdapter = adapter;
    var signedOut = 0;
    final api = ApiClient(dio: dio, onUnauthorized: () => signedOut++)
      ..applyToken('expired')
      ..refreshToken = 'SYNTHETIC_REFRESH_TOKEN_2';
    await expectLater(api.usersMe(), throwsA(isA<DioException>()));
    expect(signedOut, 0);
    expect(api.refreshToken, 'SYNTHETIC_REFRESH_TOKEN_2');
  });

  test(
    'Invalid refresh ends session, public login carries no old bearer',
    () async {
      final adapter = SessionAdapter()..refreshStatus = 401;
      adapter.gate.complete();
      final dio = Dio(BaseOptions(baseUrl: 'https://modish.test'))
        ..httpClientAdapter = adapter;
      var signedOut = 0;
      final api = ApiClient(dio: dio, onUnauthorized: () => signedOut++)
        ..applyToken('expired')
        ..refreshToken = 'invalid-refresh';
      await expectLater(api.usersMe(), throwsA(isA<DioException>()));
      expect(signedOut, 1);
      api.applyToken('stale');
      expect(await api.login('test@example.test', 'test-password'), 'SYNTHETIC_ACCESS_TOKEN_1');
      expect(adapter.loginAuthorization, isNull);
    },
  );
}
