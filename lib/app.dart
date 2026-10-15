import 'dart:async';

import 'package:flutter/material.dart';
import 'core/network/api_client.dart';
import 'core/network/user_error_message.dart';
import 'core/storage/token_storage.dart';
import 'core/storage/feed_history.dart';
import 'core/theme/app_colors.dart';
import 'core/theme/app_theme.dart';
import 'core/widgets/modish_widgets.dart';
import 'features/auth/auth_screen.dart';
import 'features/onboarding/analysis_screen.dart';
import 'features/onboarding/fit_quiz_screen.dart';
import 'features/onboarding/intro_onboarding_screen.dart';
import 'features/onboarding/onboarding_v2_screen.dart';
import 'features/onboarding/upload_screen.dart';
import 'features/onboarding/welcome_screen.dart';
import 'features/onboarding/wow_outfit.dart';
import 'features/profile/profile_screen.dart';
import 'features/profile/edit_profile_screen.dart';
import 'features/shopping/shopping_task_controller.dart';
import 'features/recommendations/detail_screen.dart';
import 'features/recommendations/feed_screen.dart';
import 'features/recommendations/feed_query.dart';
import 'features/recommendations/models.dart';
import 'features/recommendations/saved_screen.dart';
import 'features/visual/visual_analysis_screen.dart';
import 'features/products/models.dart' as prod;
import 'features/outfits/outfits_screen.dart';
import 'features/onboarding/result_screen.dart';

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
    final controller = ModishApp.of(context);
    return MaterialApp(
      // A new account boundary must also discard pushed routes from the old
      // account. Access-token rotation deliberately leaves this key unchanged.
      key: ValueKey(controller.navigationSessionEpoch),
      debugShowCheckedModeBanner: false,
      theme: AppTheme.dark,
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
      AppStage.fitConfirmation => EditProfileScreen(
        controller: app,
        setupMode: true,
      ),
      AppStage.auth => AuthScreen(controller: app),
      AppStage.upload => UploadScreen(controller: app),
      AppStage.analysis => AnalysisScreen(controller: app),
      AppStage.onboardingV2 => OnboardingV2Screen(controller: app),
      AppStage.home => HomeScreen(controller: app),
    };
  }
}

enum AppStage {
  splash,
  welcome,
  onboarding,
  fitQuiz,
  fitConfirmation,
  auth,
  upload,
  analysis,
  onboardingV2,
  home,
}

class AppController extends ChangeNotifier {
  AppController({ApiClient? apiClient, FeedHistory? feedHistory})
    : _ownsApi = apiClient == null {
    _feedHistory = feedHistory ?? FeedHistory();
    api = apiClient ?? ApiClient();
    _unauthorizedHandler = _handleUnauthorized;
    _sessionChangedHandler = (access, refresh) async {
      if (_disposed) return;
      token = access;
      await _persistSession(access, refresh);
    };
    api.onUnauthorized = _unauthorizedHandler;
    api.onSessionChanged = _sessionChangedHandler;
  }

  late final ApiClient api;
  final bool _ownsApi;
  late final VoidCallback _unauthorizedHandler;
  late final Future<void> Function(String, String?) _sessionChangedHandler;
  int _sessionGeneration = 0;

  /// Changes when an account is entered or left, never on access-token rotation.
  int get navigationSessionEpoch => _sessionGeneration;
  bool _isCurrentSession(int generation) =>
      !_disposed && generation == _sessionGeneration;
  final _busyOperations = <Object>{};
  Future<void> _sessionStorageWork = Future.value();
  Future<bool> _persistSession(String access, String? refresh) {
    final generation = _sessionGeneration;
    final write = _sessionStorageWork.then<bool>((_) async {
      if (!_isCurrentSession(generation)) return false;
      return await TokenStorage.write(access, refreshToken: refresh);
    });
    _sessionStorageWork = write.then<void>((_) {});
    return write;
  }

  Future<void> _clearPersistedSession() {
    // Token rotation may already be writing when logout begins. Serialize the
    // deletion after that write and before any subsequent login's persistence.
    return _sessionStorageWork = _sessionStorageWork.then(
      (_) => TokenStorage.clear(),
    );
  }

  int _secondaryGeneration = 0;
  Future<void>? _secondaryRefresh;
  late final ShoppingTaskController shoppingTask = ShoppingTaskController(api);
  late final FeedHistory _feedHistory;
  String? _historyAccount;
  Future<void>? _historyLoading;
  Future<void> _historyWrite = Future.value();
  final _historyRows = <Map<String, dynamic>>[];

  Future<void> _restoreFeedHistory() async {
    final generation = _sessionGeneration;
    final account = email.toLowerCase().trim();
    if (_historyAccount == account) {
      await _historyLoading;
      return;
    }
    _historyAccount = account;
    _historyRows.clear();
    _dismissedProductIds.clear();
    _dismissedProductKeys.clear();
    _historyLoading = (() async {
      final rows = await _feedHistory.read(account);
      if (_historyAccount != account || !_isCurrentSession(generation)) return;
      _historyRows.addAll(rows);
      for (final row in rows) {
        _dismissedProductIds.add('${row['id']}');
        _dismissedProductKeys.addAll(
          (row['keys'] as List? ?? []).whereType<String>(),
        );
      }
    })();
    await _historyLoading;
  }

  void _rememberDismissedProduct(String id, Set<String> keys) {
    _historyRows.removeWhere((row) => row['id'] == id);
    _historyRows.add({
      'id': id,
      'keys': keys.toList(),
      'at': DateTime.now().millisecondsSinceEpoch,
    });
    if (_historyRows.length > 2000) {
      _historyRows.removeRange(0, _historyRows.length - 2000);
    }
    final account = email.toLowerCase().trim();
    final snapshot = List<Map<String, dynamic>>.from(_historyRows);
    _historyWrite = _historyWrite.then(
      (_) => _feedHistory.write(account, snapshot),
    );
  }

