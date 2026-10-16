import 'dart:async';
import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';

import '../config/api_config.dart';
import 'photo_upload_attempt.dart';
import 'user_error_message.dart';
import '../../features/recommendations/models.dart';

/// Базовый URL API.
///
/// Production (HTTPS):
/// `flutter build apk --release --dart-define=MODISH_API_BASE_URL=https://api.YOUR_DOMAIN.com`
///
/// Локально / эмулятор: по умолчанию `10.0.2.2:8000` (Android) или `127.0.0.1:8000`.
class ApiClient {
  ApiClient({this.onUnauthorized, Dio? dio})
    : _dio =
          dio ??
          Dio(
            BaseOptions(
              baseUrl: resolvedBaseUrl(),
              connectTimeout: const Duration(seconds: 45),
              sendTimeout: const Duration(seconds: 45),
              receiveTimeout: const Duration(seconds: 45),
              headers: const {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
              },
            ),
          ) {
    _stampSessionOptions();
    _dio.interceptors.add(
      InterceptorsWrapper(
        onRequest: (options, handler) {
          options.extra.putIfAbsent('sessionVersion', () => _sessionVersion);
          if (options.extra['sessionVersion'] != _sessionVersion) {
            handler.reject(_staleSession(options));
            return;
          }
          if (options.extra['publicAuth'] == true) {
            options.headers.remove('Authorization');
          }
          handler.next(options);
        },
        onResponse: (response, handler) {
          final request = response.requestOptions;
          if (request.extra['publicAuth'] != true &&
              request.extra['sessionVersion'] != _sessionVersion) {
            handler.reject(_staleSession(request));
            return;
          }
          handler.next(response);
        },
        onError: (error, handler) async {
          final request = error.requestOptions;
          if (request.extra['publicAuth'] != true &&
              request.extra['sessionVersion'] != _sessionVersion) {
            handler.next(error);
            return;
          }
          if (error.response?.statusCode == 401 &&
              request.extra['publicAuth'] != true &&
              request.extra['authRetried'] != true) {
            final sessionVersion = _sessionVersion;
            try {
              final sentToken = request.headers['Authorization'];
              final currentToken = _dio.options.headers['Authorization'];
              if (sentToken == currentToken || currentToken == null) {
                await _refreshAccess();
              }
              if (sessionVersion != _sessionVersion) {
                handler.next(error);
                return;
              }
              request.extra['authRetried'] = true;
              request.headers['Authorization'] =
                  _dio.options.headers['Authorization'];
              if (request.data is FormData) {
                // Multipart streams are consumed by the first dispatch. Clone
                // them while retaining this upload's idempotency key.
                request.data = (request.data as FormData).clone();
              }
              handler.resolve(await _dio.fetch(request));
              return;
            } on DioException catch (refreshError) {
              if (sessionVersion == _sessionVersion &&
                  refreshError.response?.statusCode == 401) {
                clearToken();
                onUnauthorized?.call();
              }
              handler.next(refreshError);
              return;
            } catch (refreshError, stackTrace) {
              // A malformed refresh response/storage callback must finish the
              // original request, rather than leave its interceptor pending.
              handler.next(
                DioException(
                  requestOptions: request,
                  error: refreshError,
                  stackTrace: stackTrace,
                  message: 'Не удалось обновить сессию. Повторите запрос.',
                ),
              );
              return;
            }
          }
          if (_shouldRetry(error) &&
              error.requestOptions.extra['retried'] != true) {
            error.requestOptions.extra['retried'] = true;
            await Future<void>.delayed(const Duration(milliseconds: 800));
            if (request.extra['publicAuth'] != true &&
                request.extra['sessionVersion'] != _sessionVersion) {
              handler.next(_staleSession(request));
              return;
            }
            try {
              final response = await _dio.fetch(error.requestOptions);
              handler.resolve(response);
              return;
            } catch (_) {}
          }
          handler.next(error);
        },
      ),
    );
    if (kDebugMode) {
      debugPrint('Modish API baseUrl: ${_dio.options.baseUrl}');
    }
  }

  final Dio _dio;

