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

/// Учитывает новый формат profile_json с вложенным `analysis` и старый плоский.
bool _profileHasAnalysis(Map<String, dynamic> pj) {
  final nested = pj['analysis'];
  if (nested is Map) {
    final n = Map<String, dynamic>.from(nested as Map);
    return n.containsKey('recommended_colors') || n.containsKey('style_summary');
  }
  return pj.containsKey('recommended_colors') || pj.containsKey('style_summary');
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
  String email = 'alexandra@example.com';
  String styleTarget = 'menswear';
  String? token;
  String? selectedPhotoPath;
  bool isLoading = false;
  String? error;
  int analysisProgress = 0;

  final savedIds = <String>{};
  final _detailViewSent = <String>{};
  List<Outfit> feed = [];
  List<Map<String, dynamic>> savedRows = [];
  Map<String, dynamic> summary = {};
  Map<String, dynamic> billing = {};

  Outfit? get currentOutfit => feed.isEmpty ? null : feed.first;

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
      await api.setStyleTarget(styleTarget);
      await api.analyzeStyleProfile(selectedPhotoPath!);
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
      summary = await api.summary();
    } catch (_) {
      summary = {};
    }
    try {
      savedRows = await api.savedRecommendations();
    } catch (_) {
      savedRows = [];
    }
    try {
      billing = await api.billingStatus();
    } catch (_) {
      billing = {};
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
    notifyListeners();
  }

  void goToReanalyze() {
    selectedPhotoPath = null;
    stage = AppStage.upload;
    notifyListeners();
  }

  Future<void> logout() async {
    await TokenStorage.clear();
    api.clearToken();
    token = null;
    feed = [];
    savedRows = [];
    summary = {};
    billing = {};
    savedIds.clear();
    _detailViewSent.clear();
    stage = AppStage.auth;
    notifyListeners();
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
      RecommendationsScreen(controller: controller),
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
              icon: Icon(Icons.auto_awesome_outlined),
              selectedIcon: Icon(Icons.auto_awesome),
              label: 'Рекомендации',
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
