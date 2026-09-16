import 'dart:convert';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

const _kToken = 'modish_access_token';

/// Безопасное хранение JWT. В unit-тестах без плагина операции безопасно игнорируются.
class TokenStorage {
  static const _storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );

  static Future<String?> read() async {
    try {
      final raw = await _storage.read(key: _kToken);
      if (raw == null || !raw.startsWith('{')) return raw;
      return jsonDecode(raw)['access_token'] as String?;
    } catch (_) {
      return null;
    }
  }

  /// `true`, если токен записан; `false` при ошибке хранилища.
  static Future<String?> readRefresh() async {
    try {
      final raw = await _storage.read(key: _kToken);
      if (raw == null || !raw.startsWith('{')) return null;
      return jsonDecode(raw)['refresh_token'] as String?;
    } catch (_) {
      return null;
    }
  }

  static Future<bool> write(String token, {String? refreshToken}) async {
    try {
      await _storage.write(
        key: _kToken,
        value: jsonEncode({
          'access_token': token,
          'refresh_token': refreshToken,
        }),
      );
      return true;
    } catch (_) {
      return false;
    }
  }

  static Future<void> clear() async {
    try {
      await _storage.delete(key: _kToken);
    } catch (_) {}
  }
}