  /// Вызывается при 401 (кроме login/register) — сброс сессии в приложении.
  VoidCallback? onUnauthorized;
  Future<void> Function(String access, String? refresh)? onSessionChanged;
  String? refreshToken;
  Future<void>? _refreshing;
  int _sessionVersion = 0;
  bool _disposed = false;
  final _pendingPhotos = <String, PhotoUploadAttempt>{};

  /// Account identity for work which awaits something outside this transport.
  /// Access-token rotation keeps the value; login/logout/disposal advance it.
  int get sessionEpoch => _sessionVersion;

  void _stampSessionOptions() {
    // Dio copies BaseOptions.extra when the call constructs RequestOptions,
    // before its async interceptor runs. Stamping only in onRequest is late.
    _dio.options.extra = {
      ..._dio.options.extra,
      'sessionVersion': _sessionVersion,
    };
  }

  void _advanceSession() {
    _sessionVersion++;
    _refreshing = null;
    _pendingPhotos.clear();
    _stampSessionOptions();
  }

  DioException _staleSession(RequestOptions request) => DioException(
    requestOptions: request,
    type: DioExceptionType.cancel,
    message: 'Сессия изменилась. Предыдущий запрос отменён.',
  );

  void _requireSession(int version, RequestOptions request) {
    if (version != _sessionVersion) throw _staleSession(request);
  }

  Future<void> _acceptSession(dynamic data) async {
    final access = data['access_token'] as String;
    refreshToken = data['refresh_token'] as String?;
    applyToken(access);
    await onSessionChanged?.call(access, refreshToken);
  }

  Future<void> _refreshAccess() {
    final pending = _refreshing;
    if (pending != null) return pending;
    late final Future<void> refresh;
    refresh = _doRefresh().whenComplete(() {
      if (identical(_refreshing, refresh)) _refreshing = null;
    });
    return _refreshing = refresh;
  }

  Future<void> _doRefresh() async {
    final refresh = refreshToken;
    if (refresh == null) {
      throw DioException(
        requestOptions: RequestOptions(path: '/auth/refresh'),
        response: Response(
          requestOptions: RequestOptions(path: '/auth/refresh'),
          statusCode: 401,
        ),
      );
    }
    final version = _sessionVersion;
    final response = await _dio.post(
      '/auth/refresh',
      data: {'refresh_token': refresh},
      options: Options(extra: {'publicAuth': true}),
    );
    if (version == _sessionVersion) await _acceptSession(response.data);
  }

  Future<void> upgradeSession() async {
    if (refreshToken != null) return;
    final version = _sessionVersion;
    final response = await _dio.post('/auth/session');
    _requireSession(version, response.requestOptions);
    await _acceptSession(response.data);
  }

  Future<void> logoutSession() async {
    final refresh = refreshToken;
    clearToken();
    if (refresh == null) return;
    try {
      await _dio.post(
        '/auth/logout',
        data: {'refresh_token': refresh},
        options: Options(
          extra: {'publicAuth': true},
          receiveTimeout: const Duration(seconds: 5),
        ),
      );
    } catch (_) {}
  }

  static bool _shouldRetry(DioException e) {
    if (!{'GET', 'HEAD'}.contains(e.requestOptions.method.toUpperCase())) {
      return false;
    }
    if (e.type == DioExceptionType.connectionError ||
        e.type == DioExceptionType.connectionTimeout ||
        e.type == DioExceptionType.receiveTimeout) {
      return true;
    }
    final inner = '${e.error ?? ''}';
    return inner.contains('Connection closed') ||
        inner.contains('Connection reset');
  }

  /// Базовый URL API без завершающего `/`.
  static String resolvedBaseUrl() {
    const fromEnv = String.fromEnvironment(
      'MODISH_API_BASE_URL',
      defaultValue: '',
    );

    if (fromEnv.trim().isNotEmpty) {
      return _normalizeBaseUrl(fromEnv);
    }

    if (kReleaseMode && productionApiBaseUrl.trim().isNotEmpty) {
      return _normalizeBaseUrl(productionApiBaseUrl);
    }

    // 10.0.2.2 — только Android-эмулятор на том же ПК, где крутится uvicorn.
    // На реальном телефоне без dart-define сюда не попасть — нужен LAN IP или HTTPS домен.
    if (kIsWeb) {
      return 'http://127.0.0.1:8000';
    }

    if (defaultTargetPlatform == TargetPlatform.android) {
      // На реальном телефоне в debug — VPS из api_config; эмулятор может переопределить dart-define.
      if (productionApiBaseUrl.trim().isNotEmpty) {
        return _normalizeBaseUrl(productionApiBaseUrl);
      }
      return 'http://10.0.2.2:8000';
    }

    return 'http://127.0.0.1:8000';
  }

