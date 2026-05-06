import 'dart:async';

import 'package:flutter/material.dart';
import 'core/network/api_client.dart';
import 'core/storage/token_storage.dart';
import 'core/theme/app_colors.dart';
import 'core/widgets/modish_widgets.dart';
import 'features/auth/auth_screen.dart';
import 'features/onboarding/analysis_screen.dart';
import 'features/onboarding/upload_screen.dart';
import 'features/profile/profile_screen.dart';
import 'features/recommendations/detail_screen.dart';
import 'features/recommendations/feed_screen.dart';
import 'features/recommendations/models.dart';
import 'features/recommendations/recommendations_screen.dart';
import 'features/recommendations/saved_screen.dart';
import 'features/visual/visual_analysis_screen.dart';
import 'features/products/models.dart' as prod;
import 'features/outfits/outfits_screen.dart';
import 'features/onboarding/result_screen.dart';

/// Учитывает новый формат profile_json с вложенным `analysis` и старый плоский.
bool _profileHasAnalysis(Map<String, dynamic> pj) {
  final nested = pj['analysis'];
  if (nested is Map) {
    final n = Map<String, dynamic>.from(nested as Map);
    return n.containsKey('color_palette') ||
        n.containsKey('recommended_silhouettes') ||
        n.containsKey('summary');
  }
  return pj.containsKey('color_palette') ||
      pj.containsKey('recommended_silhouettes') ||
      pj.containsKey('summary');
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
        scaffoldBackgroundColor: AppColors.bg,
        colorScheme: ColorScheme.fromSeed(
          seedColor: AppColors.accent,
          surface: AppColors.card,
          primary: AppColors.accent,
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
      AppStage.auth => AuthScreen(controller: app),
      AppStage.upload => UploadScreen(controller: app),
      AppStage.analysis => AnalysisScreen(controller: app),
      AppStage.home => HomeScreen(controller: app),
    };
  }
}

enum AppStage { splash, auth, upload, analysis, home }

class AppController extends ChangeNotifier {
  final api = ApiClient();

  AppStage stage = AppStage.splash;
  /// 0 Подборка, 1 Рекомендации, 2 Сохранённое, 3 Профиль (п.2 ТЗ)
  int tab = 0;
  String email = '';
  String styleTarget = 'menswear';
  String? token;
  String? selectedPhotoPath;
  final photoPaths = <String>[];
  bool isLoading = false;
  String? error;
  int analysisProgress = 0;

  final savedIds = <String>{};
  final _detailViewSent = <String>{};
  List<Outfit> feed = [];
  List<Map<String, dynamic>> savedRows = [];
  List<prod.FeedCard> productFeed = [];
  List<Map<String, dynamic>> savedProductRows = [];
  List<Map<String, dynamic>> outfits = [];
  Map<String, dynamic> summary = {};
  Map<String, dynamic> billing = {};
  String? visualImageUrl;
  Map<String, dynamic> fitProfile = {};
  Map<String, dynamic> tasteProfile = {};

  Outfit? get currentOutfit => feed.isEmpty ? null : feed.first;
  prod.FeedCard? get currentProduct => productFeed.isEmpty ? null : productFeed.first;

  Future<void> boot() async {
    await Future<void>.delayed(const Duration(milliseconds: 200));
    try {
      final stored = await TokenStorage.read().timeout(
        const Duration(seconds: 1),
        onTimeout: () => null,
      );
      if (stored != null && stored.isNotEmpty) {
        api.applyToken(stored);
        token = stored;
        final me = await api
            .usersMe()
            .timeout(const Duration(seconds: 1), onTimeout: () => throw TimeoutException('usersMe'));
        email = '${me['email'] ?? email}';
        final profile = await api.styleProfileMe().timeout(
          const Duration(seconds: 1),
          onTimeout: () => throw TimeoutException('styleProfileMe'),
        );
        final raw = profile['profile_json'];
        final analyzed = raw is Map &&
            Map<String, dynamic>.from(raw as Map).isNotEmpty &&
            _profileHasAnalysis(Map<String, dynamic>.from(raw as Map));
        await refreshRemoteData()
            .timeout(const Duration(seconds: 3), onTimeout: () => throw TimeoutException('refresh'));
        stage = analyzed ? AppStage.home : AppStage.upload;
        notifyListeners();
        return;
      }
    } catch (_) {
      await TokenStorage.clear();
      api.clearToken();
      token = null;
    }
    stage = AppStage.auth;
    notifyListeners();
  }

