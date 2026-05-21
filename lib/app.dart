import 'dart:async';

import 'package:flutter/material.dart';
import 'core/network/api_client.dart';
import 'core/storage/token_storage.dart';
import 'core/theme/app_colors.dart';
import 'core/widgets/modish_widgets.dart';
import 'features/auth/auth_screen.dart';
import 'features/onboarding/analysis_screen.dart';
import 'features/onboarding/fit_quiz_screen.dart';
import 'features/onboarding/intro_onboarding_screen.dart';
import 'features/onboarding/upload_screen.dart';
import 'features/onboarding/welcome_screen.dart';
import 'features/profile/profile_screen.dart';
import 'features/recommendations/detail_screen.dart';
import 'features/recommendations/feed_screen.dart';
import 'features/recommendations/models.dart';
import 'features/recommendations/saved_screen.dart';
import 'features/visual/visual_analysis_screen.dart';
import 'features/products/models.dart' as prod;
import 'features/outfits/outfits_screen.dart';
import 'features/onboarding/result_screen.dart';

/// Учитывает новый формат profile_json с вложенным `analysis` и старый плоский.
bool _profileHasAnalysis(Map<String, dynamic> pj) {
  final nested = pj['analysis'];
  if (nested is Map) {
    final n = Map<String, dynamic>.from(nested);
    return n.containsKey('color_palette') ||
        n.containsKey('recommended_silhouettes') ||
        n.containsKey('summary');
  }
  return pj.containsKey('color_palette') ||
      pj.containsKey('recommended_silhouettes') ||
      pj.containsKey('summary');
}

/// Базовый профиль для ленты: указан мужской или женский пол.
bool _fitProfileReady(Map<String, dynamic> fit) {
  final g = '${fit['gender_target'] ?? ''}'.trim().toLowerCase();
  return g == 'menswear' || g == 'womenswear';
}

class ModishBootstrap extends StatefulWidget {
  const ModishBootstrap({super.key});

  @override
  State<ModishBootstrap> createState() => _ModishBootstrapState();
}

class _ModishBootstrapState extends State<ModishBootstrap> {
  late final AppController _controller = AppController();

  @override
  void initState() {
    super.initState();
    unawaited(_controller.boot());
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AppScope(notifier: _controller, child: const ModishApp());
  }
}

class AppScope extends InheritedNotifier<AppController> {
  const AppScope({super.key, required super.notifier, required super.child});
}

class ModishApp extends StatelessWidget {
  const ModishApp({super.key});

  static AppController of(BuildContext context) {
    final scope = context.dependOnInheritedWidgetOfExactType<AppScope>();
    return scope!.notifier!;
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        brightness: Brightness.dark,
        scaffoldBackgroundColor: AppColors.bg,
        fontFamily: 'Arial',
        colorScheme: const ColorScheme.dark(
          surface: AppColors.card,
          primary: AppColors.accent,
          onPrimary: AppColors.onAccent,
          secondary: AppColors.accentSoft,
          onSurface: AppColors.ink,
          outline: AppColors.line,
        ),
        dividerColor: AppColors.line,
        iconTheme: const IconThemeData(color: AppColors.ink),
        appBarTheme: const AppBarTheme(
          centerTitle: true,
          backgroundColor: AppColors.bg,
          surfaceTintColor: Colors.transparent,
          foregroundColor: AppColors.ink,
          elevation: 0,
        ),
        chipTheme: ChipThemeData(
          backgroundColor: AppColors.chipBg,
          selectedColor: AppColors.accent,
          side: const BorderSide(color: AppColors.line),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(18),
          ),
          labelStyle: const TextStyle(fontSize: 12, color: AppColors.ink),
          secondaryLabelStyle: const TextStyle(
            fontSize: 12,
            color: AppColors.onAccent,
          ),
        ),
        sliderTheme: const SliderThemeData(
          activeTrackColor: AppColors.accent,
          inactiveTrackColor: AppColors.line,
          thumbColor: AppColors.accent,
          overlayColor: Color(0x33C4A574),
        ),
        inputDecorationTheme: InputDecorationTheme(
          filled: true,
          fillColor: AppColors.card,
          hintStyle: const TextStyle(color: AppColors.muted),
          labelStyle: const TextStyle(color: AppColors.muted),
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(8),
            borderSide: const BorderSide(color: AppColors.line),
          ),
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(8),
            borderSide: const BorderSide(color: AppColors.line),
          ),
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(8),
            borderSide: const BorderSide(color: AppColors.accent, width: 1.2),
          ),
        ),
        textTheme: const TextTheme(
          bodyMedium: TextStyle(color: AppColors.ink),
          bodySmall: TextStyle(color: AppColors.muted),
          titleMedium: TextStyle(color: AppColors.ink, fontWeight: FontWeight.w600),
        ),
        snackBarTheme: SnackBarThemeData(
          backgroundColor: AppColors.surface,
          contentTextStyle: const TextStyle(color: AppColors.ink),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
          behavior: SnackBarBehavior.floating,
        ),
      ),
      home: const RootScreen(),
    );
  }
}