  AppStage stage = AppStage.splash;

  /// 0 Подборка (товары), 1 Образы, 2 Сохранённое, 3 Профиль
  int tab = 0;
  bool authRegisterMode = true;
  FeedQuery _feedQuery = FeedQuery();
  List<String> get feedFilterCategories => _feedQuery.categories;
  String get feedScenario => _feedQuery.scenario;
  bool get supportsShoppingContext =>
      (activationStatus['recommendation_features'] as List? ?? const [])
          .contains('context_v1');
  bool get supportsShoppingIntent =>
      (activationStatus['recommendation_features'] as List? ?? const [])
          .contains('shopping_intent_v1');
  bool get supportsCatalogueDiscovery =>
      (activationStatus['recommendation_features'] as List? ?? const [])
          .contains('catalogue_discovery_v1');
  int? get feedMinPrice => _feedQuery.minPrice;
  int? get feedMaxPrice => _feedQuery.maxPrice;
  List<String> get feedFilterSizes => _feedQuery.sizes;
  List<String> get feedFilterColors => _feedQuery.colors;
  String email = '';
  String? styleTarget;
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
  final _dismissedProductKeys = <String>{};
  final _pendingProductActions = <String>{};
  int _feedRequestGeneration = 0;
  Future<void>? _feedLoad;
  int _feedLoadGeneration = -1;
  final _dismissedProductIds = <String>{};
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

  /// Онбординг v2: 0 step1 … 4 wow progress, 5 wow result.
  int onboardingV2SubStep = 0;
  Map<String, dynamic>? photoConfirmPending;
  List<WowOutfit> wowOutfits = const [];
  Map<String, dynamic> activationStatus = {};
  List<prod.Product> styleSwipeCards = [];
  int styleSwipeRated = 0;
  int styleSwipeMinimum = 15;
  int styleSwipeTarget = 20;
  bool styleSwipeCanContinue = false;
  bool _activationCompleted = false;
  int _feedReactions = 0;

  Outfit? get currentOutfit => feed.isEmpty ? null : feed.first;
  prod.FeedCard? get currentProduct =>
      filteredProductFeed.isEmpty ? null : filteredProductFeed.first;

  List<prod.FeedCard> get filteredProductFeed => productFeed;

  final _related = <String, List<prod.FeedCard>>{};
  final _relatedLoading = <String>{};
  int _relatedGeneration = 0;

  List<prod.FeedCard> relatedProducts(String productId, {int limit = 6}) =>
      (_related[productId] ?? const <prod.FeedCard>[]).take(limit).toList();

  String relatedProductsHint(String productId) => 'Дополнения к этой вещи';

  Future<void> loadRelatedProducts(String productId) async {
    if (_disposed ||
        _related.containsKey(productId) ||
        !_relatedLoading.add(productId)) {
      return;
    }
    final generation = _relatedGeneration;
    try {
      final rows = await api.productComplements(productId);
      if (generation != _relatedGeneration) return;
      final seen = <String>{'id:$productId'};
      _related[productId] = rows
          .map(
            (row) => prod.FeedCard(
              product: prod.Product.fromApi(row),
              reason: 'Дополняет образ',
            ),
          )
          .where((card) {
            final keys = card.product.identityKeys;
            if (keys.any(seen.contains)) return false;
            seen.addAll(keys);
            return true;
          })
          .toList();
      while (_related.length > 20) {
        _related.remove(_related.keys.first);
      }
      notifyListeners();
    } catch (_) {
      // A missing complement must never be replaced by an unrelated feed item.
    } finally {
      if (generation == _relatedGeneration) _relatedLoading.remove(productId);
    }
  }

  void _clearRelated() {
    _relatedGeneration++;
    _related.clear();
    _relatedLoading.clear();
  }

  Future<void> boot() async {
    final generation = _sessionGeneration;
    await Future<void>.delayed(const Duration(milliseconds: 200));
    if (!_isCurrentSession(generation)) return;
    try {
      final stored = await TokenStorage.read().timeout(
        const Duration(seconds: 3),
        onTimeout: () => null,
      );
      if (!_isCurrentSession(generation)) return;
      if (stored != null && stored.isNotEmpty) {
        api.applyToken(stored);
        final refresh = await TokenStorage.readRefresh();
        if (!_isCurrentSession(generation)) return;
        api.refreshToken = refresh;
        token = stored;
        final me = await api.usersMe().timeout(
          const Duration(seconds: 10),
          onTimeout: () => throw TimeoutException('usersMe'),
        );
        if (!_isCurrentSession(generation)) return;
        email = '${me['email'] ?? email}';
        await api.upgradeSession();
        if (!_isCurrentSession(generation)) return;
        try {
          final profile = await api.fitProfileMe().timeout(
            const Duration(seconds: 10),
            onTimeout: () => throw TimeoutException('fitProfileMe'),
          );
          if (!_isCurrentSession(generation)) return;
          fitProfile = profile;
        } catch (_) {
          if (!_isCurrentSession(generation)) return;
          fitProfile = {};
        }
        final obStatus = await api.onboardingStatus().timeout(
          const Duration(seconds: 8),
          onTimeout: () => throw TimeoutException('onboardingStatus'),
        );
        if (!_isCurrentSession(generation)) return;
        activationStatus = obStatus;
        _activationCompleted = obStatus['completed'] == true;
        if (_activationCompleted &&
            obStatus['fit_confirmation_required'] == true) {
          stage = AppStage.fitConfirmation;
          notifyListeners();
          return;
        }
        if (obStatus['completed'] != true) {
          onboardingV2SubStep = _onboardingV2SubFromStatus(obStatus);
          stage = AppStage.onboardingV2;
          tab = 0;
          notifyListeners();
          return;
        }
        await refreshRemoteData().timeout(
          const Duration(seconds: 25),
          onTimeout: () => throw TimeoutException('refresh'),
        );
        if (!_isCurrentSession(generation)) return;
        stage = AppStage.home;
        tab = 0;
        notifyListeners();
        return;
      }
    } on TimeoutException {
      if (!_isCurrentSession(generation)) return;
      error = 'Медленное соединение. Проверьте интернет и войдите снова.';
    } catch (e) {
      if (!_isCurrentSession(generation)) return;
      if (token == null) {
        error = 'Сессия истекла. Войдите снова, чтобы продолжить.';
        authRegisterMode = false;
        stage = AppStage.auth;
        notifyListeners();
        return;
      }
      error = 'Не удалось подключиться. Проверьте интернет и повторите вход.';
    }
    stage = AppStage.welcome;
    notifyListeners();
  }

