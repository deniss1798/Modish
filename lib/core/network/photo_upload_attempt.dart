import 'dart:math';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:http_parser/http_parser.dart';

/// One immutable, memory-only photo submission. ApiClient owns its lifetime.
class PhotoUploadAttempt {
  PhotoUploadAttempt._(this._path, this._contentType, this._bytes)
    : key = _newKey(),
      _form = FormData.fromMap({
        'photo': MultipartFile.fromBytes(
          _bytes,
          filename: _path.split(RegExp(r'[/\\]')).last,
          contentType: _contentType,
        ),
      });

  final String _path;
  final MediaType _contentType;
  final Uint8List _bytes;
  final String key;
  final FormData _form;

  bool matches(PhotoUploadAttempt other) =>
      _path == other._path &&
      _contentType.toString() == other._contentType.toString() &&
      listEquals(_bytes, other._bytes);

  // Dio's clone retains both the multipart boundary and the captured bytes.
  FormData newForm() => _form.clone();

  static String _newKey() {
    final random = Random.secure();
    final key = List.generate(
      16,
      (_) => random.nextInt(256),
    ).map((byte) => byte.toRadixString(16).padLeft(2, '0')).join();
    return 'photo-$key';
  }

  static Future<PhotoUploadAttempt> capture(
    String path, {
    required RequestOptions request,
    required void Function() requireAccount,
  }) async {
    requireAccount();
    final lower = path.toLowerCase();
    final subtype = lower.endsWith('.png')
        ? 'png'
        : lower.endsWith('.webp')
        ? 'webp'
        : 'jpeg';
    final file = await MultipartFile.fromFile(path);
    requireAccount();
    const maxPhotoBytes = 5 * 1024 * 1024;
    void requireSize(int size) {
      if (size <= maxPhotoBytes) return;
      throw DioException(
        requestOptions: request,
        type: DioExceptionType.badResponse,
        response: Response(requestOptions: request, statusCode: 413),
      );
    }

    // fromFile obtains the length before contents enter memory. The stream
    // limit also covers a file that grows after its length was obtained.
    requireSize(file.length);
    final buffer = BytesBuilder(copy: false);
    await for (final chunk in file.finalize()) {
      requireAccount();
      requireSize(buffer.length + chunk.length);
      buffer.add(chunk);
    }
    requireAccount();
    return PhotoUploadAttempt._(
      path,
      MediaType('image', subtype),
      buffer.takeBytes().asUnmodifiableView(),
    );
  }

  static bool isTerminalFailure(DioException error) {
    final data = error.response?.data;
    if (data is! Map) return false;
    final detail = data['detail'];
    return switch (error.response?.statusCode) {
      502 => detail == 'Не удалось выполнить анализ фото. Попробуйте ещё раз.',
      504 =>
        detail == 'Анализ занял слишком много времени. Попробуйте ещё раз.',
      409 =>
        detail == 'Анализ завершён без результата. Загрузите фото повторно.',
      _ => false,
    };
  }
}