class RootScreen extends StatelessWidget {
  const RootScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final app = ModishApp.of(context);
    return switch (app.stage) {
      AppStage.splash => const SplashScreen(),
      AppStage.welcome => WelcomeScreen(controller: app),
      AppStage.onboarding => IntroOnboardingScreen(controller: app),
      AppStage.fitQuiz => FitQuizScreen(controller: app),
      AppStage.auth => AuthScreen(controller: app),
      AppStage.upload => UploadScreen(controller: app),
      AppStage.analysis => AnalysisScreen(controller: app),
      AppStage.home => HomeScreen(controller: app),
    };
  }
}

enum AppStage { splash, welcome, onboarding, fitQuiz, auth, upload, analysis, home }

class AppController extends ChangeNotifier {
  AppController() {
    api = ApiClient(onUnauthorized: _handleUnauthorized);
  }

  late final ApiClient api;

  AppStage stage = AppStage.splash;

  /// 0 Подборка (товары), 1 Образы, 2 Сохранённое, 3 Профиль
  int tab = 0;
  bool authRegisterMode = true;
  int? feedMinPrice;
  int? feedMaxPrice;
  List<String> feedFilterSizes = const [];
  List<String> feedFilterColors = const [];
  String email = '';
  String styleTarget = 'menswear';
  String? token;
  String? selectedPhotoPath;
  final photoPaths = <String>[];
  bool isLoading = false;
  bool feedRefreshing = false;
  String? error;
  int analysisProgress = 0;

  final savedIds = <String>{};
  final _detailViewSent = <String>{};
  final _productViewSent = <String>{};
  List<Outfit> feed = [];
  List<Map<String, dynamic>> savedRows = [];
  List<prod.FeedCard> productFeed = [];

  /// Ошибка последней загрузки `/feed` (сеть, 401, сервер).
  String? productFeedError;

  List<Map<String, dynamic>> savedProductRows = [];
  List<Map<String, dynamic>> outfits = [];
  List<Map<String, dynamic>> savedOutfits = [];
  Map<String, dynamic> summary = {};
  Map<String, dynamic> billing = {};
  String? visualImageUrl;
  Map<String, dynamic> fitProfile = {};
  Map<String, dynamic> tasteProfile = {};
  Map<String, dynamic>? pendingFitPrefs;

  Outfit? get currentOutfit => feed.isEmpty ? null : feed.first;
  prod.FeedCard? get currentProduct =>
      filteredProductFeed.isEmpty ? null : filteredProductFeed.first;

