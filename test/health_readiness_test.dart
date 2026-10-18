import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/network/api_client.dart';

const _healthy = {'status': 'ok', 'database': 'ok', 'version': 'fixture'};

class _Reply {
  const _Reply(this.status, this.data);
  final int status;
  final dynamic data;

  ResponseBody response() => ResponseBody.fromString(
    jsonEncode(data),
    status,
    headers: {
      Headers.contentTypeHeader: [Headers.jsonContentType],
    },
  );
}

class _ProbeAdapter implements HttpClientAdapter {
  final requests = <RequestOptions>[];
  final List<_Reply> replies;
  DioExceptionType? failure;
  Object? innerError;
  Completer<ResponseBody>? pending;

  _ProbeAdapter([this.replies = const []]);

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    requests.add(options);
    if (failure != null) {
      throw DioException(
        requestOptions: options,
        type: failure!,
        error: innerError,
      );
    }
    if (pending != null) return pending!.future;
    return requests.length <= replies.length
        ? replies[requests.length - 1].response()
        : const _Reply(503, {'status': 'degraded'}).response();
  }

  @override
  void close({bool force = false}) {}
}

ApiClient _client(_ProbeAdapter adapter) {
  final api = ApiClient(
    dio: Dio(
      BaseOptions(
        baseUrl: 'https://health-fixture.example.test',
        // The probe must still validate its own HTTP status when an injected
        // or future shared transport accepts other responses.
        validateStatus: (_) => true,
        connectTimeout: const Duration(seconds: 45),
      ),
    )..httpClientAdapter = adapter,
  );
  addTearDown(api.dispose);
  return api;
}