  /// Подсказка, если на телефоне остался адрес эмулятора.
  static bool get isLikelyEmulatorOnlyUrl {
    final u = resolvedBaseUrl();
    return u.contains('10.0.2.2') || u.contains('127.0.0.1');
  }

  static String _normalizeBaseUrl(String raw) {
    var value = raw.trim();

    while (value.endsWith('/')) {
      value = value.substring(0, value.length - 1);
    }

    value = value.replaceAll('localhost', '127.0.0.1');

    return value;
  }

  /// Загрузка картинок через `GET /media/proxy-image`.
  ///
  /// `--dart-define=MODISH_IMAGE_PROXY=true|false` переопределяет авто-режим.
  static bool useImageProxyForProductImages() {
    const override = String.fromEnvironment(
      'MODISH_IMAGE_PROXY',
      defaultValue: '',
    );

    final value = override.trim().toLowerCase();

    if (value == '1' || value == 'true' || value == 'yes') {
      return true;
    }

    if (value == '0' || value == 'false' || value == 'no') {
      return false;
    }

    return resolvedBaseUrl().contains('10.0.2.2');
  }

  void applyToken(String token) {
    _dio.options.headers['Authorization'] = 'Bearer $token';
  }

  void clearToken() {
    _advanceSession();
    refreshToken = null;
    _dio.options.headers.remove('Authorization');
  }

  /// The client owns its transport; a controller must close only its own client.
  void dispose() {
    _disposed = true;
    clearToken();
    onUnauthorized = null;
    onSessionChanged = null;
    _dio.close(force: true);
  }

  Future<String> register(String email, String password) async {
    final version = _sessionVersion;
    final response = await _dio.post(
      '/auth/register',
      data: {'email': email.trim(), 'password': password},
      options: Options(
        extra: {'publicAuth': true},
        sendTimeout: const Duration(seconds: 60),
        receiveTimeout: const Duration(seconds: 60),
      ),
    );

    _requireSession(version, response.requestOptions);
    _advanceSession();
    final acceptedVersion = _sessionVersion;
    await _acceptSession(response.data);
    _requireSession(acceptedVersion, response.requestOptions);
    final token = '${response.data['access_token']}';
    return token;
  }

  Future<String> login(String email, String password) async {
    final version = _sessionVersion;
    final response = await _dio.post(
      '/auth/login',
      data: {'email': email.trim(), 'password': password},
      options: Options(
        extra: {'publicAuth': true},
        sendTimeout: const Duration(seconds: 60),
        receiveTimeout: const Duration(seconds: 60),
      ),
    );

    _requireSession(version, response.requestOptions);
    _advanceSession();
    final acceptedVersion = _sessionVersion;
    await _acceptSession(response.data);
    _requireSession(acceptedVersion, response.requestOptions);
    final token = '${response.data['access_token']}';
    return token;
  }

  Future<bool> pingHealth() async {
    try {
      final response = await _dio.get(
        '/health',
        options: Options(
          sendTimeout: const Duration(seconds: 10),
          receiveTimeout: const Duration(seconds: 10),
        ),
      );
      final data = response.data;
      if (data is Map && data['status'] == 'ok') return true;
      if (data is Map && data['status'] == 'degraded') return true;
      return false;
    } catch (_) {
      return false;
    }
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
    await _postPhoto('style_profile', '/style-profile/analyze', photoPath);
  }

  Options _photoRequestOptions(PhotoUploadAttempt attempt, int version) {
    return Options(
      headers: {'Idempotency-Key': attempt.key},
      // The provider has a 180-second server deadline. The usual short read
      // timeout would abandon a valid analysis after its quota was reserved.
      sendTimeout: const Duration(minutes: 2),
      receiveTimeout: const Duration(minutes: 4),
      extra: {'sessionVersion': version},
    );
  }

