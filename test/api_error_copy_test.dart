import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/core/network/api_client.dart';

void main() {
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
                'SQL private connection postgres://user:password@host/database',
          },
        ),
      );
      expect(ApiClient.formatError(serverError), isNot(contains('SQL')));
      expect(ApiClient.formatError(serverError), isNot(contains('password')));
      final validation = DioException(
        requestOptions: request,
        response: Response(
          requestOptions: request,
          statusCode: 422,
          data: {'detail': 'Подтвердите размер обуви'},
        ),
      );
      expect(ApiClient.formatError(validation), 'Подтвердите размер обуви');
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
      expect(ApiClient.formatError(error), 'Это действие сейчас недоступно.');
    },
  );
}