Future<void> _waitFor(_ProbeAdapter adapter, int count) async {
  final deadline = DateTime.now().add(const Duration(seconds: 3));
  while (adapter.requests.length < count && DateTime.now().isBefore(deadline)) {
    await Future<void>.delayed(const Duration(milliseconds: 10));
  }
  expect(adapter.requests, hasLength(count));
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('Readiness requires HTTP 200 and both positive health fields', () async {
    final adapter = _ProbeAdapter([const _Reply(200, _healthy)]);
    final api = _client(adapter)..applyToken('SYNTHETIC_OLD_ACCESS');
    expect(await api.pingHealth(), isTrue);
    expect(adapter.requests.map((request) => request.path), ['/health/ready']);
    final request = adapter.requests.single;
    expect(request.headers.containsKey('Authorization'), isFalse);
    expect(request.connectTimeout, const Duration(seconds: 10));
    expect(request.sendTimeout, const Duration(seconds: 10));
    expect(request.receiveTimeout, const Duration(seconds: 10));
  });

  test(
    'A probe omits authorization without mutating the stored session',
    () async {
      final adapter = _ProbeAdapter([
        const _Reply(401, {'detail': 'Unexpected health authorization'}),
        const _Reply(200, _healthy),
      ]);
      final api = _client(adapter)
        ..applyToken('SYNTHETIC_CURRENT_ACCESS')
        ..refreshToken = 'SYNTHETIC_CURRENT_REFRESH_FOR_LOCAL_TEST';
      final epoch = api.sessionEpoch;
      var unauthorized = 0;
      api.onUnauthorized = () => unauthorized++;
      expect(await api.pingHealth(), isFalse);
      await api.usersMe();
      expect(adapter.requests.map((request) => request.path), [
        '/health/ready',
        '/users/me',
      ]);
      expect(
        adapter.requests.first.headers.containsKey('Authorization'),
        isFalse,
      );
      expect(
        adapter.requests.last.headers['Authorization'],
        'Bearer SYNTHETIC_CURRENT_ACCESS',
      );
      expect(api.refreshToken, 'SYNTHETIC_CURRENT_REFRESH_FOR_LOCAL_TEST');
      expect(api.sessionEpoch, epoch);
      expect(unauthorized, 0);
      expect(adapter.requests.last.connectTimeout, const Duration(seconds: 45));
    },
  );

  final invalidBodies = <dynamic>[
    {'status': 'degraded', 'database': 'ok'},
    {'status': 'ok', 'database': 'error'},
    {'status': 'ok'},
    {'database': 'ok'},
    {'status': true, 'database': true},
    {'status': 'OK', 'database': 'OK'},
    <String, dynamic>{},
    <dynamic>[],
    '<html>service unavailable</html>',
    null,
  ];
  for (var index = 0; index < invalidBodies.length; index++) {
    test(
      'Readiness rejects degraded or malformed body $index without fallback',
      () async {
        final adapter = _ProbeAdapter([_Reply(200, invalidBodies[index])]);
        expect(await _client(adapter).pingHealth(), isFalse);
        expect(adapter.requests.map((request) => request.path), [
          '/health/ready',
        ]);
      },
    );
  }

  for (final status in [201, 204, 301, 400, 401, 403, 410, 500, 503]) {
    test(
      'HTTP $status is unready even with an ok body and never falls back',
      () async {
        final adapter = _ProbeAdapter([_Reply(status, _healthy)]);
        final api = _client(adapter)
          ..applyToken('SYNTHETIC_OLD_ACCESS')
          ..refreshToken = 'SYNTHETIC_REFRESH_FIXTURE_FOR_LOCAL_TEST';
        var unauthorized = 0;
        api.onUnauthorized = () => unauthorized++;
        expect(await api.pingHealth(), isFalse);
        expect(adapter.requests.map((request) => request.path), [
          '/health/ready',
        ]);
        expect(unauthorized, 0);
      },
    );
  }

  test(
    'Only missing modern readiness permits a strictly healthy legacy probe',
    () async {
      final adapter = _ProbeAdapter([
        const _Reply(404, {'detail': 'Not Found'}),
        const _Reply(200, _healthy),
      ]);
      expect(await _client(adapter).pingHealth(), isTrue);
      expect(adapter.requests.map((request) => request.path), [
        '/health/ready',
        '/health',
      ]);
    },
  );

  for (var index = 0; index < invalidBodies.length; index++) {
    test(
      'Legacy fallback also rejects degraded or malformed body $index',
      () async {
        final adapter = _ProbeAdapter([
          const _Reply(404, {'detail': 'Not Found'}),
          _Reply(200, invalidBodies[index]),
        ]);
        expect(await _client(adapter).pingHealth(), isFalse);
        expect(adapter.requests.map((request) => request.path), [
          '/health/ready',
          '/health',
        ]);
      },
    );
  }

  for (final status in [404, 503]) {
    test('Legacy HTTP $status cannot become readiness or recurse', () async {
      final adapter = _ProbeAdapter([
        const _Reply(404, {'detail': 'Not Found'}),
        _Reply(status, _healthy),
      ]);
      expect(await _client(adapter).pingHealth(), isFalse);
      expect(adapter.requests.map((request) => request.path), [
        '/health/ready',
        '/health',
      ]);
    });
  }

  for (final type in [
    DioExceptionType.connectionError,
    DioExceptionType.connectionTimeout,
    DioExceptionType.receiveTimeout,
    DioExceptionType.sendTimeout,
    DioExceptionType.badCertificate,
    DioExceptionType.cancel,
    DioExceptionType.unknown,
  ]) {
    test('Probe $type neither retries nor falls back', () async {
      final adapter = _ProbeAdapter()
        ..failure = type
        ..innerError = StateError(
          'Connection reset private fixture diagnostics',
        );
      expect(await _client(adapter).pingHealth(), isFalse);
      expect(adapter.requests.map((request) => request.path), [
        '/health/ready',
      ]);
    });
  }

  test(
    'Session change before dispatch prevents a stale health request',
    () async {
      final adapter = _ProbeAdapter([const _Reply(200, _healthy)]);
      final api = _client(adapter);
      final result = api.pingHealth();
      api.clearToken();
      expect(await result, isFalse);
      expect(adapter.requests, isEmpty);
    },
  );

  for (final status in [200, 404]) {
    test(
      'Old-session HTTP $status cannot authorize readiness or legacy fallback',
      () async {
        final adapter = _ProbeAdapter()..pending = Completer();
        final api = _client(adapter);
        final result = api.pingHealth();
        await _waitFor(adapter, 1);
        api.clearToken();
        adapter.pending!.complete(_Reply(status, _healthy).response());
        expect(await result, isFalse);
        expect(adapter.requests, hasLength(1));
      },
    );
  }

  test(
    'Disposal before a probe does not dispatch against a closed client',
    () async {
      final adapter = _ProbeAdapter([const _Reply(200, _healthy)]);
      final api = _client(adapter);
      api.dispose();
      expect(await api.pingHealth(), isFalse);
      expect(adapter.requests, isEmpty);
    },
  );

  test(
    'Disposal during a probe cannot publish a late healthy response',
    () async {
      final adapter = _ProbeAdapter()..pending = Completer();
      final api = _client(adapter);
      final result = api.pingHealth();
      await _waitFor(adapter, 1);
      api.dispose();
      adapter.pending!.complete(const _Reply(200, _healthy).response());
      expect(await result, isFalse);
      expect(adapter.requests, hasLength(1));
    },
  );

  for (final operation in ['login', 'register']) {
    test(
      'Unready service prevents AppController $operation from posting credentials',
      () async {
        final adapter = _ProbeAdapter([
          const _Reply(503, {'status': 'degraded', 'database': 'error'}),
        ]);
        final api = _client(adapter);
        final controller = AppController(apiClient: api)
          ..email = 'fixture@example.test'
          ..stage = AppStage.auth;
        addTearDown(controller.dispose);
        if (operation == 'login') {
          await controller.login('SYNTHETIC_PASSWORD_1');
        } else {
          await controller.register('SYNTHETIC_PASSWORD_1');
        }
        expect(adapter.requests.map((request) => request.path), [
          '/health/ready',
        ]);
        expect(
          adapter.requests.every((request) => request.method == 'GET'),
          isTrue,
        );
        expect(controller.token, isNull);
        expect(controller.stage, AppStage.auth);
        expect(controller.error, contains('Сервис временно недоступен'));
        expect(controller.isLoading, isFalse);
      },
    );
  }

  test(
    'Disposed AppController cannot post a login after readiness finishes',
    () async {
      final adapter = _ProbeAdapter()..pending = Completer();
      final api = _client(adapter);
      final controller = AppController(apiClient: api)
        ..email = 'fixture@example.test';
      final operation = controller.login('SYNTHETIC_PASSWORD_1');
      await _waitFor(adapter, 1);
      controller.dispose();
      adapter.pending!.complete(const _Reply(200, _healthy).response());
      await operation;
      expect(adapter.requests.map((request) => request.path), [
        '/health/ready',
      ]);
      expect(controller.token, isNull);
    },
  );
}