  void _handleUnauthorized() {
    if (_disposed) return;
    _resetAccountState();
    token = null;
    api.clearToken();
    unawaited(_clearPersistedSession());
    stage = AppStage.auth;
    authRegisterMode = false;
    error = 'Сессия истекла — войдите снова.';
    notifyListeners();
  }

  Future<void> _routeAfterAuth({bool resetAccount = true}) async {
    if (resetAccount) {
      final pending = pendingFitPrefs;
      _resetAccountState();
      pendingFitPrefs = pending;
    }
    // Routing belongs to the newly accepted session. The login operation's
    // earlier generation intentionally cannot handle this session's errors.
    await _run(_loadAuthenticatedRoute);
  }

  Future<void> _loadAuthenticatedRoute() async {
    final generation = _sessionGeneration;
    _feedReactions = 0;
    styleSwipeCards = [];
    await applyPendingFitPrefsIfAny();
    if (!_isCurrentSession(generation)) return;
    try {
      final profile = await api.fitProfileMe();
      if (!_isCurrentSession(generation)) return;
      fitProfile = profile;
    } catch (_) {
      if (!_isCurrentSession(generation)) return;
      fitProfile = {};
    }
    styleTarget = fitProfile['gender_target'] as String?;
    final obStatus = await api.onboardingStatus();
    if (!_isCurrentSession(generation)) return;
    activationStatus = obStatus;
    _activationCompleted = obStatus['completed'] == true;
    if (_activationCompleted && obStatus['fit_confirmation_required'] == true) {
      stage = AppStage.fitConfirmation;
      notifyListeners();
      return;
    }
    if (obStatus['completed'] != true) {
      onboardingV2SubStep = _onboardingV2SubFromStatus(obStatus);
      photoConfirmPending = null;
      wowOutfits = const [];
      stage = AppStage.onboardingV2;
      notifyListeners();
      return;
    }
    await refreshRemoteData();
    if (!_isCurrentSession(generation)) return;
    stage = AppStage.home;
    tab = 0;
    notifyListeners();
  }

  int _onboardingV2SubFromStatus(Map<String, dynamic> status) {
    styleSwipeRated = (status['rated'] as num?)?.toInt() ?? 0;
    styleSwipeCanContinue =
        false; // Reload the durable server verdict with the deck.
    return status['preferences_ready'] == true ? 1 : 0;
  }

  Future<void> startStyleSwipe({
    required String genderTarget,
    required String ageGroup,
    required String clothingSize,
    required String shoeSize,
    required List<String> styles,
    required String priceSegment,
    required int height,
  }) async {
    final generation = _sessionGeneration;
    await _run(() async {
      await api.activationPreferences(
        genderTarget: genderTarget,
        ageGroup: ageGroup,
        clothingSize: clothingSize,
        shoeSize: shoeSize,
        styles: styles,
        priceSegment: priceSegment,
        height: height,
      );
      if (!_isCurrentSession(generation)) return;
      styleTarget = genderTarget;
      activationStatus = {
        'gender_target': genderTarget,
        'age_group': ageGroup,
        'clothing_size': clothingSize,
        'shoe_size': shoeSize,
        'style_preferences': styles,
        'price_segment': priceSegment,
        'height': height,
        'preferences_ready': true,
      };
      _goOnboardingV2Sub(1);
      await _loadStyleSwipe();
    });
  }

  Future<void> _loadStyleSwipe() async {
    final generation = _sessionGeneration;
    final data = await api.styleSwipeDeck();
    if (!_isCurrentSession(generation)) return;
    styleSwipeCards = (data['products'] as List? ?? [])
        .whereType<Map>()
        .map((row) => prod.Product.fromApi(Map<String, dynamic>.from(row)))
        .toList();
    styleSwipeRated = (data['rated'] as num?)?.toInt() ?? 0;
    styleSwipeMinimum = (data['minimum'] as num?)?.toInt() ?? 15;
    styleSwipeTarget = (data['target'] as num?)?.toInt() ?? 20;
    styleSwipeCanContinue = data['can_continue'] == true;
  }

  Future<void> loadStyleSwipe() => _run(_loadStyleSwipe);

  void editActivationPreferences() {
    error = null;
    _goOnboardingV2Sub(0);
  }