  List<prod.FeedCard> get filteredProductFeed {
    return productFeed.where((c) {
      final p = c.product;
      if (feedMinPrice != null && p.price < feedMinPrice!) return false;
      if (feedMaxPrice != null && p.price > feedMaxPrice!) return false;
      if (feedFilterSizes.isNotEmpty) {
        final sizes = p.availableSizes.map((e) => e.toUpperCase()).toSet();
        if (!feedFilterSizes.any((s) => sizes.contains(s.toUpperCase()))) {
          return false;
        }
      }
      if (feedFilterColors.isNotEmpty) {
        final cols = p.colors.map((e) => e.toLowerCase()).toList();
        if (!feedFilterColors.any(
          (col) => cols.any((pc) => pc.contains(col.toLowerCase())),
        )) {
          return false;
        }
      }
      return true;
    }).toList();
  }

  List<prod.FeedCard> relatedProducts(String productId, {int limit = 6}) {
    return filteredProductFeed
        .where((c) => c.product.id != productId)
        .take(limit)
        .toList();
  }

  Future<void> boot() async {
    await Future<void>.delayed(const Duration(milliseconds: 200));
    try {
      final stored = await TokenStorage.read().timeout(
        const Duration(seconds: 3),
        onTimeout: () => null,
      );
      if (stored != null && stored.isNotEmpty) {
        api.applyToken(stored);
        token = stored;
        final me = await api.usersMe().timeout(
          const Duration(seconds: 10),
          onTimeout: () => throw TimeoutException('usersMe'),
        );
        email = '${me['email'] ?? email}';
        try {
          fitProfile = await api.fitProfileMe().timeout(
            const Duration(seconds: 10),
            onTimeout: () => throw TimeoutException('fitProfileMe'),
          );
        } catch (_) {
          fitProfile = {};
        }
        var styleAnalyzed = false;
        try {
          final profile = await api.styleProfileMe().timeout(
            const Duration(seconds: 10),
            onTimeout: () => throw TimeoutException('styleProfileMe'),
          );
          final raw = profile['profile_json'];
          final rawMap = raw is Map ? Map<String, dynamic>.from(raw) : null;
          styleAnalyzed =
              rawMap != null && rawMap.isNotEmpty && _profileHasAnalysis(rawMap);
        } catch (_) {
          styleAnalyzed = false;
        }
        await refreshRemoteData().timeout(
          const Duration(seconds: 25),
          onTimeout: () => throw TimeoutException('refresh'),
        );
        stage = _fitProfileReady(fitProfile) || styleAnalyzed
            ? AppStage.home
            : AppStage.upload;
        tab = 0;
        notifyListeners();
        return;
      }
    } on TimeoutException catch (e) {
      error = 'Медленное соединение (${e.message ?? 'timeout'}). '
          'Проверьте интернет и войдите снова.';
    } catch (e) {
      error = ApiClient.formatError(e);
      await _clearSession();
    }
    stage = AppStage.welcome;
    notifyListeners();
  }

  void _handleUnauthorized() {
    token = null;
    api.clearToken();
    unawaited(TokenStorage.clear());
    stage = AppStage.auth;
    authRegisterMode = false;
    error = 'Сессия истекла — войдите снова.';
    notifyListeners();
  }

  Future<void> _clearSession() async {
    await TokenStorage.clear();
    api.clearToken();
    token = null;
  }

  Future<void> _routeAfterAuth() async {
    await applyPendingFitPrefsIfAny();
    try {
      fitProfile = await api.fitProfileMe();
    } catch (_) {
      fitProfile = {};
    }
    await refreshRemoteData();
    if (_fitProfileReady(fitProfile)) {
      stage = AppStage.home;
      tab = 0;
    } else {
      stage = AppStage.upload;
    }
    notifyListeners();
  }

  /// Лента без фото-анализа (базовый онбординг).
  Future<void> finishBasicOnboarding() async {
    await _run(() async {
      if (!_fitProfileReady(fitProfile)) {
        throw Exception('Укажите пол и параметры в профиле');
      }
      await refreshRemoteData();
      stage = AppStage.home;
      tab = 0;
    });
  }

  void goToWelcome() {
    stage = AppStage.welcome;
    notifyListeners();
  }

  void goToOnboarding() {
    stage = AppStage.onboarding;
    notifyListeners();
  }

