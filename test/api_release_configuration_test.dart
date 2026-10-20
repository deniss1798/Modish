import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/network/api_client.dart';

class _RecordingAdapter implements HttpClientAdapter {
  final requests = <Uri>[];

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    requests.add(options.uri);
    return ResponseBody.fromString(
      options.path.startsWith('/auth/')
          ? '{"access_token":"SYNTHETIC_ACCESS"}'
          : '{"status":"ok","database":"ok"}',
      200,
      headers: {
        Headers.contentTypeHeader: [Headers.jsonContentType],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

void main() {
  group('Release API configuration', () {
    for (final url in [
      'https://modish.org.ru',
      'https://api.example.com:8443/api',
      'https://8.8.8.8',
      'https://[2606:4700:4700::1111]',
    ]) {
      test('accepts absolute HTTPS endpoint $url', () {
        expect(ApiClient.validateReleaseBaseUrl(url), url);
      });
    }

    test('normalizes case and outer whitespace without rewriting the host', () {
      expect(
        ApiClient.validateReleaseBaseUrl('  HTTPS://API.EXAMPLE.COM///  '),
        'https://api.example.com',
      );
    });

    for (final url in [
      '',
      'http://modish.org.ru',
      'http://10.0.2.2:8773',
      '//modish.org.ru',
      'modish.org.ru',
      'ftp://modish.org.ru',
      'https:///api',
      'https://',
      'https://user:SYNTHETIC_PASSWORD@modish.org.ru',
      'https://@modish.org.ru',
      'https://modish.org.ru?token=SYNTHETIC_TOKEN',
      'https://modish.org.ru?',
      'https://modish.org.ru#fragment',
      'https://modish.org.ru#',
      'https://bad host.example',
      'https://api.example.com/path with spaces',
      'https://api.example.com/\npath',
      'https://api.example.com\\@bad.example',
      'https://api_example.com',
      'https://-api.example.com',
      'https://api..example.com',
      'https://api.example.com.',
      'https://api.example.com:wrong',
      'https://api.example.com:0',
      'https://api.example.com:65536',
      'https://localhost',
      'https://api.localhost',
      'https://api.local',
      'https://api.internal',
      'https://intranet',
      'https://127.0.0.1',
      'https://10.0.2.2',
      'https://192.168.1.1',
      'https://172.16.0.1',
      'https://169.254.1.1',
      'https://0.0.0.0',
      'https://999.1.1.1',
      'https://127.000.0.1',
      'https://[::1]',
      'https://[0:0:0:0:0:0:0:1]',
      'https://[0:0:0:0:0:0:0:0]',
      'https://[fc00::1]',
      'https://[fe80::1]',
      'https://[::ffff:127.0.0.1]',
      'https://[0:0:0:0:0:ffff:7f00:1]',
    ]) {
      test('rejects an invalid or local release endpoint $url', () {
        expect(
          () => ApiClient.validateReleaseBaseUrl(url),
          throwsA(isA<ApiConfigurationException>()),
        );
      });
    }

    test('rejection never embeds the supplied URL or credentials', () {
      try {
        ApiClient.validateReleaseBaseUrl(
          'http://SYNTHETIC_USER:SYNTHETIC_PASSWORD@private.internal',
        );
        fail('Invalid release configuration was accepted');
      } on ApiConfigurationException catch (error) {
        expect(error.toString(), 'MODISH_RELEASE_API_CONFIGURATION_INVALID');
        expect(error.toString(), isNot(contains('SYNTHETIC_')));
        expect(error.toString(), isNot(contains('private.internal')));
      }
    });

    test(
      'release transport refuses HTTP before health or auth can be sent',
      () {
        final adapter = _RecordingAdapter();
        final dio = Dio(BaseOptions(baseUrl: 'http://10.0.2.2:8773'))
          ..httpClientAdapter = adapter;
        final originalInterceptors = dio.interceptors.length;
        expect(
          () => ApiClient(dio: dio, enforceReleaseConfiguration: true),
          throwsA(isA<ApiConfigurationException>()),
        );
        expect(adapter.requests, isEmpty);
        expect(dio.interceptors.length, originalInterceptors);
        expect(dio.options.headers.containsKey('Authorization'), isFalse);
        dio.close();
      },
    );

    test('release transport accepts HTTPS health and authentication', () async {
      final adapter = _RecordingAdapter();
      final dio = Dio(BaseOptions(baseUrl: 'https://api.example.com'))
        ..httpClientAdapter = adapter;
      final api = ApiClient(dio: dio, enforceReleaseConfiguration: true);
      addTearDown(api.dispose);
      expect(await api.pingHealth(), isTrue);
      expect(
        await api.login('fixture@example.com', 'SYNTHETIC_PASSWORD'),
        'SYNTHETIC_ACCESS',
      );
      expect(adapter.requests.map((uri) => uri.scheme), everyElement('https'));
      expect(adapter.requests.map((uri) => uri.path), [
        '/health/ready',
        '/auth/login',
      ]);
    });

    test('debug transport retains the emulator HTTP workflow', () async {
      final adapter = _RecordingAdapter();
      final dio = Dio(BaseOptions(baseUrl: 'http://10.0.2.2:8773'))
        ..httpClientAdapter = adapter;
      final api = ApiClient(dio: dio);
      addTearDown(api.dispose);
      expect(await api.pingHealth(), isTrue);
      expect(
        await api.login('fixture@example.com', 'SYNTHETIC_PASSWORD'),
        'SYNTHETIC_ACCESS',
      );
      expect(adapter.requests.map((uri) => uri.scheme), everyElement('http'));
    });

    test('release default resolves to the configured HTTPS production API', () {
      expect(
        ApiClient.resolvedBaseUrl(enforceReleaseConfiguration: true),
        'https://modish.org.ru',
      );
    });
  });

  testWidgets('bad release configuration shows an actionable startup page', (
    tester,
  ) async {
    var constructionAttempts = 0;
    await tester.pumpWidget(
      ModishBootstrap(
        controllerFactory: () {
          constructionAttempts++;
          throw const ApiConfigurationException();
        },
      ),
    );
    await tester.pump();
    expect(tester.takeException(), isNull);
    expect(find.text('Не удалось запустить приложение'), findsOneWidget);
    expect(
      find.text(
        'Установите актуальную версию Modish из официального источника.',
      ),
      findsOneWidget,
    );
    expect(find.byType(AppScope), findsNothing);
    expect(find.byType(TextField), findsNothing);
    expect(find.textContaining('MODISH_RELEASE_'), findsNothing);
    expect(find.textContaining('https://'), findsNothing);
    expect(find.textContaining('http://'), findsNothing);
    expect(find.text('Повторить'), findsNothing);
    await tester.pump(const Duration(seconds: 5));
    expect(constructionAttempts, 1);
    expect(tester.takeException(), isNull);
  });
}