  Future<void> rateStyleSwipe(String reaction) async {
    if (isLoading || styleSwipeCards.isEmpty) return;
    final id = styleSwipeCards.first.id;
    final generation = _sessionGeneration;
    await _run(() async {
      final result = await api.styleSwipeFeedback(id, reaction);
      if (!_isCurrentSession(generation)) return;
      styleSwipeRated = (result['rated'] as num?)?.toInt() ?? styleSwipeRated;
      styleSwipeCanContinue = result['can_continue'] == true;
      styleSwipeCards.removeWhere((p) => p.id == id);
      if (styleSwipeCards.isEmpty && !styleSwipeCanContinue) {
        await _loadStyleSwipe();
      }
    });
  }

  void _goOnboardingV2Sub(int sub) {
    onboardingV2SubStep = sub;
    notifyListeners();
  }

  Future<void> onboardingV2Step1({
    required String gender,
    String? ageGroup,
  }) async {
    final generation = _sessionGeneration;
    await _run(() async {
      await api.onboardingStep1(gender: gender, ageGroup: ageGroup);
      if (!_isCurrentSession(generation)) return;
      styleTarget = switch (gender) {
        'female' => 'womenswear',
        'male' => 'menswear',
        'unisex' => 'unisex',
        _ => null,
      };
      _goOnboardingV2Sub(1);
    });
  }

  Future<void> onboardingV2AnalyzePhoto(String path) async {
    final generation = _sessionGeneration;
    isLoading = true;
    error = null;
    notifyListeners();
    try {
      final res = await api.onboardingPhoto(path);
      if (!_isCurrentSession(generation)) return;
      photoConfirmPending = res;
      _goOnboardingV2Sub(2);
    } catch (e) {
      if (!_isCurrentSession(generation)) return;
      error = ApiClient.formatError(e);
    } finally {
      if (_isCurrentSession(generation)) {
        isLoading = false;
        notifyListeners();
      }
    }
  }

  void onboardingV2SkipPhoto() {
    photoConfirmPending = null;
    _goOnboardingV2Sub(3);
  }

  Future<void> onboardingV2ConfirmPhoto({
    String? bodyShape,
    String? colorType,
    String? heightCategory,
  }) async {
    final generation = _sessionGeneration;
    await _run(() async {
      await api.onboardingPhotoConfirm(
        bodyShape: bodyShape,
        colorType: colorType,
        heightCategory: heightCategory,
      );
      if (!_isCurrentSession(generation)) return;
      photoConfirmPending = null;
      _goOnboardingV2Sub(3);
    });
  }

  Future<void> onboardingV2Step3({
    List<String> stylePreferences = const [],
    String? priceSegment,
  }) async {
    await _run(() async {
      await api.onboardingStep3(
        stylePreferences: stylePreferences,
        priceSegment: priceSegment,
      );
    });
  }

  Future<void> onboardingV2CompleteWow() async {
    if (!styleSwipeCanContinue ||
        styleSwipeRated < styleSwipeMinimum ||
        isLoading) {
      return;
    }
    final generation = _sessionGeneration;
    _goOnboardingV2Sub(4);
    isLoading = true;
    error = null;
    notifyListeners();
    try {
      final res = await api.onboardingComplete(count: 3);
      if (!_isCurrentSession(generation)) return;
      if (res['recommendation_features'] is List) {
        activationStatus = {
          ...activationStatus,
          'recommendation_features': res['recommendation_features'],
        };
      }
      _activationCompleted = res['completed'] == true;
      final rows = res['outfits'];
      wowOutfits = rows is List
          ? rows
                .whereType<Map>()
                .map((e) => WowOutfit.fromApi(Map<String, dynamic>.from(e)))
                .toList()
          : const [];
      try {
        final profile = await api.fitProfileMe();
        if (!_isCurrentSession(generation)) return;
        fitProfile = profile;
      } catch (_) {}
      if (!_isCurrentSession(generation)) return;
      _goOnboardingV2Sub(5);
      try {
        await api.metricsEvent('onboarding_v2_completed');
      } catch (_) {}
    } catch (e) {
      if (!_isCurrentSession(generation)) return;
      error = ApiClient.formatError(e);
      _goOnboardingV2Sub(1);
    } finally {
      if (_isCurrentSession(generation)) {
        isLoading = false;
        notifyListeners();
      }
    }
  }

  Future<void> finishOnboardingV2ToHome() async {
    if (!_activationCompleted) return;
    stage = AppStage.home;
    tab = 0;
    onboardingV2SubStep = 0;
    notifyListeners();
    await refreshRemoteData();
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
    required int height,
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
    final generation = _sessionGeneration;
    final p = pendingFitPrefs;
    if (p == null || token == null) return;
    final height = (p['height'] as num?)?.toInt();
    final size = '${p['clothing_size'] ?? ''}'.trim();
    final budget = (p['budget_max'] as num?)?.toInt();
    final gender = p['gender_target'];
    if (height == null ||
        height < 50 ||
        height > 250 ||
        size.isEmpty ||
        budget == null ||
        budget <= 0 ||
        (gender != 'menswear' && gender != 'womenswear')) {
      return;
    }
    try {
      final profile = await api.fitProfilePatch(
        height: height,
        weight: (p['weight'] as num?)?.toInt(),
        genderTarget: gender as String,
        clothingSize: size,
        budgetMin: (p['budget_min'] as num?)?.toInt() ?? 0,
        budgetMax: budget,
        interestCategories: List<String>.from(
          p['interest_categories'] as List? ?? [],
        ),
        styleScenarios: List<String>.from(p['style_scenarios'] as List? ?? []),
      );
      if (!_isCurrentSession(generation)) return;
      fitProfile = profile;
      pendingFitPrefs = null;
    } catch (e) {
      if (_isCurrentSession(generation)) error = ApiClient.formatError(e);
    }
  }

  void goToAuth({required bool registerMode}) {
    error = null;
    authRegisterMode = registerMode;
    stage = AppStage.auth;
    notifyListeners();
  }