  Future<void> register(String password) async {
    await _run(() async {
      final t = await api.register(email, password);
      token = t;
      await TokenStorage.write(t);
      // metrics
      try {
        await api.metricsEvent('user_registered');
      } catch (_) {}
      stage = AppStage.upload;
    });
  }

  Future<void> login(String password) async {
    await _run(() async {
      final t = await api.login(email, password);
      token = t;
      await TokenStorage.write(t);
      stage = AppStage.upload;
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
    try {
      feed = await api.feed();
    } catch (_) {
      feed = [];
    }
    try {
      final rows = await api.productFeed(limit: 30);
      productFeed = rows.map(prod.FeedCard.fromApi).toList();
    } catch (_) {
      productFeed = [];
    }
    if (productFeed.isNotEmpty) {
      try {
        await api.metricsEvent('feed_opened');
      } catch (_) {}
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
    } catch (_) {
      outfits = [];
    }
    savedIds
      ..clear()
      ..addAll(
        savedRows.map((r) => '${r['recommendation_id']}'),
      );
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
    // UploadScreen может быть корневым экраном (stage switch),
    // поэтому Navigator.pop() не сработает. Возвращаем пользователя в Home/Profile,
    // если он уже авторизован; иначе — на Auth.
    if (token != null && token!.isNotEmpty) {
      stage = AppStage.home;
      tab = 3; // Профиль
    } else {
      stage = AppStage.auth;
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
    savedProductRows = [];
    outfits = [];
    summary = {};
    billing = {};
    visualImageUrl = null;
    fitProfile = {};
    tasteProfile = {};
    savedIds.clear();
    _detailViewSent.clear();
    stage = AppStage.auth;
    notifyListeners();
  }

  Future<void> sendProductEvent(BuildContext context, String productId, String eventType) async {
    await _run(() async {
      final res = await api.recordRecommendationEvent(eventType: eventType, productId: productId);
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
      if (eventType == 'like' || eventType == 'dislike' || eventType == 'skip') {
        productFeed = productFeed.where((c) => c.product.id != productId).toList();
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

  Future<void> generateOutfitsV2({int count = 3}) async {
    await _run(() async {
      outfits = await api.outfitsGenerate(count: count);
      try {
        await api.metricsEvent('outfit_generated', meta: {'count': count});
      } catch (_) {}
      await refreshRemoteData();
    });
  }

  Future<void> saveOutfit(String outfitId) async {
    await _run(() async {
      await api.outfitsSave(outfitId);
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
          MaterialPageRoute(builder: (_) => VisualAnalysisScreen(controller: this)),
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

class SplashScreen extends StatelessWidget {
  const SplashScreen({super.key});
  @override
  Widget build(BuildContext context) {
    return const MobileViewport(
      child: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Brand(size: 72),
            SizedBox(height: 14),
            Text(
              'Персональный AI-стилист',
              style: TextStyle(color: AppColors.muted, fontSize: 18),
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
    final pages = [
      FeedScreen(controller: controller),
      OutfitsScreen(controller: controller),
      SavedScreen(controller: controller),
      ProfileScreen(controller: controller),
    ];

    return MobileViewport(
      child: Scaffold(
        backgroundColor: Colors.transparent,
        body: pages[controller.tab],
        bottomNavigationBar: NavigationBar(
          selectedIndex: controller.tab,
          onDestinationSelected: controller.setTab,
          destinations: const [
            NavigationDestination(
              icon: Icon(Icons.explore_outlined),
              selectedIcon: Icon(Icons.explore),
              label: 'Подборка',
            ),
            NavigationDestination(
              icon: Icon(Icons.style_outlined),
              selectedIcon: Icon(Icons.style),
              label: 'Образы',
            ),
            NavigationDestination(
              icon: Icon(Icons.bookmark_border),
              selectedIcon: Icon(Icons.bookmark),
              label: 'Сохраненное',
            ),
            NavigationDestination(
              icon: Icon(Icons.person_outline),
              selectedIcon: Icon(Icons.person),
              label: 'Профиль',
            ),
          ],
        ),
      ),
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
