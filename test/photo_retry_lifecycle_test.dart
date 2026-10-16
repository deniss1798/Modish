import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/core/network/api_client.dart';

class _Upload {
  _Upload(this.options, List<int> bytes)
    : body = List<int>.unmodifiable(bytes),
      path = options.path,
      key = options.headers['Idempotency-Key'] as String?,
      contentType = options.contentType;

  final RequestOptions options;
  final String path;
  final String? key;
  final String? contentType;
  final List<int> body;
  final response = Completer<ResponseBody>();

  void complete(Object? data, [int status = 200]) {
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

  void completeHtml(String html) {
    response.complete(
      ResponseBody.fromString(
        html,
        200,
        headers: {
          Headers.contentTypeHeader: ['text/html'],
        },
      ),
    );
  }

  void fail(DioExceptionType type) {
    response.completeError(
      DioException(
        requestOptions: options,
        type: type,
        message: 'Synthetic transport failure after multipart dispatch',
      ),
    );
  }
}

class _UploadAdapter implements HttpClientAdapter {
  final requests = <_Upload>[];
  bool closed = false;

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
    final request = _Upload(options, bytes);
    requests.add(request);
    return request.response.future;
  }

  @override
  void close({bool force = false}) => closed = true;
}

Future<void> _waitUploads(_UploadAdapter adapter, int count) async {
  final deadline = DateTime.now().add(const Duration(seconds: 3));
  while (adapter.requests.length < count && DateTime.now().isBefore(deadline)) {
    await Future<void>.delayed(const Duration(milliseconds: 5));
  }
  expect(adapter.requests.length, count);
}

final _cancelled = isA<DioException>().having(
  (error) => error.type,
  'type',
  DioExceptionType.cancel,
);

