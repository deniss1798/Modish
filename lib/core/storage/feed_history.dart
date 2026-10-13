import 'dart:convert';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Device history is account-scoped. Server feedback remains the source of truth.
class FeedHistory {
  static const _storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );
  static const retention = Duration(days: 30);
  static String _key(String account) =>
      'modish_feed_history_v1_${base64Url.encode(utf8.encode(account.toLowerCase().trim()))}';

  Future<List<Map<String, dynamic>>> read(String account) async {
    if (account.isEmpty) return [];
    try {
      final raw = await _storage.read(key: _key(account));
      if (raw == null) return [];
      final cutoff = DateTime.now().subtract(retention).millisecondsSinceEpoch;
      return (jsonDecode(raw) as List)
          .whereType<Map>()
          .map((row) => Map<String, dynamic>.from(row))
          .where((row) => (row['at'] as num? ?? 0) > cutoff)
          .toList();
    } catch (_) {
      return [];
    }
  }

  Future<void> write(String account, List<Map<String, dynamic>> rows) async {
    if (account.isEmpty) return;
    try {
      await _storage.write(key: _key(account), value: jsonEncode(rows));
    } catch (_) {
      // A storage failure must not interrupt a swipe or erase server history.
    }
  }
}