  Future<void> applyFeedFilters({
    String scenario = 'daily',
    int? minPrice,
    int? maxPrice,
    List<String> sizes = const [],
    List<String> colors = const [],
    List<String> categories = const [],
  }) async {
    _feedQuery = FeedQuery(
      scenario: supportsShoppingContext ? scenario : 'daily',
      minPrice: minPrice,
      maxPrice: maxPrice,
      sizes: sizes,
      colors: colors,
      categories: categories,
    );
    _feedRequestGeneration++;
    productFeed = [];
    await refreshFeedFromUser();
  }

  Future<void> register(String password) async {
    final generation = _sessionGeneration;
    await _run(() async {
      final healthy = await api.pingHealth();
      if (!_isCurrentSession(generation)) return;
      if (!healthy) {
        throw const AppFailure(AppFailureKind.serviceUnavailable);
      }
      final t = await api.register(email, password);
      if (!_isCurrentSession(generation)) return;
      token = t;
      if (!await _persistSession(t, api.refreshToken)) {
        if (!_isCurrentSession(generation)) return;
        throw const AppFailure(AppFailureKind.sessionStorage);
      }
      if (!_isCurrentSession(generation)) return;
      try {
        await api.metricsEvent('user_registered');
      } catch (_) {}
      if (!_isCurrentSession(generation)) return;
      await _routeAfterAuth();
    });
  }

  Future<void> login(String password) async {
    final generation = _sessionGeneration;
    await _run(() async {
      final healthy = await api.pingHealth();
      if (!_isCurrentSession(generation)) return;
      if (!healthy) {
        throw const AppFailure(AppFailureKind.serviceUnavailable);
      }
      final t = await api.login(email, password);
      if (!_isCurrentSession(generation)) return;
      token = t;
      if (!await _persistSession(t, api.refreshToken)) {
        if (!_isCurrentSession(generation)) return;
        throw const AppFailure(AppFailureKind.sessionStorage);
      }
      if (!_isCurrentSession(generation)) return;
      await _routeAfterAuth();
    });
  }

  int _analysisRun = 0;

  Future<void> analyze() async {
    if (isLoading) return;
    final run = ++_analysisRun;
    final photo = selectedPhotoPath;
    isLoading = true;
    error = null;
    notifyListeners();
    try {
      if (photo == null) {
        throw Exception('Сначала выберите фото');
      }
      stage = AppStage.analysis;
      analysisProgress = 25;
      notifyListeners();
      final target = styleTarget;
      if (target != null) await api.setStyleTarget(target);
      if (run != _analysisRun) return;
      await api.analyzeStyleProfile(photo);
      if (run != _analysisRun) return;
      analysisProgress = 100;
      try {
        await api.metricsEvent('photo_uploaded');
      } catch (_) {}
      if (run != _analysisRun) return;
      await refreshRemoteData();
      if (run != _analysisRun) return;
      stage = AppStage.home;
      tab = 0;
    } catch (e) {
      if (run != _analysisRun) return;
      error = ApiClient.formatError(e);
      stage = AppStage.upload;
      analysisProgress = 0;
    } finally {
      if (run == _analysisRun) {
        isLoading = false;
        notifyListeners();
      }
    }
  }

  Future<void> refreshRemoteData() async {
    final generation = _sessionGeneration;
    await _refreshProductFeed();
    if (!_isCurrentSession(generation)) return;
    notifyListeners();
    unawaited(_refreshSecondaryRemoteData());
  }

  /// Обновление ленты по кнопке «Обновить» — со спиннером и понятным результатом.
  /// Возвращает текст для SnackBar или null, если всё ок и есть товары.
  Future<String?> refreshFeedFromUser() async {
    final generation = _sessionGeneration;
    productFeedError = null;
    await _refreshProductFeed();
    if (!_isCurrentSession(generation)) return null;
    unawaited(_refreshSecondaryRemoteData());

    if (productFeedError != null) {
      return productFeedError;
    }
    if (productFeed.isEmpty) {
      return 'Подходящих вещей пока нет. Попробуйте изменить или сбросить фильтры.';
    }
    return null;
  }

  Future<void> _refreshProductFeed() {
    if (_disposed) return Future.value();
    if (_feedLoad != null && _feedLoadGeneration == _feedRequestGeneration) {
      return _feedLoad!;
    }
    final generation = _feedRequestGeneration;
    _feedLoadGeneration = generation;
    feedRefreshing = true;
    notifyListeners();
    return _feedLoad = _fetchFeed(generation).whenComplete(() {
      if (generation == _feedRequestGeneration) {
        _feedLoad = null;
        feedRefreshing = false;
        notifyListeners();
      }
    });
  }

  Future<void> _fetchFeed(int generation) async {
    try {
      await _restoreFeedHistory();
      if (generation != _feedRequestGeneration) return;
      final excluded = <String>{
        ..._dismissedProductIds.toList().reversed.take(90),
        ...productFeed.map((card) => card.product.id).take(30),
      };
      final rows = await _feedQuery.fetch(api, excludeIds: excluded);
      if (generation != _feedRequestGeneration) return;
      // Merge at response time: swipes during a slow request stay dismissed,
      // while the current card and the remaining queue retain their position.
      productFeed = mergeFeedCards([
        ...productFeed,
        ...rows,
      ], dismissedKeys: _dismissedProductKeys);
      productFeedError = null;
    } catch (e) {
      if (generation != _feedRequestGeneration) return;
      productFeedError = ApiClient.formatError(e);
    }
  }

