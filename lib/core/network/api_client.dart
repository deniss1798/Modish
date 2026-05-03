import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:http_parser/http_parser.dart';
import '../../features/recommendations/models.dart';

/// Базовый URL API.
///
/// На **Android-эмуляторе** `127.0.0.1` — это сам эмулятор, не ваш ПК → по умолчанию
/// используется `http://10.0.2.2:8000` (алиас к localhost хоста).
///
/// Переопределение: `--dart-define=MODISH_API_BASE_URL=http://192.168.x.x:8000`
class ApiClient {
  ApiClient()
    : _dio = Dio(
        BaseOptions(
          baseUrl: _resolvedBaseUrl(),
          connectTimeout: const Duration(seconds: 12),
          receiveTimeout: const Duration(seconds: 20),
          headers: {'Accept': 'application/json'},
        ),
      );

  static String _resolvedBaseUrl() {
    const fromEnv = String.fromEnvironment(
      'MODISH_API_BASE_URL',
      defaultValue: '',
    );
    if (fromEnv.trim().isNotEmpty) {
      return fromEnv.trim();
    }
    if (kIsWeb) {
      return 'http://127.0.0.1:8000';
    }
    if (defaultTargetPlatform == TargetPlatform.android) {
      return 'http://10.0.2.2:8000';
    }
    return 'http://127.0.0.1:8000';
  }

  final Dio _dio;

  void applyToken(String token) {
    _dio.options.headers['Authorization'] = 'Bearer $token';
  }

  void clearToken() {
    _dio.options.headers.remove('Authorization');
  }

  Future<String> register(String email, String password) async {
    final response = await _dio.post(
      '/auth/register',
      data: {'email': email, 'password': password},
    );
    final token = '${response.data['access_token']}';
    applyToken(token);
    return token;
  }

  Future<String> login(String email, String password) async {
    final response = await _dio.post(
      '/auth/login',
      data: {'email': email, 'password': password},
    );
    final token = '${response.data['access_token']}';
    applyToken(token);
    return token;
  }

  Future<Map<String, dynamic>> usersMe() async {
    final response = await _dio.get('/users/me');
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<Map<String, dynamic>> billingStatus() async {
    final response = await _dio.get('/billing/status');
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<Map<String, dynamic>> styleProfileMe() async {
    final response = await _dio.get('/style-profile/me');
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<void> setStyleTarget(String target) async {
    await _dio.patch('/style-profile/target', data: {'style_target': target});
  }

  Future<void> analyzeStyleProfile(String photoPath) async {
    final lower = photoPath.toLowerCase();
    final subtype = lower.endsWith('.png')
        ? 'png'
        : lower.endsWith('.webp')
        ? 'webp'
        : 'jpeg';
    final form = FormData.fromMap({
      'photo': await MultipartFile.fromFile(
        photoPath,
        filename: photoPath.split(RegExp(r'[/\\]')).last,
        contentType: MediaType('image', subtype),
      ),
    });
    await _dio.post('/style-profile/analyze', data: form);
  }

  Future<List<Outfit>> feed() async {
    final response = await _dio.get('/recommendations/feed');
    final list = (response.data as List).cast<Map<String, dynamic>>();
    return list.map(Outfit.fromApi).toList();
  }

  Future<List<Outfit>> generateOutfits({
    String type = 'outfit',
    int count = 10,
    String scenario = 'daily',
  }) async {
    final response = await _dio.post(
      '/recommendations/generate',
      data: {'type': type, 'count': count, 'scenario': scenario},
    );
    final list = (response.data as List).cast<Map<String, dynamic>>();
    return list.map(Outfit.fromApi).toList();
  }

  Future<void> feedback(String recommendationId, String eventType) async {
    await _dio.post(
      '/recommendations/$recommendationId/feedback',
      data: {'event_type': eventType},
    );
  }

  Future<Map<String, dynamic>> summary() async {
    final response = await _dio.get('/recommendations/summary');
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<void> summaryRebuild() async {
    await _dio.post('/recommendations/summary/rebuild');
  }

  Future<List<Map<String, dynamic>>> savedRecommendations() async {
    final response = await _dio.get('/recommendations/saved');
    final list = (response.data as List).cast<Map<String, dynamic>>();
    return list;
  }

  Future<Map<String, dynamic>> recommendationById(String id) async {
    final response = await _dio.get('/recommendations/$id');
    return Map<String, dynamic>.from(response.data as Map);
  }

  /// Сообщение об ошибке для UI.
  static String formatError(Object e) {
    if (e is DioException) {
      if (e.type == DioExceptionType.connectionError ||
          e.type == DioExceptionType.connectionTimeout) {
        return 'Нет связи с сервером. Запустите backend (uvicorn) и на эмуляторе '
            'используйте адрес 10.0.2.2 вместо 127.0.0.1 (в приложении это уже учтено).';
      }
      final data = e.response?.data;
      if (data is Map && data['detail'] != null) {
        final d = data['detail'];
        if (d is String) return d;
        if (d is List && d.isNotEmpty && d.first is Map) {
          return '${(d.first as Map)['msg'] ?? d}';
        }
      }
      return e.message ?? e.toString();
    }
    return e.toString();
  }
}