  void goToFitQuiz() {
    stage = AppStage.fitQuiz;
    notifyListeners();
  }

  void setPendingFitPrefs({
    required String genderTarget,
    required String clothingSize,
    required int budgetMin,
    required int budgetMax,
    int height = 170,
    int? weight,
    List<String> interestCategories = const [],
    List<String> styleScenarios = const [],
  }) {
    pendingFitPrefs = {
      'gender_target': genderTarget,
      'clothing_size': clothingSize,
      'budget_min': budgetMin,
      'budget_max': budgetMax,
      'height': height,
      'weight': weight,
      'interest_categories': interestCategories,
      'style_scenarios': styleScenarios,
    };
    styleTarget = genderTarget;
    notifyListeners();
  }

  Future<void> applyPendingFitPrefsIfAny() async {
    final p = pendingFitPrefs;
    if (p == null || token == null) return;
    try {
      fitProfile = await api.fitProfilePatch(
        height: (p['height'] as num?)?.toInt() ?? 170,
        weight: (p['weight'] as num?)?.toInt(),
        genderTarget: '${p['gender_target'] ?? 'menswear'}',
        clothingSize: '${p['clothing_size'] ?? 'M'}',
        budgetMin: (p['budget_min'] as num?)?.toInt() ?? 0,
        budgetMax: (p['budget_max'] as num?)?.toInt() ?? 10000,
        interestCategories: List<String>.from(p['interest_categories'] as List? ?? []),
        styleScenarios: List<String>.from(p['style_scenarios'] as List? ?? []),
      );
      pendingFitPrefs = null;
    } catch (e) {
      error = ApiClient.formatError(e);
    }
  }

  void goToAuth({required bool registerMode}) {
    authRegisterMode = registerMode;
    stage = AppStage.auth;
    notifyListeners();
  }

  void applyFeedFilters({
    int? minPrice,
    int? maxPrice,
    List<String> sizes = const [],
    List<String> colors = const [],
  }) {
    feedMinPrice = minPrice;
    feedMaxPrice = maxPrice;
    feedFilterSizes = sizes;
    feedFilterColors = colors;
    notifyListeners();
  }

  Future<void> register(String password) async {
    await _run(() async {
      if (!await api.pingHealth()) {
        throw Exception(
          'Сервер не отвечает на ${ApiClient.resolvedBaseUrl()}/health. '
          'Откройте этот адрес в браузере телефона.',
        );
      }
      final t = await api.register(email, password);
      token = t;
      if (!await TokenStorage.write(t)) {
        throw Exception('Не удалось сохранить сессию на устройстве');
      }
      try {
        await api.metricsEvent('user_registered');
      } catch (_) {}
      await _routeAfterAuth();
    });
  }

  Future<void> login(String password) async {
    await _run(() async {
      if (!await api.pingHealth()) {
        throw Exception(
          'Сервер не отвечает на ${ApiClient.resolvedBaseUrl()}/health. '
          'Откройте этот адрес в браузере телефона.',
        );
      }
      final t = await api.login(email, password);
      token = t;
      if (!await TokenStorage.write(t)) {
        throw Exception('Не удалось сохранить сессию на устройстве');
      }
      await _routeAfterAuth();
    });
  }

  Future<void> analyze() async {
    isLoading = true;
    error = null;
    notifyListeners();
    try {
      if (selectedPhotoPath == null) {
        throw Exception('Сначала выберите фото');
      }
      stage = AppStage.analysis;
      analysisProgress = 0;
      notifyListeners();
      for (var i = 1; i <= 4; i++) {
        await Future<void>.delayed(const Duration(milliseconds: 320));
        analysisProgress = i * 25;
        notifyListeners();
      }
      // 100% — фото загружено, дальше реальный анализ на сервере
      await api.setStyleTarget(styleTarget);
      await api.analyzeStyleProfile(selectedPhotoPath!);
      try {
        await api.metricsEvent('photo_uploaded');
      } catch (_) {}
      await refreshRemoteData();
      stage = AppStage.home;
      tab = 0;
    } catch (e) {
      error = ApiClient.formatError(e);
      stage = AppStage.upload;
      analysisProgress = 0;
    } finally {
      isLoading = false;
      notifyListeners();
    }
  }