  Future<void> _rerankRemainingFeed() {
    final generation = ++_feedRequestGeneration;
    _feedLoadGeneration = generation;
    feedRefreshing = true;
    notifyListeners();
    return _feedLoad = (() async {
      try {
        final rows = await _feedQuery.fetch(
          api,
          excludeIds: [
            ..._dismissedProductIds.toList().reversed.take(90),
            if (currentProduct != null) currentProduct!.product.id,
          ],
        );
        if (generation != _feedRequestGeneration) return;
        productFeed = mergeFeedCards([
          ...productFeed.take(1),
          ...rows,
        ], dismissedKeys: _dismissedProductKeys);
      } catch (_) {
        // Keep the usable queue when a background rerank cannot reach the API.
      } finally {
        if (generation == _feedRequestGeneration) {
          _feedLoad = null;
          feedRefreshing = false;
          notifyListeners();
        }
      }
    })();
  }

  Future<void> _refreshSecondaryRemoteData() async {
    if (_disposed) return;
    if (_secondaryRefresh != null) return _secondaryRefresh;
    final session = _sessionGeneration;
    final request = _secondaryGeneration;
    bool isCurrent() =>
        _isCurrentSession(session) && request == _secondaryGeneration;
    Future<void> load<T>(Future<T> read, void Function(T) apply) async {
      try {
        final value = await read;
        if (isCurrent()) apply(value);
      } catch (_) {
        // A failed read cannot erase an already confirmed profile/collection.
      }
    }

    late final Future<void> refresh;
    refresh =
        Future.wait([
              load(api.summary(), (value) => summary = value),
              load(api.fitProfileMe(), (value) => fitProfile = value),
              load(api.tasteProfileMe(), (value) => tasteProfile = value),
              load(api.savedRecommendations(), (value) {
                savedRows = value;
                savedIds
                  ..clear()
                  ..addAll(value.map((row) => '${row['recommendation_id']}'));
              }),
              load(api.savedProducts(), (value) => savedProductRows = value),
              load(api.billingStatus(), (value) => billing = value),
              load(api.outfitsList(), (value) => outfits = value),
              load(
                api.outfitsList(savedOnly: true),
                (value) => savedOutfits = value,
              ),
            ])
            .then((_) async {
              if (!isCurrent()) return;
              notifyListeners();
              if (productFeed.isNotEmpty) {
                try {
                  await api.metricsEvent('feed_opened');
                } catch (_) {}
              }
            })
            .whenComplete(() {
              if (identical(_secondaryRefresh, refresh)) {
                _secondaryRefresh = null;
              }
            });
    _secondaryRefresh = refresh;
    return refresh;
  }

  Future<void> refreshSavedItems() async {
    final generation = _sessionGeneration;
    try {
      final products = await api.savedProducts();
      if (!_isCurrentSession(generation)) return;
      final looks = await api.outfitsList(savedOnly: true);
      if (_isCurrentSession(generation)) {
        savedProductRows = products;
        savedOutfits = looks;
        notifyListeners();
      }
    } catch (_) {
      // Preserve the last confirmed saved collection during a network failure.
    }
  }

  Future<void> generateOutfits({String scenario = 'daily'}) async {
    final generation = _sessionGeneration;
    await _run(() async {
      await api.generateOutfits(scenario: scenario);
      if (!_isCurrentSession(generation)) return;
      _invalidateSecondaryReads();
      await refreshRemoteData();
    });
  }

  void setTab(int value) {
    tab = value;
    if (value == 3) unawaited(refreshTasteSummary());
    notifyListeners();
  }

  bool _disposed = false;
  bool _tasteRefreshing = false;

  Future<void> refreshTasteSummary() async {
    if (_tasteRefreshing || _disposed) return;
    _tasteRefreshing = true;
    final generation = _sessionGeneration;
    final request = _secondaryGeneration;
    try {
      final data = await api.tasteProfileMe();
      if (_isCurrentSession(generation) &&
          request == _secondaryGeneration &&
          stage == AppStage.home) {
        tasteProfile = data;
        notifyListeners();
      }
    } catch (_) {
      // Retain the last confirmed summary when the network is unavailable.
    } finally {
      if (_isCurrentSession(generation) && request == _secondaryGeneration) {
        _tasteRefreshing = false;
      }
    }
  }

  @override
  void dispose() {
    _disposed = true;
    _sessionGeneration++;
    _feedRequestGeneration++;
    _analysisRun++;
    _clearRelated();
    if (identical(api.onUnauthorized, _unauthorizedHandler)) {
      api.onUnauthorized = null;
    }
    if (identical(api.onSessionChanged, _sessionChangedHandler)) {
      api.onSessionChanged = null;
    }
    if (_ownsApi) api.dispose();
    shoppingTask.dispose();
    super.dispose();
  }

  @override
  void notifyListeners() {
    if (!_disposed) super.notifyListeners();
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
    photoPaths.clear();
    error = null;
    styleTarget = fitProfile['gender_target'] as String?;
    stage = AppStage.upload;
    notifyListeners();
  }

  void exitUploadFlow() {
    _analysisRun++;
    isLoading = false;
    selectedPhotoPath = null;
    photoPaths.clear();
    error = null;
    if (token != null && token!.isNotEmpty) {
      stage = AppStage.home;
      tab = 3;
    } else {
      stage = AppStage.welcome;
    }
    notifyListeners();
  }

  Future<void> logout() async {
    if (_disposed) return;
    _resetAccountState();
    unawaited(api.logoutSession());
    token = null;
    stage = AppStage.welcome;
    notifyListeners();
    await _clearPersistedSession();
  }