  void _clearPhotoAttempt(String kind, PhotoUploadAttempt attempt) {
    if (identical(_pendingPhotos[kind], attempt)) _pendingPhotos.remove(kind);
  }

  Future<Response<dynamic>> _postPhoto(
    String kind,
    String endpoint,
    String photoPath,
  ) async {
    // Capture the account before any file IO. Even a finished preparation must
    // not dispatch its photo after logout/login or disposal.
    final version = _sessionVersion;
    final request = RequestOptions(path: endpoint);
    void requireAccount() {
      if (_disposed) throw _staleSession(request);
      _requireSession(version, request);
    }

    final captured = await PhotoUploadAttempt.capture(
      photoPath,
      request: request,
      requireAccount: requireAccount,
    );
    requireAccount();
    var attempt = _pendingPhotos[kind];
    if (attempt == null || !attempt.matches(captured)) {
      attempt = captured;
      _pendingPhotos[kind] = attempt;
    }
    try {
      final response = await _dio.post<dynamic>(
        endpoint,
        data: attempt.newForm(),
        options: _photoRequestOptions(attempt, version),
      );
      // A proxy can return HTML/arrays with 200 after the server completed the
      // job. Keep its key until we receive the expected response container.
      if (response.data is! Map) {
        throw DioException(
          requestOptions: response.requestOptions,
          response: response,
          type: DioExceptionType.badResponse,
        );
      }
      _clearPhotoAttempt(kind, attempt);
      return response;
    } on DioException catch (error) {
      // Pending/ambiguous failures can already have spent quota or committed a
      // result. Only confirmed stopped/refunded jobs permit a fresh request.
      if (PhotoUploadAttempt.isTerminalFailure(error)) {
        _clearPhotoAttempt(kind, attempt);
      }
      rethrow;
    }
  }

  Future<List<Outfit>> feed() async {
    final response = await _dio.get('/recommendations/feed');
    final list = (response.data as List).cast<Map<String, dynamic>>();
    return list.map(Outfit.fromApi).toList();
  }