  Future<void> refreshRemoteData() async {
    await _refreshProductFeed();
    notifyListeners();
    unawaited(_refreshSecondaryRemoteData());
  }

  /// Обновление ленты по кнопке «Обновить» — со спиннером и понятным результатом.
  /// Возвращает текст для SnackBar или null, если всё ок и есть товары.
  Future<String?> refreshFeedFromUser() async {
    feedRefreshing = true;
    productFeedError = null;
    notifyListeners();

    await _refreshProductFeed();
    feedRefreshing = false;
    notifyListeners();
    unawaited(_refreshSecondaryRemoteData());

    if (productFeedError != null) {
      return productFeedError;
    }
    if (productFeed.isEmpty) {
      return 'Лента пустая. Возможные причины:\n'
          '• каталог не импортирован на сервер;\n'
          '• фильтры профиля (бюджет, категории) скрыли товары — сбросьте фильтры ⚙';
    }
    if (filteredProductFeed.isEmpty && productFeed.isNotEmpty) {
      return 'Товары есть, но фильтры скрыли все карточки. Сбросьте фильтры (иконка справа вверху).';
    }
    return null;
  }

  Future<void> _refreshProductFeed() async {
    try {
      final rows = await api.productFeed(limit: 30);
      productFeed = rows.map(prod.FeedCard.fromApi).toList();
      productFeedError = null;
    } catch (e) {
      productFeed = [];
      productFeedError = ApiClient.formatError(e);
    }
  }

  Future<void> _refreshSecondaryRemoteData() async {
    try {
      feed = await api.feed();
    } catch (_) {
      feed = [];
    }
    try {
      summary = await api.summary();
    } catch (_) {
      summary = {};
    }
    try {
      fitProfile = await api.fitProfileMe();
    } catch (_) {
      fitProfile = {};
    }
    try {
      tasteProfile = await api.tasteProfileMe();
    } catch (_) {
      tasteProfile = {};
    }
    try {
      savedRows = await api.savedRecommendations();
    } catch (_) {
      savedRows = [];
    }
    try {
      savedProductRows = await api.savedProducts();
    } catch (_) {
      savedProductRows = [];
    }
    try {
      billing = await api.billingStatus();
    } catch (_) {
      billing = {};
    }
    try {
      outfits = await api.outfitsList();
      savedOutfits = await api.outfitsList(savedOnly: true);
    } catch (_) {
      outfits = [];
      savedOutfits = [];
    }
    if (productFeed.isNotEmpty) {
      try {
        await api.metricsEvent('feed_opened');
      } catch (_) {}
    }
    savedIds
      ..clear()
      ..addAll(savedRows.map((r) => '${r['recommendation_id']}'));
    notifyListeners();
  }

  Future<void> generateOutfits({String scenario = 'daily'}) async {
    await _run(() async {
      await api.generateOutfits(scenario: scenario);
      await refreshRemoteData();
    });
  }

  void setTab(int value) {
    tab = value;
    notifyListeners();
  }

  void setStyleTarget(String value) {
    styleTarget = value;
    notifyListeners();
  }

  void setPhotoPath(String? path) {
    selectedPhotoPath = path;
    photoPaths
      ..clear()
      ..addAll(path == null ? const [] : [path]);
    notifyListeners();
  }

  void addPhotoPath(String path) {
    if (photoPaths.contains(path)) return;
    if (photoPaths.length >= 3) return;
    photoPaths.add(path);
    selectedPhotoPath ??= path;
    notifyListeners();
  }

  void removePhotoPath(String path) {
    photoPaths.remove(path);
    if (selectedPhotoPath == path) {
      selectedPhotoPath = photoPaths.isEmpty ? null : photoPaths.first;
    }
    notifyListeners();
  }

