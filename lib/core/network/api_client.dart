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
          baseUrl: resolvedBaseUrl(),
          connectTimeout: const Duration(seconds: 12),
          receiveTimeout: const Duration(seconds: 20),
          headers: {'Accept': 'application/json'},
        ),
      );

  /// Базовый URL API (без завершающего `/`).
  static String resolvedBaseUrl() {
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

  /// Загрузка картинок через `GET /media/proxy-image` (эмулятор без исходящего HTTPS).
  /// `--dart-define=MODISH_IMAGE_PROXY=true|false` переопределяет авто-режим.
  static bool useImageProxyForProductImages() {
    const override = String.fromEnvironment('MODISH_IMAGE_PROXY', defaultValue: '');
    final v = override.trim().toLowerCase();
    if (v == '1' || v == 'true' || v == 'yes') return true;
    if (v == '0' || v == 'false' || v == 'no') return false;
    return resolvedBaseUrl().contains('10.0.2.2');
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

  Future<List<Map<String, dynamic>>> productFeed({int limit = 30, String? source}) async {
    final qp = <String, dynamic>{'limit': limit};
    if (source != null && source.trim().isNotEmpty) {
      qp['source'] = source.trim();
    }
    final response = await _dio.get('/feed', queryParameters: qp);
    final list = (response.data as List).cast<Map<String, dynamic>>();
    return list;
  }

  Future<Map<String, dynamic>> recordRecommendationEvent({
    required String eventType,
    String? productId,
    String? outfitId,
    Map<String, dynamic>? meta,
  }) async {
    final response = await _dio.post(
      '/recommendations/events',
      data: {
        'event_type': eventType,
        'product_id': productId,
        'outfit_id': outfitId,
        'meta': meta ?? {},
      },
    );
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<Map<String, dynamic>> productById(String id) async {
    final response = await _dio.get('/products/$id');
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<List<Map<String, dynamic>>> productsList({
    int limit = 50,
    int offset = 0,
    String? category,
    String? source,
    String? brand,
  }) async {
    final qp = <String, dynamic>{
      'limit': limit,
      'offset': offset,
    };
    if (category != null && category.trim().isNotEmpty) qp['category'] = category.trim();
    if (source != null && source.trim().isNotEmpty) qp['source'] = source.trim();
    if (brand != null && brand.trim().isNotEmpty) qp['brand'] = brand.trim();
    final response = await _dio.get('/products', queryParameters: qp);
    final list = (response.data as List).cast<Map<String, dynamic>>();
    return list;
  }

  Future<void> metricsEvent(String name, {Map<String, dynamic>? meta}) async {
    await _dio.post('/metrics/events', data: {'name': name, 'meta': meta ?? {}});
  }

  Future<Map<String, dynamic>> profileBrief() async {
    final response = await _dio.get('/profile/brief');
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<List<Map<String, dynamic>>> savedProducts() async {
    final response = await _dio.get('/saved-products');
    final list = (response.data as List).cast<Map<String, dynamic>>();
    return list;
  }

  Future<Map<String, dynamic>> fitProfileMe() async {
    final response = await _dio.get('/fit-profile/me');
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<Map<String, dynamic>> fitProfilePatch({
    required int height,
    int? weight,
    required String genderTarget,
    required String clothingSize,
    required int budgetMin,
    required int budgetMax,
    List<String> interestCategories = const [],
    List<String> styleScenarios = const [],
  }) async {
    final response = await _dio.patch(
      '/fit-profile/me',
      data: {
        'height': height,
        'weight': weight,
        'gender_target': genderTarget,
        'clothing_size': clothingSize,
        'budget_min': budgetMin,
        'budget_max': budgetMax,
        'interest_categories': interestCategories,
        'style_scenarios': styleScenarios,
      },
    );
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<Map<String, dynamic>> tasteProfileMe() async {
    final response = await _dio.get('/taste-profile/me');
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<Map<String, dynamic>> tasteProfilePatch({
    required int priceMin,
    required int priceMax,
    required String preferredFit,
  }) async {
    final response = await _dio.patch(
      '/taste-profile/me',
      data: {
        'price_min': priceMin,
        'price_max': priceMax,
        'preferred_fit': preferredFit,
      },
    );
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<List<Map<String, dynamic>>> outfitsList({
    String? scenario,
    bool savedOnly = false,
  }) async {
    final qp = <String, dynamic>{};
    if (scenario != null && scenario.trim().isNotEmpty) {
      qp['scenario'] = scenario.trim();
    }
    if (savedOnly) qp['saved_only'] = true;
    final response = await _dio.get('/outfits', queryParameters: qp);
    final list = (response.data as List).cast<Map<String, dynamic>>();
    return list;
  }

  Future<List<Map<String, dynamic>>> outfitsGenerate({
    int count = 3,
    String scenario = 'daily',
  }) async {
    final response = await _dio.post(
      '/outfits/generate',
      queryParameters: {'count': count, 'scenario': scenario},
    );
    final list = (response.data as List).cast<Map<String, dynamic>>();
    return list;
  }

  Future<void> outfitsSave(String outfitId) async {
    await _dio.post('/outfits/save', queryParameters: {'outfit_id': outfitId});
  }

  Future<void> outfitsUnsave(String outfitId) async {
    await _dio.post('/outfits/unsave', queryParameters: {'outfit_id': outfitId});
  }

  Future<Map<String, dynamic>> visualAnalysis() async {
    final response = await _dio.post('/visual-analysis');
    return Map<String, dynamic>.from(response.data as Map);
  }

  /// Affiliate: зарегистрировать клик и получить финальный URL магазина.
  Future<Map<String, dynamic>> affiliateClick(String productId) async {
    final response = await _dio.post(
      '/affiliate/click/$productId',
      options: Options(
        sendTimeout: const Duration(seconds: 15),
        receiveTimeout: const Duration(seconds: 30),
      ),
    );
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
        if (d is String) {
          if (d.contains('недоступен') || d.contains('недоступ')) {
            return 'Товар временно недоступен';
          }
          return d;
        }
        if (d is List && d.isNotEmpty && d.first is Map) {
          return '${(d.first as Map)['msg'] ?? d}';
        }
      }
      return e.message ?? e.toString();
    }
    return e.toString();
  }
}
