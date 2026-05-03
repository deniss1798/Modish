import 'package:flutter_secure_storage/flutter_secure_storage.dart';

const _kToken = 'modish_access_token';

/// Безопасное хранение JWT. В unit-тестах без плагина операции безопасно игнорируются.
class TokenStorage {
  static const _storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );

  static Future<String?> read() async {
    try {
      return await _storage.read(key: _kToken);
    } catch (_) {
      return null;
    }
  }

  static Future<void> write(String token) async {
    try {
      await _storage.write(key: _kToken, value: token);
    } catch (_) {}
  }

  static Future<void> clear() async {
    try {
      await _storage.delete(key: _kToken);
    } catch (_) {}
  }
}