  void goToReanalyze() {
    selectedPhotoPath = null;
    stage = AppStage.upload;
    notifyListeners();
  }

  void exitUploadFlow() {
    if (token != null && token!.isNotEmpty) {
      if (_fitProfileReady(fitProfile)) {
        stage = AppStage.home;
        tab = 0;
      } else {
        stage = AppStage.home;
        tab = 3;
      }
    } else {
      stage = AppStage.welcome;
    }
    notifyListeners();
  }

  Future<void> logout() async {
    await TokenStorage.clear();
    api.clearToken();
    token = null;
    feed = [];
    savedRows = [];
    productFeed = [];
    productFeedError = null;
    savedProductRows = [];
    outfits = [];
    savedOutfits = [];
    summary = {};
    billing = {};
    visualImageUrl = null;
    fitProfile = {};
    tasteProfile = {};
    savedIds.clear();
    _detailViewSent.clear();
    _productViewSent.clear();
    stage = AppStage.welcome;
    notifyListeners();
  }

  /// Показ карточки в ленте: `view` один раз на товар за сессию, без блокирующего loading.
  Future<void> recordProductViewIfNeeded(String productId) async {
    if (_productViewSent.contains(productId)) return;
    _productViewSent.add(productId);
    try {
      await api.recordRecommendationEvent(
        eventType: 'view',
        productId: productId,
      );
      try {
        await api.metricsEvent(
          'product_viewed',
          meta: {'product_id': productId},
        );
      } catch (_) {}
    } catch (_) {
      _productViewSent.remove(productId);
    }
  }

  Future<void> sendProductEvent(
    BuildContext context,
    String productId,
    String eventType,
  ) async {
    await _run(() async {
      final res = await api.recordRecommendationEvent(
        eventType: eventType,
        productId: productId,
      );
      try {
        final name = switch (eventType) {
          'like' => 'product_liked',
          'save' => 'product_saved',
          'open_product' => 'product_opened',
          'buy_click' => 'product_buy_click',
          _ => null,
        };
        if (name != null) {
          await api.metricsEvent(name);
        }
      } catch (_) {}
      if (res['milestone_reached'] == true && context.mounted) {
        Navigator.of(context).push(
          MaterialPageRoute(builder: (_) => ResultScreen(controller: this)),
        );
      }
      if (eventType == 'save') {
        // оптимистично: просто перезагрузим
      }
      if (eventType == 'like' ||
          eventType == 'dislike' ||
          eventType == 'skip') {
        productFeed = productFeed
            .where((c) => c.product.id != productId)
            .toList();
      }
      await refreshRemoteData();
    });
  }

  Future<void> updateFitProfile({
    required int height,
    int? weight,
    required String genderTarget,
    required String clothingSize,
    required int budgetMin,
    required int budgetMax,
    List<String> interestCategories = const [],
    List<String> styleScenarios = const [],
  }) async {
    await _run(() async {
      fitProfile = await api.fitProfilePatch(
        height: height,
        weight: weight,
        genderTarget: genderTarget,
        clothingSize: clothingSize,
        budgetMin: budgetMin,
        budgetMax: budgetMax,
        interestCategories: interestCategories,
        styleScenarios: styleScenarios,
      );
      await refreshRemoteData();
    });
  }

  Future<void> updateTasteProfile({
    required int priceMin,
    required int priceMax,
    required String preferredFit,
  }) async {
    await _run(() async {
      tasteProfile = await api.tasteProfilePatch(
        priceMin: priceMin,
        priceMax: priceMax,
        preferredFit: preferredFit,
      );
      await refreshRemoteData();
    });
  }

  Future<void> generateOutfitsV2({int count = 3, String scenario = 'daily'}) async {
    await _run(() async {
      await api.outfitsGenerate(count: count, scenario: scenario);
      outfits = await api.outfitsList();
      savedOutfits = await api.outfitsList(savedOnly: true);
      try {
        await api.metricsEvent('outfit_generated', meta: {'count': count, 'scenario': scenario});
      } catch (_) {}
    });
  }