  /// One boundary for all account-owned state and in-flight read generations.
  void _resetAccountState() {
    _sessionGeneration++;
    _invalidateSecondaryReads();
    _busyOperations.clear();
    error = null;
    isLoading = false;
    feedRefreshing = false;
    _feedLoad = null;
    _feedLoadGeneration = -1;
    _tasteRefreshing = false;
    _analysisRun++;
    analysisProgress = 0;
    tab = 0;
    styleTarget = null;
    pendingFitPrefs = null;
    selectedPhotoPath = null;
    photoPaths.clear();
    photoConfirmPending = null;
    wowOutfits = const [];
    onboardingV2SubStep = 0;
    shoppingTask.clear();
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
    _clearRelated();
    _detailViewSent.clear();
    _productViewSent.clear();
    _dismissedProductKeys.clear();
    _dismissedProductIds.clear();
    _historyAccount = null;
    _historyLoading = null;
    _historyRows.clear();
    _feedQuery = FeedQuery();
    _pendingProductActions.clear();
    _activationCompleted = false;
    activationStatus = {};
    styleSwipeCards = [];
    styleSwipeRated = 0;
    styleSwipeMinimum = 15;
    styleSwipeTarget = 20;
    styleSwipeCanContinue = false;
    _feedReactions = 0;
    _feedRequestGeneration++;
  }

  void _invalidateSecondaryReads() {
    _secondaryGeneration++;
    _secondaryRefresh = null;
    _tasteRefreshing = false;
  }

  /// Показ карточки в ленте: `view` один раз на товар за сессию, без блокирующего loading.
  Future<void> recordProductViewIfNeeded(String productId) async {
    final generation = _sessionGeneration;
    if (_productViewSent.contains(productId)) return;
    _productViewSent.add(productId);
    try {
      await api.recordRecommendationEvent(
        eventType: 'view',
        productId: productId,
        meta: {'surface': 'feed', 'scenario': feedScenario},
      );
      if (!_isCurrentSession(generation)) return;
      try {
        await api.metricsEvent(
          'product_viewed',
          meta: {'product_id': productId},
        );
      } catch (_) {}
    } catch (_) {
      if (_isCurrentSession(generation)) _productViewSent.remove(productId);
    }
  }

  Future<void> sendProductEvent(
    BuildContext context,
    String productId,
    String eventType, {
    Map<String, dynamic>? meta,
  }) async {
    if (_disposed) return;
    final generation = _sessionGeneration;
    final advances = {'like', 'dislike', 'skip', 'save'}.contains(eventType);
    final eventMeta = <String, dynamic>{
      if (currentProduct?.product.id == productId && tab == 0) ...{
        'surface': 'feed',
        'scenario': feedScenario,
      },
      ...?meta,
    };
    if (_pendingProductActions.contains(productId)) return;
    _pendingProductActions.add(productId);
    if (advances) {
      final keys = <String>{'id:$productId'};
      for (final card in productFeed) {
        if (card.product.id == productId) {
          keys.addAll(card.product.identityKeys);
        }
      }
      _dismissedProductKeys.addAll(keys);
      _dismissedProductIds.add(productId);
      _rememberDismissedProduct(productId, keys);
      productFeed.removeWhere((c) => c.product.identityKeys.any(keys.contains));
      if (productFeed.length <= 8) unawaited(_refreshProductFeed());
      notifyListeners();
    }
    try {
      await _run(() async {
        final res = await api.recordRecommendationEvent(
          eventType: eventType,
          productId: productId,
          meta: eventMeta,
        );
        if (!_isCurrentSession(generation)) return;
        try {
          final name = switch (eventType) {
            'like' => 'product_liked',
            'save' => 'product_saved',
            'open_product' => 'product_opened',
            'affiliate_click' => 'product_affiliate_click',
            _ => null,
          };
          if (name != null) {
            await api.metricsEvent(name);
          }
        } catch (_) {}
        if (!_isCurrentSession(generation)) return;
        if (res['milestone_reached'] == true && context.mounted) {
          Navigator.of(context).push(
            MaterialPageRoute(builder: (_) => ResultScreen(controller: this)),
          );
        }
        if (eventType == 'save') {
          _invalidateSecondaryReads();
          final rows = await api.savedProducts();
          if (!_isCurrentSession(generation)) return;
          savedProductRows = rows;
        }
        if (advances) {
          _feedReactions++;
          if (_feedReactions % 10 == 0) {
            unawaited(_rerankRemainingFeed());
          } else if (productFeed.length < 5) {
            await _refreshProductFeed();
          }
        }
      });
    } finally {
      if (_isCurrentSession(generation)) {
        _pendingProductActions.remove(productId);
      }
    }
  }

