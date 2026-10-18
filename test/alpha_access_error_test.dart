import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/core/network/api_client.dart';

// This is the backend ALPHA_ACCESS_MESSAGE contract. Raw server details are
// otherwise untrusted, including messages which merely start with this text.
const _alphaMessage =
    'Доступ к Modish пока открыт только участникам закрытого тестирования. Свяжитесь с командой, чтобы получить доступ.';
const _genericForbidden =
    'Это действие сейчас недоступно. Вернитесь на предыдущий экран или повторите позже.';
const _photoForbidden =
    'Анализ фото сейчас недоступен. Продолжите подбор без фото или повторите позже.';

DioException _error(String path, dynamic data, {int status = 403}) {
  final request = RequestOptions(path: path);
  return DioException(
    type: DioExceptionType.badResponse,
    requestOptions: request,
    response: Response(requestOptions: request, statusCode: status, data: data),
    message: 'private diagnostics must stay hidden',
  );
}

class _AlphaAdapter implements HttpClientAdapter {
  final requests = <String>[];
  bool refreshFlow = false;

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    requests.add(options.path);
    return ResponseBody.fromString(
      jsonEncode({'detail': _alphaMessage}),
      refreshFlow && options.path == '/users/me' ? 401 : 403,
      headers: {
        Headers.contentTypeHeader: [Headers.jsonContentType],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

Future<DioException> _failure(Future<Object?> request) async {
  try {
    await request;
  } on DioException catch (error) {
    return error;
  }
  throw StateError('The denied request must fail');
}

void main() {
  for (final path in [
    '/auth/register',
    '/auth/login',
    '/auth/refresh',
    '/auth/session',
    '/users/me',
    '/feed',
    '/catalog/discovery',
    '/onboarding/photo',
    '/style-profile/analyze',
  ]) {
    test('Closed alpha denial is clear on $path', () {
      expect(
        ApiClient.formatError(_error(path, {'detail': _alphaMessage})),
        _alphaMessage,
      );
    });
  }

  test(
    'Unknown forbidden details never expose text or expand the allowlist',
    () {
      const diagnostic =
          'private fixture email=user@example.test SQL traceback';
      for (final data in <dynamic>[
        {'detail': diagnostic},
        {'detail': '$_alphaMessage $diagnostic'},
        {'detail': '$diagnostic $_alphaMessage'},
        {'detail': ' $_alphaMessage '},
        {
          'detail': [
            {'msg': _alphaMessage, 'input': diagnostic},
          ],
        },
        {
          'detail': {'message': _alphaMessage, 'private': diagnostic},
        },
        {'error': _alphaMessage, 'detail': null},
        _alphaMessage,
        '<html>$diagnostic</html>',
      ]) {
        final message = ApiClient.formatError(_error('/auth/register', data));
        expect(message, _genericForbidden);
        expect(message, isNot(contains('example.test')));
        expect(message, isNot(contains('SQL')));
        expect(message, isNot(contains('traceback')));
      }
    },
  );

  for (final path in ['/onboarding/photo', '/style-profile/analyze']) {
    test(
      'Other photo 403 failures preserve their existing guidance on $path',
      () {
        for (final detail in ['Photo limit reached', 'Trial expired', null]) {
          expect(
            ApiClient.formatError(_error(path, {'detail': detail})),
            _photoForbidden,
          );
        }
      },
    );
  }

  test('Only HTTP 403 uses the exact alpha response contract', () {
    expect(
      ApiClient.formatError(
        _error('/auth/login', {'detail': _alphaMessage}, status: 401),
      ),
      'Проверьте email и пароль и попробуйте снова.',
    );
    expect(
      ApiClient.formatError(
        _error('/feed', {'detail': _alphaMessage}, status: 503),
      ),
      'Сервис временно недоступен. Попробуйте ещё раз немного позже.',
    );
  });

  test(
    'Auth transport preserves an alpha rejection without accepting a token',
    () async {
      final adapter = _AlphaAdapter();
      final api = ApiClient(
        dio: Dio(BaseOptions(baseUrl: 'https://fixture.example.test'))
          ..httpClientAdapter = adapter,
      );
      addTearDown(api.dispose);
      var accepted = 0;
      api.onSessionChanged = (_, _) async => accepted++;
      final epoch = api.sessionEpoch;
      final registration = await _failure(
        api.register('fixture@example.test', 'SYNTHETIC_PASSWORD_1'),
      );
      final login = await _failure(
        api.login('fixture@example.test', 'SYNTHETIC_PASSWORD_1'),
      );
      expect(ApiClient.formatError(registration), _alphaMessage);
      expect(ApiClient.formatError(login), _alphaMessage);
      expect(adapter.requests, ['/auth/register', '/auth/login']);
      expect(accepted, 0);
      expect(api.sessionEpoch, epoch);
    },
  );

  test(
    'Refresh denial retains alpha copy instead of reporting an expired session',
    () async {
      final adapter = _AlphaAdapter()..refreshFlow = true;
      final api =
          ApiClient(
              dio: Dio(BaseOptions(baseUrl: 'https://fixture.example.test'))
                ..httpClientAdapter = adapter,
            )
            ..applyToken('SYNTHETIC_ACCESS_FIXTURE')
            ..refreshToken = 'SYNTHETIC_REFRESH_FIXTURE_FOR_LOCAL_TEST';
      addTearDown(api.dispose);
      var unauthorized = 0;
      api.onUnauthorized = () => unauthorized++;
      final error = await _failure(api.usersMe());
      expect(adapter.requests, ['/users/me', '/auth/refresh']);
      expect(error.response?.statusCode, 403);
      expect(ApiClient.formatError(error), _alphaMessage);
      expect(unauthorized, 0);
    },
  );
}