  Future<void> saveOutfit(String outfitId) async {
    await _run(() async {
      await api.outfitsSave(outfitId);
      outfits = await api.outfitsList();
      savedOutfits = await api.outfitsList(savedOnly: true);
      await refreshRemoteData();
    });
  }

  Future<void> unsaveOutfit(String outfitId) async {
    await _run(() async {
      await api.outfitsUnsave(outfitId);
      outfits = await api.outfitsList();
      savedOutfits = await api.outfitsList(savedOnly: true);
      await refreshRemoteData();
    });
  }

  Future<void> unsaveProduct(String productId) async {
    await _run(() async {
      await api.recordRecommendationEvent(
        eventType: 'unsave',
        productId: productId,
      );
      await refreshRemoteData();
    });
  }

  Future<void> openVisualAnalysis(BuildContext context) async {
    await _run(() async {
      final res = await api.visualAnalysis();
      final url = res['image_url'];
      if (url is String && url.isNotEmpty) {
        visualImageUrl = url;
      }
      if (context.mounted) {
        Navigator.of(context).push(
          MaterialPageRoute(
            builder: (_) => VisualAnalysisScreen(controller: this),
          ),
        );
      }
    });
  }

  Future<void> recordViewDetails(String recommendationId) async {
    if (_detailViewSent.contains(recommendationId)) return;
    _detailViewSent.add(recommendationId);
    try {
      await api.feedback(recommendationId, 'view_details');
      await refreshRemoteData();
    } catch (_) {
      /* не блокируем экран деталей */
    }
  }

  Future<void> sendFeedback(String recommendationId, String eventType) async {
    await _run(() async {
      await api.feedback(recommendationId, eventType);
      if (eventType == 'save') savedIds.add(recommendationId);
      if (eventType == 'unsave') savedIds.remove(recommendationId);
      if (eventType == 'like' || eventType == 'dislike') {
        feed = feed.where((item) => item.id != recommendationId).toList();
      }
      await refreshRemoteData();
    });
  }

  Future<void> _run(Future<void> Function() block) async {
    isLoading = true;
    error = null;
    notifyListeners();
    try {
      await block();
    } catch (e) {
      error = ApiClient.formatError(e);
    } finally {
      isLoading = false;
      notifyListeners();
    }
  }
}

class SplashScreen extends StatefulWidget {
  const SplashScreen({super.key});
  @override
  State<SplashScreen> createState() => _SplashScreenState();
}

class _SplashScreenState extends State<SplashScreen> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      Future<void>.delayed(const Duration(milliseconds: 900), () {
        if (!mounted) return;
        final app = ModishApp.of(context);
        if (app.stage == AppStage.splash) {
          app.goToWelcome();
        }
      });
    });
  }

  @override
  Widget build(BuildContext context) {
    return const MobileViewport(
      minimalBackdrop: true,
      child: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Brand(width: 200),
            SizedBox(height: 14),
            Text(
              'Персональный AI-стилист',
              style: TextStyle(color: AppColors.muted, fontSize: 16),
            ),
          ],
        ),
      ),
    );
  }
}

class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key, required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final pages = [
          FeedScreen(controller: controller),
          OutfitsScreen(controller: controller),
          SavedScreen(controller: controller),
          ProfileScreen(controller: controller),
        ];
        return MobileViewport(
          child: Scaffold(
            backgroundColor: Colors.transparent,
            body: IndexedStack(
              index: controller.tab,
              children: pages,
            ),
            bottomNavigationBar: ModishBottomNav(
              index: controller.tab,
              onChanged: controller.setTab,
            ),
          ),
        );
      },
    );
  }
}

void openDetails(
  BuildContext context,
  Outfit outfit,
  AppController controller,
) {
  Navigator.of(context).push(
    MaterialPageRoute(
      builder: (_) => DetailScreen(outfit: outfit, controller: controller),
    ),
  );
}