  Future<void> updateFitProfile({
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
    final generation = _sessionGeneration;
    await _run(() async {
      final profile = await api.fitProfilePatch(
        height: height,
        weight: weight,
        genderTarget: genderTarget,
        clothingSize: clothingSize,
        shoeSize: shoeSize,
        sizes: sizes,
        budgetMin: budgetMin,
        budgetMax: budgetMax,
        interestCategories: interestCategories,
        styleScenarios: styleScenarios,
      );
      if (!_isCurrentSession(generation)) return;
      fitProfile = profile;
      _invalidateSecondaryReads();
      styleTarget = genderTarget;
      _clearRelated();
      shoppingTask.clear();
      _feedRequestGeneration++;
      productFeed = [];
      await refreshRemoteData();
    });
  }

  Future<void> finishFitConfirmation() => _routeAfterAuth(resetAccount: false);

  Future<void> updateTasteProfile({
    required int priceMin,
    required int priceMax,
    required String preferredFit,
  }) async {
    final generation = _sessionGeneration;
    await _run(() async {
      final profile = await api.tasteProfilePatch(
        priceMin: priceMin,
        priceMax: priceMax,
        preferredFit: preferredFit,
      );
      if (!_isCurrentSession(generation)) return;
      tasteProfile = profile;
      _invalidateSecondaryReads();
      await refreshRemoteData();
    });
  }

  Future<int> generateOutfitsV2({
    int count = 3,
    String scenario = 'daily',
  }) async {
    final generation = _sessionGeneration;
    var createdCount = 0;
    await _run(() async {
      final scenarios = scenario == 'all'
          ? const ['daily', 'office', 'evening']
          : [scenario];
      for (final key in scenarios) {
        final created = await api.outfitsGenerate(count: count, scenario: key);
        if (!_isCurrentSession(generation)) return;
        createdCount += created.length;
      }
      _invalidateSecondaryReads();
      await _reloadOutfitLists(generation);
      if (!_isCurrentSession(generation)) return;
      try {
        await api.metricsEvent(
          'outfit_generated',
          meta: {'count': createdCount, 'scenario': scenario},
        );
      } catch (_) {}
    });
    return createdCount;
  }

  /// Returns whether the server confirmed the mutation. A subsequent list
  /// refresh can fail independently; that must not invite a duplicate save.
  Future<bool> saveOutfit(String outfitId) async {
    final generation = _sessionGeneration;
    var confirmed = false;
    await _run(() async {
      await api.outfitsSave(outfitId);
      if (!_isCurrentSession(generation)) return;
      confirmed = true;
      _applyConfirmedOutfitSave(outfitId, true);
      _invalidateSecondaryReads();
      await _reloadOutfitLists(generation);
    });
    return confirmed && _isCurrentSession(generation);
  }

  Future<bool> unsaveOutfit(String outfitId) async {
    final generation = _sessionGeneration;
    var confirmed = false;
    await _run(() async {
      await api.outfitsUnsave(outfitId);
      if (!_isCurrentSession(generation)) return;
      confirmed = true;
      _applyConfirmedOutfitSave(outfitId, false);
      _invalidateSecondaryReads();
      await _reloadOutfitLists(generation);
    });
    return confirmed && _isCurrentSession(generation);
  }

  void _applyConfirmedOutfitSave(String outfitId, bool saved) {
    outfits = [
      for (final row in outfits)
        if (row['id'] == outfitId) {...row, 'is_saved': saved} else row,
    ];
    savedOutfits = [
      for (final row in savedOutfits)
        if (row['id'] != outfitId) row,
    ];
    if (saved) {
      for (final row in outfits) {
        if (row['id'] == outfitId) {
          savedOutfits = [...savedOutfits, row];
          break;
        }
      }
    }
  }

  Future<void> unsaveProduct(String productId) async {
    final generation = _sessionGeneration;
    await _run(() async {
      await api.recordRecommendationEvent(
        eventType: 'unsave',
        productId: productId,
      );
      if (!_isCurrentSession(generation)) return;
      _invalidateSecondaryReads();
      savedProductRows = savedProductRows
          .where(
            (row) => (row['product'] as Map?)?['id']?.toString() != productId,
          )
          .toList();
    });
  }

  Future<void> openVisualAnalysis(BuildContext context) async {
    final generation = _sessionGeneration;
    await _run(() async {
      final res = await api.visualAnalysis();
      if (!_isCurrentSession(generation)) return;
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
    final generation = _sessionGeneration;
    if (_detailViewSent.contains(recommendationId)) return;
    _detailViewSent.add(recommendationId);
    try {
      await api.feedback(recommendationId, 'view_details');
      if (!_isCurrentSession(generation)) return;
      await refreshRemoteData();
    } catch (_) {
      /* не блокируем экран деталей */
    }
  }

  Future<void> sendFeedback(String recommendationId, String eventType) async {
    final generation = _sessionGeneration;
    await _run(() async {
      await api.feedback(recommendationId, eventType);
      if (!_isCurrentSession(generation)) return;
      _invalidateSecondaryReads();
      if (eventType == 'save') savedIds.add(recommendationId);
      if (eventType == 'unsave') savedIds.remove(recommendationId);
      if (eventType == 'like' || eventType == 'dislike') {
        feed = feed.where((item) => item.id != recommendationId).toList();
      }
      await refreshRemoteData();
    });
  }

  Future<void> _reloadOutfitLists(int generation) async {
    final rows = await Future.wait([
      api.outfitsList(),
      api.outfitsList(savedOnly: true),
    ]);
    if (!_isCurrentSession(generation)) return;
    outfits = rows[0];
    savedOutfits = rows[1];
  }

  Future<void> _run(Future<void> Function() block) async {
    if (_disposed) return;
    final generation = _sessionGeneration;
    final operation = Object();
    _busyOperations.add(operation);
    isLoading = true;
    error = null;
    notifyListeners();
    try {
      await block();
    } catch (e) {
      if (_isCurrentSession(generation)) error = ApiClient.formatError(e);
    } finally {
      if (_isCurrentSession(generation)) {
        _busyOperations.remove(operation);
        isLoading = _busyOperations.isNotEmpty;
        notifyListeners();
      }
    }
  }
}

class SplashScreen extends StatelessWidget {
  const SplashScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return const MobileViewport(
      minimalBackdrop: true,
      child: Stack(
        fit: StackFit.expand,
        children: [
          HeroFashionBackdrop(),
          SafeArea(
            child: Align(
              alignment: Alignment.bottomCenter,
              child: Padding(
                padding: EdgeInsets.only(bottom: 48),
                child: SizedBox(
                  width: 22,
                  height: 22,
                  child: CircularProgressIndicator(
                    strokeWidth: 2,
                    color: AppColors.accentSoft,
                  ),
                ),
              ),
            ),
          ),
        ],
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
            body: SafeArea(
              bottom: false,
              child: IndexedStack(index: controller.tab, children: pages),
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