  Future<List<Outfit>> generateOutfits({
    String type = 'outfit',
    int count = 3,
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

  Future<List<Map<String, dynamic>>> productFeed({
    int limit = 30,
    String scenario = 'daily',
    String? source,
    int? minPrice,
    int? maxPrice,
    List<String> categories = const [],
    List<String> sizes = const [],
    List<String> colors = const [],
    List<String> excludeIds = const [],
  }) async {
    final queryParameters = <String, dynamic>{
      'limit': limit,
      if (scenario != 'daily') 'scenario': scenario,
      'min_price': ?minPrice,
      'max_price': ?maxPrice,
      if (categories.isNotEmpty) 'categories': categories,
      if (sizes.isNotEmpty) 'sizes': sizes,
      if (colors.isNotEmpty) 'colors': colors,
      if (excludeIds.isNotEmpty) 'exclude_ids': excludeIds,
    };

    if (source != null && source.trim().isNotEmpty) {
      queryParameters['source'] = source.trim();
    }

    final response = await _dio.get(
      '/feed',
      queryParameters: queryParameters,
      options: Options(listFormat: ListFormat.multi),
    );

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

  Future<Map<String, dynamic>> replaceIntentOutfitItem(
    String intentId,
    String outfitId,
    String slot,
    int version,
  ) async {
    final response = await _dio.post(
      '/shopping-intents/$intentId/outfits/$outfitId/replace',
      data: {'slot': slot, 'expected_version': version},
    );
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<void> saveIntentOutfit(
    String intentId,
    String outfitId,
    int version,
  ) async {
    await _dio.post(
      '/shopping-intents/$intentId/outfits/$outfitId/save',
      data: {'expected_version': version},
    );
  }

  Future<Map<String, dynamic>> giveIntentFeedback(
    String intentId,
    String productId,
    String? reason,
    int version,
  ) async {
    final response = await _dio.post(
      '/shopping-intents/$intentId/feedback',
      data: {
        'product_id': productId,
        'reason': ?reason,
        'expected_version': version,
      },
    );
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<Map<String, dynamic>> productById(String id) async {
    final response = await _dio.get('/products/$id');
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<Map<String, dynamic>> createShoppingIntent(
    String text,
    String requestId,
  ) async {
    final response = await _dio.post(
      '/shopping-intents',
      data: {'text': text, 'request_id': requestId},
    );
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<void> closeShoppingIntent(
    String id,
    int version,
    String outcome,
  ) async {
    await _dio.post(
      '/shopping-intents/$id/close',
      data: {'expected_version': version, 'outcome': outcome},
    );
  }

  Future<Map<String, dynamic>> shoppingIntent(String id) async {
    final response = await _dio.get('/shopping-intents/$id');
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<Map<String, dynamic>> refineShoppingIntent(
    String id,
    String text,
    int version, {
    String? referenceProductId,
    String? referenceOutfitId,
  }) async {
    final response = await _dio.post(
      '/shopping-intents/$id/refine',
      data: {
        'text': text,
        'expected_version': version,
        'reference_product_id': ?referenceProductId,
        'reference_outfit_id': ?referenceOutfitId,
      },
    );
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<List<Map<String, dynamic>>> productComplements(String id) async {
    final response = await _dio.get(
      '/products/${Uri.encodeComponent(id)}/complements',
    );
    return (response.data as List)
        .map((row) => Map<String, dynamic>.from(row as Map))
        .toList();
  }

  Future<List<Map<String, dynamic>>> productsBrands({int limit = 80}) async {
    final response = await _dio.get(
      '/products/brands',
      queryParameters: {'limit': limit},
    );
    final list = (response.data as List).cast<Map<String, dynamic>>();
    return list;
  }

  Future<Map<String, dynamic>> catalogueDiscovery({
    int limit = 20,
    String? category,
    String? query,
    String? source,
    int? minPrice,
    int? maxPrice,
    String? cursor,
  }) async {
    final response = await _dio.get(
      '/catalog/discovery',
      queryParameters: {
        'limit': limit,
        if (category != null && category.trim().isNotEmpty)
          'category': category.trim(),
        if (query != null && query.trim().isNotEmpty) 'q': query.trim(),
        if (source != null && source.trim().isNotEmpty) 'source': source.trim(),
        'min_price': ?minPrice,
        'max_price': ?maxPrice,
        if (cursor != null && cursor.isNotEmpty) 'cursor': cursor,
      },
    );
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<Map<String, dynamic>> catalogueDiscoveryProduct(String id) async {
    final response = await _dio.get(
      '/catalog/discovery/${Uri.encodeComponent(id)}',
    );
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<List<Map<String, dynamic>>> productsList({
    int limit = 50,
    int offset = 0,
    String? category,
    String? source,
    String? brand,
    String? query,
  }) async {
    final queryParameters = <String, dynamic>{'limit': limit, 'offset': offset};
    if (query != null && query.trim().isNotEmpty) {
      queryParameters['q'] = query.trim();
    }

    if (category != null && category.trim().isNotEmpty) {
      queryParameters['category'] = category.trim();
    }

    if (source != null && source.trim().isNotEmpty) {
      queryParameters['source'] = source.trim();
    }

    if (brand != null && brand.trim().isNotEmpty) {
      queryParameters['brand'] = brand.trim();
    }

    final response = await _dio.get(
      '/products',
      queryParameters: queryParameters,
    );

    final list = (response.data as List).cast<Map<String, dynamic>>();
    return list;
  }

  Future<void> metricsEvent(String name, {Map<String, dynamic>? meta}) async {
    await _dio.post(
      '/metrics/events',
      data: {'name': name, 'meta': meta ?? {}},
    );
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

  Future<Map<String, dynamic>> onboardingStatus() async {
    final response = await _dio.get('/onboarding/status');
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<void> activationPreferences({
    required String genderTarget,
    required String ageGroup,
    required String clothingSize,
    required String shoeSize,
    required List<String> styles,
    required String priceSegment,
    int? height,
  }) async {
    await _dio.post(
      '/onboarding/preferences',
      data: {
        'gender_target': genderTarget,
        'age_group': ageGroup,
        'clothing_size': clothingSize,
        'shoe_size': shoeSize,
        'clothing_size_system': RegExp(r'^\d').hasMatch(clothingSize)
            ? 'RU'
            : 'LETTER',
        'shoe_size_system': 'EU',
        'styles': styles,
        'price_segment': priceSegment,
        'height': ?height,
      },
    );
  }

  Future<Map<String, dynamic>> styleSwipeDeck() async {
    final response = await _dio.get(
      '/onboarding/style-swipe',
      options: Options(receiveTimeout: const Duration(seconds: 90)),
    );
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<Map<String, dynamic>> styleSwipeFeedback(
    String productId,
    String reaction,
  ) async {
    final response = await _dio.post(
      '/onboarding/style-swipe',
      data: {'product_id': productId, 'reaction': reaction},
    );
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<Map<String, dynamic>> onboardingStep1({
    required String gender,
    String? ageGroup,
  }) async {
    final response = await _dio.patch(
      '/onboarding/step1',
      data: {
        'gender': gender,
        if (ageGroup != null && ageGroup.isNotEmpty) 'age_group': ageGroup,
      },
    );
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<Map<String, dynamic>> onboardingPhoto(String photoPath) async {
    final response = await _postPhoto(
      'onboarding',
      '/onboarding/photo',
      photoPath,
    );
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<Map<String, dynamic>> onboardingPhotoConfirm({
    String? bodyShape,
    String? colorType,
    String? heightCategory,
    bool confirmed = true,
  }) async {
    final data = <String, dynamic>{'confirmed': confirmed};
    if (bodyShape != null) data['body_shape'] = bodyShape;
    if (colorType != null) data['color_type'] = colorType;
    if (heightCategory != null) data['height_category'] = heightCategory;

    final response = await _dio.patch('/onboarding/photo/confirm', data: data);
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<Map<String, dynamic>> onboardingStep3({
    List<String>? stylePreferences,
    String? priceSegment,
  }) async {
    final data = <String, dynamic>{};
    if (stylePreferences != null) data['style_preferences'] = stylePreferences;
    if (priceSegment != null) data['price_segment'] = priceSegment;

    final response = await _dio.patch('/onboarding/step3', data: data);
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<Map<String, dynamic>> onboardingComplete({int count = 3}) async {
    final response = await _dio.post(
      '/onboarding/complete',
      queryParameters: {'count': count},
      options: Options(receiveTimeout: const Duration(seconds: 120)),
    );
    return Map<String, dynamic>.from(response.data as Map);
  }

  Future<Map<String, dynamic>> fitProfilePatch({
    required int height,
    int? weight,
    required String genderTarget,
    required String clothingSize,
    String? shoeSize,
    Map<String, dynamic>? sizes,
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
        'shoe_size': ?shoeSize,
        'sizes': ?sizes,
        'clothing_size_system':
            sizes?['one_piece']?['system'] ??
            (RegExp(r'^\d').hasMatch(clothingSize) ? 'RU' : 'LETTER'),
        if (shoeSize != null)
          'shoe_size_system': sizes?['shoes']?['system'] ?? 'EU',
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
    final queryParameters = <String, dynamic>{};

    if (scenario != null && scenario.trim().isNotEmpty) {
      queryParameters['scenario'] = scenario.trim();
    }

    if (savedOnly) {
      queryParameters['saved_only'] = true;
    }

    final response = await _dio.get(
      '/outfits',
      queryParameters: queryParameters,
    );

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
      options: Options(
        sendTimeout: const Duration(seconds: 60),
        receiveTimeout: const Duration(seconds: 90),
      ),
    );

    final list = (response.data as List).cast<Map<String, dynamic>>();
    return list;
  }

  Future<void> outfitsSave(String outfitId) async {
    await _dio.post('/outfits/save', queryParameters: {'outfit_id': outfitId});
  }

  Future<void> outfitsUnsave(String outfitId) async {
    await _dio.post(
      '/outfits/unsave',
      queryParameters: {'outfit_id': outfitId},
    );
  }

  Future<Map<String, dynamic>> visualAnalysis() async {
    final response = await _dio.post(
      '/visual-analysis',
      options: Options(
        sendTimeout: const Duration(seconds: 30),
        receiveTimeout: const Duration(seconds: 45),
      ),
    );

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

  /// Safe, actionable UI copy; raw diagnostics belong only in server logs.
  static String formatError(Object e) => userErrorMessage(e);
}