void main() {
  late Directory directory;
  late File photo;
  late List<int> originalBytes;
  late _UploadAdapter adapter;
  late ApiClient api;

  setUp(() async {
    directory = await Directory.systemTemp.createTemp('modish-photo-retry-');
    originalBytes = List<int>.generate(2048, (index) => index % 251);
    photo = await File(
      '${directory.path}/original.jpg',
    ).writeAsBytes(originalBytes, flush: true);
    adapter = _UploadAdapter();
    api = ApiClient(
      dio: Dio(BaseOptions(baseUrl: 'https://modish.test'))
        ..httpClientAdapter = adapter,
    )..applyToken('synthetic-account');
  });

  tearDown(() async {
    if (!adapter.closed) api.dispose();
    await directory.delete(recursive: true);
  });

  Future<void> upload(bool onboarding, [File? file]) async {
    final path = (file ?? photo).absolute.path;
    if (onboarding) {
      await api.onboardingPhoto(path);
    } else {
      await api.analyzeStyleProfile(path);
    }
  }

  void assertSameAttempt(_Upload original, _Upload retry) {
    expect(original.key, matches(RegExp(r'^photo-[0-9a-f]{32}$')));
    expect(retry.path, original.path);
    expect(retry.key, original.key);
    expect(retry.contentType, original.contentType);
    expect(
      retry.body,
      original.body,
      reason: 'Manual retry must replay the entire immutable multipart body',
    );
  }

  for (final onboarding in [false, true]) {
    final label = onboarding ? 'onboarding' : 'style profile';
    final endpoint = onboarding
        ? '/onboarding/photo'
        : '/style-profile/analyze';

    for (final failure in [
      DioExceptionType.connectionError,
      DioExceptionType.receiveTimeout,
    ]) {
      test(
        '$label manual retry after $failure keeps key and exact body',
        () async {
          final first = upload(onboarding);
          final failed = expectLater(first, throwsA(isA<DioException>()));
          await _waitUploads(adapter, 1);
          final original = adapter.requests.single;
          expect(original.path, endpoint);
          expect(String.fromCharCodes(original.body), contains('image/jpeg'));
          expect(
            String.fromCharCodes(original.body),
            contains(String.fromCharCodes(originalBytes)),
          );
          original.fail(failure);
          await failed;

          final retry = upload(onboarding);
          await _waitUploads(adapter, 2);
          assertSameAttempt(original, adapter.requests.last);
          adapter.requests.last.complete({'status': 'ok'});
          await retry;

          final nextAnalysis = upload(onboarding);
          await _waitUploads(adapter, 3);
          expect(adapter.requests.last.key, isNot(original.key));
          adapter.requests.last.complete({'status': 'ok'});
          await nextAnalysis;
        },
      );
    }

    const recoverableResponses = <(int, String)>[
      (409, 'Анализ уже выполняется. Подождите и повторите запрос.'),
      (
        409,
        'Результат анализа не подтверждён. Повторный запуск временно недоступен.',
      ),
      (502, 'Unknown upstream response'),
      (504, 'Unknown gateway deadline'),
      (500, 'Unknown server response'),
    ];
    for (final (status, detail) in recoverableResponses) {
      test('$label retains attempt for nonterminal $status: $detail', () async {
        final first = upload(onboarding);
        final failed = expectLater(first, throwsA(isA<DioException>()));
        await _waitUploads(adapter, 1);
        final original = adapter.requests.single;
        original.complete({'detail': detail}, status);
        await failed;

        final retry = upload(onboarding);
        await _waitUploads(adapter, 2);
        assertSameAttempt(original, adapter.requests.last);
        adapter.requests.last.complete({'status': 'ok'});
        await retry;
      });
    }

    for (final payload in <Object>['<html>Proxy response</html>', const []]) {
      test(
        '$label malformed successful payload retains the attempt: $payload',
        () async {
          final first = upload(onboarding);
          final failed = expectLater(first, throwsA(isA<DioException>()));
          await _waitUploads(adapter, 1);
          final original = adapter.requests.single;
          if (payload is String) {
            original.completeHtml(payload);
          } else {
            original.complete(payload);
          }
          await failed;

          final retry = upload(onboarding);
          await _waitUploads(adapter, 2);
          assertSameAttempt(original, adapter.requests.last);
          adapter.requests.last.complete({'status': 'ok'});
          await retry;

          final nextAnalysis = upload(onboarding);
          await _waitUploads(adapter, 3);
          expect(adapter.requests.last.key, isNot(original.key));
          adapter.requests.last.complete({'status': 'ok'});
          await nextAnalysis;
        },
      );
    }

    const terminalResponses = <(int, String)>[
      (502, 'Не удалось выполнить анализ фото. Попробуйте ещё раз.'),
      (504, 'Анализ занял слишком много времени. Попробуйте ещё раз.'),
      (409, 'Анализ завершён без результата. Загрузите фото повторно.'),
    ];
    for (final (status, detail) in terminalResponses) {
      test(
        '$label terminal $status clears only the finished attempt',
        () async {
          final first = upload(onboarding);
          final failed = expectLater(first, throwsA(isA<DioException>()));
          await _waitUploads(adapter, 1);
          final original = adapter.requests.single;
          original.complete({'detail': detail}, status);
          await failed;

          final nextAnalysis = upload(onboarding);
          await _waitUploads(adapter, 2);
          expect(adapter.requests.last.key, isNot(original.key));
          adapter.requests.last.complete({'status': 'ok'});
          await nextAnalysis;
        },
      );
    }

    test(
      '$label changed bytes or path create a new attempt and MIME payload',
      () async {
        final first = upload(onboarding);
        final firstFailed = expectLater(first, throwsA(isA<DioException>()));
        await _waitUploads(adapter, 1);
        final original = adapter.requests.single;
        original.fail(DioExceptionType.connectionError);
        await firstFailed;

        final changedBytes = originalBytes.map((byte) => byte ^ 0x5a).toList();
        await photo.writeAsBytes(changedBytes, flush: true);
        final changed = upload(onboarding);
        final changedFailed = expectLater(
          changed,
          throwsA(isA<DioException>()),
        );
        await _waitUploads(adapter, 2);
        final changedRequest = adapter.requests.last;
        expect(changedRequest.key, isNot(original.key));
        expect(
          String.fromCharCodes(changedRequest.body),
          contains(String.fromCharCodes(changedBytes)),
        );
        changedRequest.fail(DioExceptionType.connectionError);
        await changedFailed;

        final moved = await File(
          '${directory.path}/same-bytes.png',
        ).writeAsBytes(changedBytes, flush: true);
        final movedUpload = upload(onboarding, moved);
        await _waitUploads(adapter, 3);
        final movedRequest = adapter.requests.last;
        expect(movedRequest.key, isNot(changedRequest.key));
        expect(String.fromCharCodes(movedRequest.body), contains('image/png'));
        expect(
          String.fromCharCodes(movedRequest.body),
          contains('same-bytes.png'),
        );
        movedRequest.complete({'status': 'ok'});
        await movedUpload;
      },
    );

    for (final oldSuccess in [true, false]) {
      test(
        '$label late older ${oldSuccess ? 'success' : 'terminal failure'} keeps newer pending attempt',
        () async {
          final old = upload(onboarding);
          final oldFailure = oldSuccess
              ? null
              : expectLater(old, throwsA(isA<DioException>()));
          await _waitUploads(adapter, 1);
          final older = adapter.requests.single;
          final newerFile = await File(
            '${directory.path}/newer.jpg',
          ).writeAsBytes(originalBytes.reversed.toList(), flush: true);
          final newer = upload(onboarding, newerFile);
          final newerFailure = expectLater(newer, throwsA(isA<DioException>()));
          await _waitUploads(adapter, 2);
          final pending = adapter.requests.last;
          expect(pending.key, isNot(older.key));

          if (oldSuccess) {
            older.complete({'status': 'ok'});
            await old;
          } else {
            older.complete({
              'detail': 'Не удалось выполнить анализ фото. Попробуйте ещё раз.',
            }, 502);
            await oldFailure;
          }
          pending.fail(DioExceptionType.connectionError);
          await newerFailure;

          final retry = upload(onboarding, newerFile);
          await _waitUploads(adapter, 3);
          assertSameAttempt(pending, adapter.requests.last);
          adapter.requests.last.complete({'status': 'ok'});
          await retry;
        },
      );
    }

    test(
      '$label refresh replays captured bytes after original disk file changes',
      () async {
        api.refreshToken = 'synthetic-refresh';
        final first = upload(onboarding);
        await _waitUploads(adapter, 1);
        final original = adapter.requests.single;
        original.complete({'detail': 'expired access'}, 401);
        await _waitUploads(adapter, 2);
        final refresh = adapter.requests.last;
        expect(refresh.path, '/auth/refresh');
        final changedBytes = List<int>.filled(originalBytes.length, 0x5a);
        await photo.writeAsBytes(changedBytes, flush: true);
        refresh.complete({
          'access_token': 'SYNTHETIC_ACCESS_TOKEN_1',
          'refresh_token': 'SYNTHETIC_REFRESH_TOKEN_2',
        });
        await _waitUploads(adapter, 3);
        assertSameAttempt(original, adapter.requests.last);
        adapter.requests.last.complete({'status': 'ok'});
        await first;

        final nextAnalysis = upload(onboarding);
        await _waitUploads(adapter, 4);
        expect(adapter.requests.last.key, isNot(original.key));
        expect(
          String.fromCharCodes(adapter.requests.last.body),
          contains(String.fromCharCodes(changedBytes)),
        );
        adapter.requests.last.complete({'status': 'ok'});
        await nextAnalysis;
      },
    );

    test(
      '$label old account completion cannot clear replacement account pending upload',
      () async {
        final old = upload(onboarding);
        final oldCancelled = expectLater(old, throwsA(_cancelled));
        await _waitUploads(adapter, 1);
        final oldRequest = adapter.requests.single;
        api.clearToken();
        api.applyToken('replacement-account');
        final next = upload(onboarding);
        final nextFailed = expectLater(next, throwsA(isA<DioException>()));
        await _waitUploads(adapter, 2);
        final pending = adapter.requests.last;
        expect(pending.key, isNot(oldRequest.key));
        expect(
          pending.options.headers['Authorization'],
          'Bearer replacement-account',
        );
        oldRequest.complete({'status': 'ok'});
        await oldCancelled;
        pending.fail(DioExceptionType.connectionError);
        await nextFailed;

        final retry = upload(onboarding);
        await _waitUploads(adapter, 3);
        assertSameAttempt(pending, adapter.requests.last);
        adapter.requests.last.complete({'status': 'ok'});
        await retry;
      },
    );

    for (final dispose in [false, true]) {
      test(
        '$label ${dispose ? 'dispose' : 'session replacement'} during preparation prevents dispatch',
        () async {
          final pending = upload(onboarding);
          final cancelled = expectLater(pending, throwsA(_cancelled));
          if (dispose) {
            api.dispose();
          } else {
            api.clearToken();
            api.applyToken('replacement-account');
          }
          await cancelled;
          expect(adapter.requests, isEmpty);
        },
      );
    }

    test(
      '$label rejects photos larger than five MiB before any dispatch',
      () async {
        final oversized = await File(
          '${directory.path}/oversized.jpg',
        ).writeAsBytes(Uint8List(5 * 1024 * 1024 + 1), flush: true);
        await expectLater(upload(onboarding, oversized), throwsA(anything));
        expect(adapter.requests, isEmpty);

        final valid = upload(onboarding);
        await _waitUploads(adapter, 1);
        adapter.requests.single.complete({'status': 'ok'});
        await valid;
      },
    );
  }

  test(
    'Style and onboarding retain separate pending attempts for the same photo',
    () async {
      final style = upload(false);
      final styleFailed = expectLater(style, throwsA(isA<DioException>()));
      await _waitUploads(adapter, 1);
      final styleRequest = adapter.requests.single;
      final onboarding = upload(true);
      final onboardingFailed = expectLater(
        onboarding,
        throwsA(isA<DioException>()),
      );
      await _waitUploads(adapter, 2);
      final onboardingRequest = adapter.requests.last;
      expect(onboardingRequest.key, isNot(styleRequest.key));
      styleRequest.fail(DioExceptionType.connectionError);
      onboardingRequest.fail(DioExceptionType.connectionError);
      await Future.wait([styleFailed, onboardingFailed]);

      final styleRetry = upload(false);
      await _waitUploads(adapter, 3);
      assertSameAttempt(styleRequest, adapter.requests.last);
      adapter.requests.last.complete({'status': 'ok'});
      await styleRetry;
      final onboardingRetry = upload(true);
      await _waitUploads(adapter, 4);
      assertSameAttempt(onboardingRequest, adapter.requests.last);
      adapter.requests.last.complete({'status': 'ok'});
      await onboardingRetry;
    },
  );
}
