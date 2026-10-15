import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/core/network/api_client.dart';
import 'package:modish/core/network/user_error_message.dart';

class _UnprintableFailure implements Exception {
  @override
  String toString() => throw StateError('Diagnostics must not be inspected');
}

DioException _response(int status, dynamic data, {String path = '/feed'}) {
  final request = RequestOptions(path: path);
  return DioException(
    type: DioExceptionType.badResponse,
    requestOptions: request,
    response: Response(requestOptions: request, statusCode: status, data: data),
    message: 'private-message password=secret-test-value',
    error: StateError('private-inner diagnostic'),
  );
}

void main() {
  test('Missing gender directs the person to an explicit profile choice', () {
    for (final detail in [
      'Явно выберите гардероб.',
      'Явно выберите гардероб в профиле.',
      'Явно выберите женский гардероб в настройках.',
      'Новый подбор пока доступен для женского гардероба.',
    ]) {
      final message = ApiClient.formatError(_response(409, {'detail': detail}));
      expect(
        message,
        anyOf(contains('параметрах подбора'), contains('профиле')),
      );
      expect(message, isNot(contains('Данные уже изменились')));
    }
  });

  test(
    'Connection errors do not expose deployment instructions in product UI',
    () {
      for (final type in [
        DioExceptionType.connectionError,
        DioExceptionType.connectionTimeout,
        DioExceptionType.receiveTimeout,
        DioExceptionType.sendTimeout,
      ]) {
        final message = ApiClient.formatError(
          DioException(
            requestOptions: RequestOptions(path: '/products/private-id'),
            type: type,
          ),
        );
        expect(message, contains('Проверьте интернет'));
        expect(message, isNot(contains('backend')));
        expect(message, isNot(contains('http')));
        expect(message, isNot(contains('эмулятор')));
      }
    },
  );

  test(
    'Server diagnostics are hidden while business validation remains useful',
    () {
      final request = RequestOptions(path: '/feed');
      final serverError = DioException(
        requestOptions: request,
        response: Response(
          requestOptions: request,
          statusCode: 500,
          data: {
            'detail':
                'SQL private connection postgres://REPLACE_ME:REPLACE_ME@host/database',
          },
        ),
      );
      expect(ApiClient.formatError(serverError), isNot(contains('SQL')));
      expect(ApiClient.formatError(serverError), isNot(contains('password')));
      expect(ApiClient.formatError(serverError), isNot(contains('REPLACE_ME')));
      final validation = DioException(
        requestOptions: request,
        response: Response(
          requestOptions: request,
          statusCode: 422,
          data: {'detail': 'Подтвердите размер обуви'},
        ),
      );
      expect(
        ApiClient.formatError(validation),
        'Подтвердите размер обуви в профиле и повторите попытку.',
      );
    },
  );

  test(
    'Forbidden action does not falsely tell the user their session expired',
    () {
      final request = RequestOptions(path: '/shopping-intents');
      final error = DioException(
        requestOptions: request,
        response: Response(
          requestOptions: request,
          statusCode: 403,
          data: {'detail': 'Forbidden'},
        ),
      );
      expect(
        ApiClient.formatError(error),
        'Это действие сейчас недоступно. Вернитесь на предыдущий экран или повторите позже.',
      );
    },
  );

  test('Arbitrary server content never becomes visible UI text', () {
    const diagnostic =
        'Ошибка! SQL UniqueViolation product_id=private-id '
        'postgres://private:secret-test-value@host/db Traceback';
    for (final status in [400, 401, 403, 404, 409, 410, 413, 422, 429, 500]) {
      for (final payload in [
        diagnostic,
        {'detail': diagnostic},
        {
          'detail': [
            {
              'loc': ['body', 'private-id'],
              'msg': diagnostic,
              'input': 'secret-test-value',
            },
          ],
        },
        '<html>private-id secret-test-value</html>',
      ]) {
        final message = ApiClient.formatError(_response(status, payload));
        expect(message, matches(RegExp('[А-Яа-яЁё]')));
        for (final privateText in [
          'SQL',
          'Traceback',
          'postgres',
          'private-id',
          'secret-test-value',
          'private-message',
          'private-inner',
          '<html>',
          'loc',
        ]) {
          expect(message, isNot(contains(privateText)), reason: '$status');
        }
      }
    }
  });

  test('Only exact allowlisted business responses are used', () {
    const safe = 'Подтвердите размер обуви в профиле и повторите попытку.';
    for (final payload in [
      {'detail': 'Подтвердите размер обуви'},
      {
        'detail': [
          {'msg': 'Подтвердите размер обуви'},
        ],
      },
    ]) {
      expect(ApiClient.formatError(_response(422, payload)), safe);
    }
    for (final payload in [
      {'detail': 'Подтвердите размер обуви SQL password=secret-test-value'},
      {
        'detail': [
          {'msg': 'Подтвердите размер обуви'},
          {'msg': 'private second validation error'},
        ],
      },
    ]) {
      expect(ApiClient.formatError(_response(422, payload)), isNot(safe));
    }
    // A server failure cannot masquerade as business validation.
    expect(
      ApiClient.formatError(
        _response(500, {'detail': 'Подтвердите размер обуви'}),
      ),
      isNot(safe),
    );
  });

  test(
    'Authentication, quota and duplicate email offer relevant next steps',
    () {
      expect(
        ApiClient.formatError(_response(401, null, path: '/auth/login')),
        'Проверьте email и пароль и попробуйте снова.',
      );
      expect(
        ApiClient.formatError(_response(401, null)),
        contains('Войдите снова'),
      );
      expect(
        ApiClient.formatError(
          _response(409, {
            'detail': 'Email уже зарегистрирован',
          }, path: '/auth/register'),
        ),
        contains('Войдите в аккаунт или укажите другой email'),
      );
      for (final path in ['/onboarding/photo', '/style-profile/analyze']) {
        final message = ApiClient.formatError(
          _response(403, {
            'detail': 'Plus недоступен: оформите подписку',
          }, path: path),
        );
        expect(message, contains('Продолжите подбор без фото'));
        expect(message, isNot(contains('Plus')));
        expect(message, isNot(contains('подписку')));
      }
    },
  );

  test('Unknown, timeout, certificate and local errors hide diagnostics', () {
    for (final error in [
      _UnprintableFailure(),
      StateError('SQL private-id'),
      TimeoutException('usersMe private-id'),
      DioException(
        requestOptions: RequestOptions(path: '/feed'),
        message: 'Exception private-id secret-test-value',
      ),
    ]) {
      final message = ApiClient.formatError(error);
      expect(message, matches(RegExp('[А-Яа-яЁё]')));
      expect(message, isNot(contains('private-id')));
      expect(message, isNot(contains('SQL')));
      expect(message, isNot(contains('secret-test-value')));
      expect(message, isNot(contains('usersMe')));
    }
    expect(
      ApiClient.formatError(
        DioException(
          requestOptions: RequestOptions(path: '/feed'),
          type: DioExceptionType.badCertificate,
        ),
      ),
      contains('Повторите попытку позже'),
    );
    expect(
      ApiClient.formatError(
        const AppFailure(AppFailureKind.serviceUnavailable),
      ),
      contains('Попробуйте ещё раз'),
    );
    expect(
      ApiClient.formatError(const AppFailure(AppFailureKind.sessionStorage)),
      contains('Повторите вход'),
    );
  });

  test(
    'Intent failures preserve the next step without inventing input fields',
    () {
      expect(
        ApiClient.formatError(
          _response(400, {
            'detail':
                'Подходящей замены пока нет. Остальные вещи и бюджет сохранены.',
          }, path: '/shopping-intents/intent/replace'),
        ),
        'Подходящей замены пока нет. Остальные вещи и бюджет сохранены. Попробуйте изменить пожелания.',
      );
      expect(
        ApiClient.formatError(
          _response(400, {
            'detail': 'Цена или наличие изменились. Обновите подборку.',
          }, path: '/shopping-intents/intent/replace'),
        ),
        'Цена или наличие изменились. Обновите подборку.',
      );
      expect(
        ApiClient.formatError(
          _response(410, {
            'detail': 'private-id expiration diagnostic',
          }, path: '/shopping-intents/intent'),
        ),
        'Этот запрос завершён или недоступен. Создайте новый запрос.',
      );
    },
  );
}
