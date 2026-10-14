// Offline screen audit. Run explicitly; no production endpoint or CDN is used.
// flutter test --no-pub test/new_screen_audit_test.dart --reporter expanded
import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:dio/dio.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/theme/app_theme.dart';
import 'package:modish/features/auth/auth_screen.dart';
import 'package:modish/features/catalog/brands_screen.dart';
import 'package:modish/features/catalog/filters_screen.dart';
import 'package:modish/features/catalog/search_screen.dart';
import 'package:modish/features/catalog/discovery_screen.dart';
import 'package:modish/features/onboarding/analysis_screen.dart';
import 'package:modish/features/onboarding/fit_quiz_screen.dart';
import 'package:modish/features/onboarding/intro_onboarding_screen.dart';
import 'package:modish/features/onboarding/onboarding_v2_screen.dart';
import 'package:modish/features/onboarding/result_screen.dart';
import 'package:modish/features/onboarding/upload_screen.dart';
import 'package:modish/features/onboarding/welcome_screen.dart';
import 'package:modish/features/onboarding/wow_outfit.dart';
import 'package:modish/features/outfits/outfit_detail_screen.dart';
import 'package:modish/features/products/models.dart' as prod;
import 'package:modish/features/products/product_detail_screen.dart';
import 'package:modish/features/profile/edit_profile_screen.dart';
import 'package:modish/features/settings/settings_screen.dart';
import 'package:modish/features/shopping/shopping_task_screen.dart';
import 'package:modish/features/visual/visual_analysis_screen.dart';

import 'app_regressions_test.dart' show FakeApi;
import 'support/load_app_fonts.dart';

const _destination = String.fromEnvironment(
  'AUDIT_OUTPUT',
  defaultValue: 'docs/modish2/ui-audit-20261012',
);
const _photoUrl = 'https://ui-fixture.invalid/blouse.jpg';
const _offlineError =
    'Не удалось загрузить данные. Проверьте подключение и попробуйте снова.';

Map<String, dynamic> _product(
  String id, {
  String? title,
  String slot = 'top',
}) => {
  'id': id,
  'title': title ?? 'Блузка из воздушного хлопка с объёмными рукавами',
  'brand': 'BAON',
  'shop_label': 'BAON',
  'source': 'baon',
  'category': slot == 'bottom'
      ? 'брюки'
      : slot == 'shoes'
      ? 'обувь'
      : 'рубашки',
  'category_name': slot == 'bottom'
      ? 'Брюки'
      : slot == 'shoes'
      ? 'Обувь'
      : 'Рубашки',
  'outfit_slot': slot,
  'price': 4599,
  'old_price': 5999,
  'discount_percent': 23,
  'currency': 'RUB',
  'image_url': _photoUrl,
  'product_url': 'https://merchant.invalid/$id',
  'available_sizes': slot == 'shoes' ? ['38', '39'] : ['M', 'L'],
  'size_system': slot == 'shoes' ? 'EU' : 'LETTER',
  'available_sizes_detailed': [
    for (final size in slot == 'shoes' ? ['38', '39'] : ['M', 'L'])
      {
        'raw_size': size,
        'size_system': slot == 'shoes' ? 'EU' : 'LETTER',
        'availability': 'available',
      },
  ],
  'availability_status': 'in_stock',
  'availability_state': 'available',
  'is_available': true,
  'is_active': true,
  'is_deleted_from_feed': false,
  'gender_target': 'womenswear',
  'colors': ['beige', 'white'],
  'description':
      'Мягкий хлопок и свободный силуэт. Лаконичная вещь для повседневного гардероба.',
  'material': 'cotton',
  'fit': 'relaxed',
  'season': 'all_season',
  'style_tags': ['minimalism', 'casual'],
};

Map<String, dynamic> _outfit(String id, {String scenario = 'daily'}) => {
  'id': id,
  'title': scenario == 'evening' ? 'Вечер в городе' : 'Каждый день',
  'style_direction': scenario,
  'total_price': 13797,
  'reason': 'Мягкие нейтральные цвета и ваш бюджет',
  'explanation': 'Минимализм и ваш размер M. Обувь EU 39 указана в наличии.',
  'products': {
    'top': _product('top'),
    'bottom': _product('bottom', title: 'Брюки прямого кроя', slot: 'bottom'),
    'shoes': _product('shoes', title: 'Лоферы из мягкой кожи', slot: 'shoes'),
  },
};

class _AuditApi extends FakeApi {
  String catalogueState = 'full';
  Completer<Map<String, dynamic>>? catalogueGate;
  @override
  Future<Map<String, dynamic>> catalogueDiscovery({
    int limit = 20,
    String? category,
    String? query,
    String? source,
    int? minPrice,
    int? maxPrice,
    String? cursor,
  }) async {
    if (catalogueState == 'error') throw StateError('offline fixture');
    return catalogueGate?.future ??
        {
          'mode': 'catalogue_discovery',
          'personalized': false,
          'size_verification': 'not_verified',
          'has_more': false,
          'next_cursor': null,
          'products': catalogueState == 'empty'
              ? <Map<String, dynamic>>[]
              : [
                  for (var i = 0; i < 6; i++)
                    _discoveryProduct(
                      'catalogue-$i',
                      role: category == 'shoes'
                          ? 'shoes'
                          : category == 'outerwear'
                          ? 'outerwear'
                          : i.isEven
                          ? 'top'
                          : 'shoes',
                    ),
                ],
        };
  }

  @override
  Future<Map<String, dynamic>> catalogueDiscoveryProduct(String id) async {
    if (detailFails) throw StateError('offline fixture');
    return _discoveryProduct(id, role: 'shoes');
  }

  int swipeRated = 0;
  bool swipeCanContinue = false;
  bool searchFails = false;
  bool emptySearch = false;
  bool detailFails = false;
  Completer<List<Map<String, dynamic>>>? searchGate;
  @override
  Future<Map<String, dynamic>> styleSwipeDeck() async => {
    'products': <Map<String, dynamic>>[],
    'rated': swipeRated,
    'minimum': 15,
    'target': 20,
    'can_continue': swipeCanContinue,
  };
  @override
  Future<List<Map<String, dynamic>>> productsList({
    int limit = 50,
    int offset = 0,
    String? category,
    String? source,
    String? brand,
    String? query,
  }) async {
    if (searchFails) throw StateError('offline fixture');
    return searchGate?.future ??
        (emptySearch
            ? []
            : [
                _product('shirt'),
                _product(
                  'trousers',
                  title: 'Брюки из мягкой шерсти',
                  slot: 'bottom',
                ),
              ]);
  }

  @override
  Future<Map<String, dynamic>> productById(String id) async {
    if (detailFails) {
      throw DioException(
        requestOptions: RequestOptions(path: '/offline-fixture'),
        type: DioExceptionType.connectionError,
      );
    }
    return _product(id);
  }

  @override
  Future<List<Map<String, dynamic>>> productsBrands({int limit = 80}) async => [
    {'brand': 'BAON', 'count': 42},
    {'brand': 'Serginnetti', 'count': 28},
  ];
  @override
  Future<Map<String, dynamic>> profileBrief() async => {
    'stats': {'events': 21, 'saved': 2},
  };
}

AppController _controller() {
  final api = _AuditApi()
    ..complements = [
      _product('bottom', title: 'Брюки прямого кроя', slot: 'bottom'),
      _product('shoes', title: 'Лоферы из мягкой кожи', slot: 'shoes'),
    ];
  return AppController(apiClient: api)
    ..token = 'offline-fixture-access-token'
    ..email = 'anna@example.test'
    ..stage = AppStage.home
    ..activationStatus = {
      'age_group': '25-34',
      'clothing_size': 'M',
      'shoe_size': '39',
      'height': 170,
      'price_segment': 'mid',
      'style_preferences': ['casual', 'minimalism'],
      'recommendation_features': [
        'context_v1',
        'shopping_intent_v1',
        'catalogue_discovery_v1',
      ],
    }
    ..fitProfile = {
      'gender_target': 'womenswear',
      'height': 170,
      'clothing_size': 'M',
      'shoe_size': '39',
      'budget_max': 25000,
      'interest_categories': ['рубашки', 'брюки', 'обувь'],
      'style_scenarios': ['daily', 'office'],
      'size_profile': {
        'height_confirmed': true,
        'sizes': {
          for (final slot in ['top', 'bottom', 'one_piece'])
            slot: {'state': 'confirmed', 'system': 'LETTER', 'value': 'M'},
          'shoes': {'state': 'confirmed', 'system': 'EU', 'value': '39'},
        },
      },
    }
    ..tasteProfile = {
      'weights': {
        'styles': {'casual': 0.8, 'minimalism': 0.6},
        'colors': {'beige': 0.8, 'white': 0.4},
      },
    }
    ..productFeed = [
      prod.FeedCard.fromApi({
        'product': _product('shirt'),
        'reasons': ['Размер M указан в наличии', 'Цена в вашем бюджете'],
      }),
    ]
    ..savedProductRows = [
      {'product': _product('saved')},
    ]
    ..outfits = [_outfit('daily'), _outfit('evening', scenario: 'evening')]
    ..savedOutfits = [_outfit('saved-look')];
}

/// All image requests terminate here. This transport never opens a socket.
class _FixtureHttp extends HttpOverrides {
  _FixtureHttp(this.bytes);
  final List<int> bytes;
  final requests = <String>[];
  @override
  HttpClient createHttpClient(SecurityContext? context) =>
      _FixtureClient(bytes, requests);
}

class _FixtureClient implements HttpClient {
  _FixtureClient(this.bytes, this.requests);
  final List<int> bytes;
  final List<String> requests;
  @override
  Future<HttpClientRequest> openUrl(String method, Uri url) async {
    // A missing FakeAPI override cannot silently reach an API through this stub.
    if (url.host != 'ui-fixture.invalid') {
      throw StateError(
        'Unexpected non-fixture HTTP request: ${url.host}${url.path}',
      );
    }
    requests.add(url.toString());
    return _FixtureRequest(bytes);
  }

  @override
  Future<HttpClientRequest> getUrl(Uri url) => openUrl('GET', url);
  @override
  dynamic noSuchMethod(Invocation invocation) => null;
}

class _FixtureHeaders implements HttpHeaders {
  @override
  void forEach(void Function(String, List<String>) action) =>
      action('content-type', ['image/jpeg']);
  @override
  String? value(String name) =>
      name.toLowerCase() == 'content-type' ? 'image/jpeg' : null;
  @override
  dynamic noSuchMethod(Invocation invocation) => null;
}

class _FixtureRequest implements HttpClientRequest {
  _FixtureRequest(this.bytes);
  final List<int> bytes;
  @override
  final HttpHeaders headers = _FixtureHeaders();
  @override
  bool followRedirects = true;
  @override
  int maxRedirects = 8;
  @override
  bool persistentConnection = true;
  @override
  Future<HttpClientResponse> close() async => _FixtureResponse(bytes);
  @override
  void abort([Object? exception, StackTrace? stackTrace]) {}
  @override
  dynamic noSuchMethod(Invocation invocation) => null;
}

class _FixtureResponse extends Stream<List<int>> implements HttpClientResponse {
  _FixtureResponse(this.bytes);
  final List<int> bytes;
  @override
  int get statusCode => 200;
  @override
  int get contentLength => bytes.length;
  @override
  String get reasonPhrase => 'OK';
  @override
  bool get isRedirect => false;
  @override
  List<RedirectInfo> get redirects => [];
  @override
  HttpClientResponseCompressionState get compressionState =>
      HttpClientResponseCompressionState.notCompressed;
  @override
  final HttpHeaders headers = _FixtureHeaders();
  @override
  StreamSubscription<List<int>> listen(
    void Function(List<int>)? onData, {
    Function? onError,
    void Function()? onDone,
    bool? cancelOnError,
  }) => Stream.value(bytes).listen(
    onData,
    onError: onError,
    onDone: onDone,
    cancelOnError: cancelOnError,
  );
  @override
  dynamic noSuchMethod(Invocation invocation) => null;
}

class _Case {
  const _Case(
    this.name,
    this.build, {
    this.prepare,
    this.interact,
    this.largeText = false,
    this.keyboard = false,
  });
  final String name;
  final Widget Function(AppController) build;
  final void Function(AppController)? prepare;
  final Future<void> Function(WidgetTester)? interact;
  final bool largeText;
  final bool keyboard;
}

List<_Case> _cases() => [
  _Case(
    'catalogue-full',
    (c) => DiscoveryScreen(controller: c),
    largeText: true,
  ),
  _Case(
    'catalogue-shoes',
    (c) => DiscoveryScreen(controller: c, initialCategory: 'shoes'),
    largeText: true,
  ),
  _Case(
    'catalogue-outerwear',
    (c) => DiscoveryScreen(controller: c, initialCategory: 'outerwear'),
    largeText: true,
  ),
  _Case(
    'catalogue-bottom',
    (c) => DiscoveryScreen(controller: c),
    interact: _scrollBottom,
    largeText: true,
  ),
  _Case(
    'catalogue-empty',
    (c) => DiscoveryScreen(controller: c),
    prepare: (c) => (c.api as _AuditApi).catalogueState = 'empty',
    largeText: true,
  ),
  _Case(
    'catalogue-error',
    (c) => DiscoveryScreen(controller: c),
    prepare: (c) => (c.api as _AuditApi).catalogueState = 'error',
    largeText: true,
  ),
  _Case(
    'catalogue-loading',
    (c) => DiscoveryScreen(controller: c),
    prepare: (c) => (c.api as _AuditApi).catalogueGate = Completer(),
  ),
  _Case(
    'catalogue-keyboard',
    (c) => DiscoveryScreen(controller: c),
    keyboard: true,
    interact: (t) async {
      await t.tap(find.byKey(const ValueKey('catalogue-query')));
      await t.enterText(
        find.byKey(const ValueKey('catalogue-query')),
        'лоферы',
      );
      await t.pump(const Duration(milliseconds: 500));
    },
    largeText: true,
  ),
  _Case(
    'catalogue-detail',
    (c) => ProductDetailScreen(
      controller: c,
      card: prod.FeedCard(
        product: prod.Product.fromApi(
          _discoveryProduct('catalogue-detail', role: 'shoes'),
        ),
        reason: '',
      ),
      eventMeta: const {'surface': 'catalogue_discovery'},
    ),
    largeText: true,
  ),
  _Case(
    'catalogue-detail-bottom',
    (c) => ProductDetailScreen(
      controller: c,
      card: prod.FeedCard(
        product: prod.Product.fromApi(
          _discoveryProduct('catalogue-detail', role: 'shoes'),
        ),
        reason: '',
      ),
      eventMeta: const {'surface': 'catalogue_discovery'},
    ),
    interact: _scrollBottom,
    largeText: true,
  ),
  _Case('splash', (_) => const SplashScreen()),
  _Case('welcome', (c) => WelcomeScreen(controller: c), largeText: true),
  _Case('intro', (c) => IntroOnboardingScreen(controller: c), largeText: true),
  _Case('auth-register', (c) => AuthScreen(controller: c), largeText: true),
  _Case(
    'auth-login-error',
    (c) => AuthScreen(controller: c),
    prepare: (c) {
      c.authRegisterMode = false;
      c.error = _offlineError;
    },
    largeText: true,
  ),
  _Case(
    'auth-keyboard',
    (c) => AuthScreen(controller: c),
    keyboard: true,
    interact: (t) async {
      await t.tap(find.byType(TextField).first);
    },
  ),
  _Case(
    'auth-loading',
    (c) => AuthScreen(controller: c),
    prepare: (c) => c.isLoading = true,
  ),
  _Case('fit-quiz', (c) => FitQuizScreen(controller: c), largeText: true),
  _Case(
    'fit-confirmation',
    (c) => EditProfileScreen(controller: c, setupMode: true),
    largeText: true,
  ),
  _Case(
    'fit-confirmation-bottom',
    (c) => EditProfileScreen(controller: c, setupMode: true),
    interact: _scrollBottom,
    largeText: true,
  ),
  _Case(
    'onboarding-preferences',
    (c) => OnboardingV2Screen(controller: c),
    largeText: true,
  ),
  _Case(
    'onboarding-preferences-bottom',
    (c) => OnboardingV2Screen(controller: c),
    interact: _scrollBottom,
    largeText: true,
  ),
  _Case(
    'style-swipe-full',
    (c) => OnboardingV2Screen(controller: c),
    prepare: (c) {
      c.onboardingV2SubStep = 1;
      c.styleSwipeCards = [prod.Product.fromApi(_product('swipe'))];
      c.styleSwipeRated = 6;
    },
    largeText: true,
  ),
  _Case(
    'style-swipe-empty',
    (c) => OnboardingV2Screen(controller: c),
    prepare: (c) {
      c.onboardingV2SubStep = 1;
    },
    largeText: true,
  ),
  _Case(
    'style-swipe-loading',
    (c) => OnboardingV2Screen(controller: c),
    prepare: (c) {
      c.onboardingV2SubStep = 1;
      c.isLoading = true;
    },
  ),
  _Case(
    'style-swipe-complete',
    (c) => OnboardingV2Screen(controller: c),
    prepare: (c) {
      c.onboardingV2SubStep = 1;
      c.styleSwipeRated = 20;
      c.styleSwipeCanContinue = true;
      (c.api as _AuditApi).swipeRated = 20;
      (c.api as _AuditApi).swipeCanContinue = true;
    },
    largeText: true,
  ),
  _Case(
    'wow-loading',
    (c) => OnboardingV2Screen(controller: c),
    prepare: (c) => c.onboardingV2SubStep = 4,
  ),
  _Case(
    'wow-empty',
    (c) => OnboardingV2Screen(controller: c),
    prepare: (c) => c.onboardingV2SubStep = 5,
    largeText: true,
  ),
  _Case(
    'wow-full',
    (c) => OnboardingV2Screen(controller: c),
    prepare: (c) {
      c.onboardingV2SubStep = 5;
      c.wowOutfits = [WowOutfit.fromApi(_outfit('wow'))];
    },
    largeText: true,
  ),
  _Case('feed-full', (c) => HomeScreen(controller: c), largeText: true),
  _Case(
    'feed-empty',
    (c) => HomeScreen(controller: c),
    prepare: (c) => c.productFeed = [],
    largeText: true,
  ),
  _Case(
    'feed-error',
    (c) => HomeScreen(controller: c),
    prepare: (c) {
      c.productFeed = [];
      c.productFeedError = _offlineError;
    },
    largeText: true,
  ),
  _Case(
    'feed-loading',
    (c) => HomeScreen(controller: c),
    prepare: (c) {
      c.productFeed = [];
      c.feedRefreshing = true;
    },
  ),
  _Case(
    'feed-reasons',
    (c) => HomeScreen(controller: c),
    interact: (t) async {
      await t.ensureVisible(find.text('Почему это вам'));
      await t.tap(find.text('Почему это вам'));
    },
    largeText: true,
  ),
  _Case(
    'feed-dislike',
    (c) => HomeScreen(controller: c),
    interact: (t) async {
      await t.ensureVisible(find.byTooltip('Не моё'));
      await t.tap(find.byTooltip('Не моё'));
    },
    largeText: true,
  ),
  _Case(
    'outfits-full',
    (c) => HomeScreen(controller: c),
    prepare: (c) => c.tab = 1,
    largeText: true,
  ),
  _Case(
    'outfits-empty',
    (c) => HomeScreen(controller: c),
    prepare: (c) {
      c.tab = 1;
      c.outfits = [];
    },
    largeText: true,
  ),
  _Case(
    'outfit-detail',
    (c) => OutfitDetailScreen(controller: c, outfit: _outfit('detail')),
    largeText: true,
  ),
  _Case(
    'outfit-detail-bottom',
    (c) => OutfitDetailScreen(controller: c, outfit: _outfit('detail')),
    interact: _scrollBottom,
    largeText: true,
  ),
  _Case(
    'saved-full',
    (c) => HomeScreen(controller: c),
    prepare: (c) => c.tab = 2,
    largeText: true,
  ),
  _Case(
    'saved-outfits',
    (c) => HomeScreen(controller: c),
    prepare: (c) {
      c.tab = 2;
      c.savedProductRows = [];
    },
    largeText: true,
  ),
  _Case(
    'saved-empty',
    (c) => HomeScreen(controller: c),
    prepare: (c) {
      c.tab = 2;
      c.savedProductRows = [];
      c.savedOutfits = [];
    },
    largeText: true,
  ),
  _Case(
    'profile-full',
    (c) => HomeScreen(controller: c),
    prepare: (c) => c.tab = 3,
    largeText: true,
  ),
  _Case(
    'profile-bottom',
    (c) => HomeScreen(controller: c),
    prepare: (c) => c.tab = 3,
    interact: _scrollBottom,
    largeText: true,
  ),
  _Case(
    'profile-no-taste',
    (c) => HomeScreen(controller: c),
    prepare: (c) {
      c.tab = 3;
      c.tasteProfile = {};
      c.activationStatus = {};
    },
    largeText: true,
  ),
  _Case(
    'profile-parameters',
    (c) => EditProfileScreen(controller: c),
    largeText: true,
  ),
  _Case(
    'profile-parameters-bottom',
    (c) => EditProfileScreen(controller: c),
    interact: _scrollBottom,
    largeText: true,
  ),
  _Case(
    'search-empty-input',
    (c) => SearchScreen(controller: c),
    largeText: true,
  ),
  _Case(
    'search-results',
    (c) => SearchScreen(controller: c),
    interact: _search,
    largeText: true,
  ),
  _Case(
    'search-no-results',
    (c) => SearchScreen(controller: c),
    prepare: (c) => (c.api as _AuditApi).emptySearch = true,
    interact: _search,
    largeText: true,
  ),
  _Case(
    'search-error',
    (c) => SearchScreen(controller: c),
    prepare: (c) => (c.api as _AuditApi).searchFails = true,
    interact: _search,
    largeText: true,
  ),
  _Case(
    'search-loading',
    (c) => SearchScreen(controller: c),
    prepare: (c) => (c.api as _AuditApi).searchGate = Completer(),
    interact: _search,
  ),
  _Case(
    'search-keyboard',
    (c) => SearchScreen(controller: c),
    keyboard: true,
    largeText: true,
  ),
  _Case('filters-top', (c) => FiltersScreen(controller: c), largeText: true),
  _Case(
    'filters-bottom',
    (c) => FiltersScreen(controller: c),
    interact: _scrollBottom,
    largeText: true,
  ),
  _Case('task-new', (c) => ShoppingTaskScreen(controller: c), largeText: true),
  _Case(
    'task-keyboard',
    (c) => ShoppingTaskScreen(controller: c),
    keyboard: true,
    largeText: true,
    interact: (t) async {
      await t.tap(find.byType(TextField).first);
    },
  ),
  _Case(
    'task-products',
    (c) => ShoppingTaskScreen(controller: c),
    prepare: (c) => c.shoppingTask.result = _taskResult(outfit: false),
    largeText: true,
  ),
  _Case(
    'task-products-bottom',
    (c) => ShoppingTaskScreen(controller: c),
    prepare: (c) => c.shoppingTask.result = _taskResult(outfit: false),
    interact: _scrollBottom,
    largeText: true,
  ),
  _Case(
    'task-outfit',
    (c) => ShoppingTaskScreen(controller: c),
    prepare: (c) => c.shoppingTask.result = _taskResult(outfit: true),
    largeText: true,
  ),
  _Case(
    'task-outfit-bottom',
    (c) => ShoppingTaskScreen(controller: c),
    prepare: (c) => c.shoppingTask.result = _taskResult(outfit: true),
    interact: _scrollBottom,
    largeText: true,
  ),
  _Case(
    'task-error',
    (c) => ShoppingTaskScreen(controller: c),
    prepare: (c) => c.shoppingTask.error = _offlineError,
    largeText: true,
  ),
  _Case(
    'task-loading',
    (c) => ShoppingTaskScreen(controller: c),
    prepare: (c) => c.shoppingTask.busy = true,
  ),
  _Case(
    'task-clarification',
    (c) => ShoppingTaskScreen(controller: c),
    prepare: (c) => c.shoppingTask.result = {
      'id': 'preview-task',
      'text': 'Что-то красивое',
      'state': 'needs_clarification',
      'version': 1,
      'constraints': {},
      'products': [],
      'outfits': [],
      'message': 'Уточните, какую вещь или образ вы ищете.',
    },
    largeText: true,
  ),
  _Case(
    'task-no-results',
    (c) => ShoppingTaskScreen(controller: c),
    prepare: (c) => c.shoppingTask.result = {
      ..._taskResult(outfit: false),
      'state': 'empty',
      'products': [],
      'message':
          'Пока нет вещей, которые соответствуют всем условиям. Попробуйте изменить запрос.',
    },
    largeText: true,
  ),
  _Case(
    'product-detail',
    (c) => ProductDetailScreen(
      controller: c,
      card: prod.FeedCard(
        product: prod.Product.fromApi(_product('shirt')),
        reason: '',
        reasons: ['Размер M указан в наличии', 'Цена в вашем бюджете'],
      ),
    ),
    largeText: true,
  ),
  _Case(
    'product-detail-bottom',
    (c) => ProductDetailScreen(
      controller: c,
      card: prod.FeedCard(
        product: prod.Product.fromApi(_product('shirt')),
        reason: '',
      ),
    ),
    interact: _scrollBottom,
    largeText: true,
  ),
  _Case(
    'product-detail-error',
    (c) => ProductDetailScreen(
      controller: c,
      card: prod.FeedCard(
        product: prod.Product.fromApi(_product('shirt')),
        reason: '',
      ),
    ),
    prepare: (c) => (c.api as _AuditApi).detailFails = true,
    largeText: true,
  ),
  _Case('brands', (c) => BrandsScreen(controller: c), largeText: true),
  _Case('settings', (c) => SettingsScreen(controller: c), largeText: true),
  _Case(
    'settings-bottom',
    (c) => SettingsScreen(controller: c),
    interact: _scrollBottom,
    largeText: true,
  ),
  _Case('help', (_) => const HelpScreen(), largeText: true),
  _Case(
    'help-bottom',
    (_) => const HelpScreen(),
    interact: _scrollBottom,
    largeText: true,
  ),
  _Case('photo-empty', (c) => UploadScreen(controller: c), largeText: true),
  _Case(
    'photo-selected',
    (c) => UploadScreen(controller: c),
    prepare: (c) => c.selectedPhotoPath = 'test/fixtures/build12-blouse.jpg',
    largeText: true,
  ),
  _Case(
    'photo-selected-bottom',
    (c) => UploadScreen(controller: c),
    prepare: (c) => c.selectedPhotoPath = 'test/fixtures/build12-blouse.jpg',
    interact: _scrollBottom,
    largeText: true,
  ),
  _Case(
    'photo-error',
    (c) => UploadScreen(controller: c),
    prepare: (c) => c.error = _offlineError,
    interact: _scrollBottom,
    largeText: true,
  ),
  _Case(
    'photo-analysis',
    (c) => AnalysisScreen(controller: c),
    prepare: (c) => c.analysisProgress = 45,
    largeText: true,
  ),
  _Case('photo-result', (c) => ResultScreen(controller: c), largeText: true),
  _Case(
    'visual-empty',
    (c) => VisualAnalysisScreen(controller: c),
    largeText: true,
  ),
  _Case(
    'visual-full',
    (c) => VisualAnalysisScreen(controller: c),
    prepare: (c) => c.visualImageUrl =
        'data:image/jpeg;base64,${base64Encode(File('test/fixtures/build12-blouse.jpg').readAsBytesSync())}',
    largeText: true,
  ),
];

Map<String, dynamic> _discoveryProduct(String id, {String role = 'top'}) => {
  ..._product(id, slot: role),
  // Distinct simulated offers need distinct image identities; the bytes stay
  // the same local layout fixture and are never fetched over the network.
  'image_url': 'https://ui-fixture.invalid/$id.jpg',
  'title': role == 'shoes'
      ? 'Лоферы из мягкой кожи'
      : role == 'outerwear'
      ? 'Куртка с высоким воротником'
      : 'Блузка из мягкого хлопка',
  'category': role == 'outerwear'
      ? 'верхняя одежда'
      : role == 'shoes'
      ? 'обувь'
      : 'рубашки',
  'catalogue_discovery': true,
  'size_verification': 'not_verified',
  'size_notice': 'Размер нужно уточнить у магазина',
  'size_system': null,
  'merchant_sizes': [
    {
      'raw_size': role == 'shoes' ? '38' : 'M',
      'normalized_size': null,
      'size_system': 'UNKNOWN',
      'availability': 'unknown',
    },
    {
      'raw_size': role == 'shoes' ? '39' : 'L',
      'normalized_size': null,
      'size_system': 'UNKNOWN',
      'availability': 'unavailable',
    },
  ],
};

Map<String, dynamic> _taskResult({required bool outfit}) => {
  'id': 'preview-task',
  'text': outfit
      ? 'Образ в офис до 20 000 ₽ без каблуков'
      : 'Блузка в офис до 7 000 ₽',
  'version': 1,
  'state': 'ready',
  'constraints': {
    'budget_max': outfit ? 20000 : 7000,
    'occasion': 'office',
    'requested_categories': ['рубашки'],
    'intent_type': outfit ? 'outfit' : 'items',
  },
  'products': outfit
      ? []
      : [
          {
            'product': _product('task-shirt'),
            'reasons': ['Размер M указан в наличии', 'Цена в пределах 7 000 ₽'],
          },
        ],
  'outfits': outfit ? [_outfit('task-look')] : [],
};

Future<void> _scrollBottom(WidgetTester tester) async {
  final scrollable = find.byType(Scrollable).hitTestable().first;
  for (var i = 0; i < 12; i++) {
    await tester.drag(scrollable, const Offset(0, -420));
    await tester.pump(const Duration(milliseconds: 100));
  }
}

Future<void> _search(WidgetTester tester) async {
  await tester.enterText(find.byType(TextField).first, 'блузка');
  await tester.pump(const Duration(milliseconds: 500));
}

void main() {
  testWidgets(
    'Offline audit of every app screen, insets, keyboard and large text',
    (tester) async {
      final oldHttp = HttpOverrides.current;
      final oldError = FlutterError.onError;
      final findings = <Map<String, dynamic>>[];
      late _FixtureHttp fixtureHttp;
      await tester.runAsync(() async {
        fixtureHttp = _FixtureHttp(
          await File('test/fixtures/build12-blouse.jpg').readAsBytes(),
        );
        HttpOverrides.global = fixtureHttp;
        await loadAppFonts();
      });
      addTearDown(() {
        HttpOverrides.global = oldHttp;
        FlutterError.onError = oldError;
      });
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      addTearDown(tester.view.resetPadding);
      addTearDown(tester.view.resetViewPadding);
      addTearDown(tester.view.resetViewInsets);
      final theme = AppTheme.dark;
      await tester.pumpWidget(
        MaterialApp(theme: theme, home: const SizedBox()),
      );
      await tester.runAsync(() async {
        final context = tester.element(find.byType(SizedBox).last);
        await precacheImage(
          FileImage(File('test/fixtures/build12-blouse.jpg')),
          context,
        ).timeout(const Duration(seconds: 5));
        await precacheImage(
          const AssetImage('assets/images/back.png'),
          context,
        ).timeout(const Duration(seconds: 5));
        await precacheImage(
          const AssetImage('assets/images/logo.png'),
          context,
        ).timeout(const Duration(seconds: 5));
      });
      const selectedScreens = String.fromEnvironment('AUDIT_SCREEN');
      for (final device in [const Size(360, 640), const Size(390, 844)]) {
        tester.view.physicalSize = device;
        tester.view.devicePixelRatio = 1;
        tester.view.padding = const FakeViewPadding(top: 24, bottom: 48);
        tester.view.viewPadding = const FakeViewPadding(top: 24, bottom: 48);
        for (final entry in _cases()) {
          if (selectedScreens.isNotEmpty &&
              !selectedScreens.split(',').any(entry.name.startsWith)) {
            continue;
          }
          for (final scale in [
            1.0,
            if (entry.largeText && device.width == 360) 1.3,
          ]) {
            final c = _controller();
            entry.prepare?.call(c);
            final errors = <String>[];
            final captureErrors = <String>[];
            FlutterError.onError = (details) =>
                errors.add(details.exceptionAsString());
            tester.view.viewInsets = FakeViewPadding(
              bottom: entry.keyboard ? 280 : 0,
            );
            final key = GlobalKey();
            final filename =
                '${entry.name}-${device.width.toInt()}${scale > 1 ? '-text130' : ''}.png';
            try {
              await tester.pumpWidget(
                RepaintBoundary(
                  key: key,
                  child: MaterialApp(
                    debugShowCheckedModeBanner: false,
                    theme: theme,
                    builder: (context, child) => MediaQuery(
                      data: MediaQuery.of(
                        context,
                      ).copyWith(textScaler: TextScaler.linear(scale)),
                      child: child!,
                    ),
                    home: entry.build(c),
                  ),
                ),
              );
              await _boundedPump(tester);
              await entry.interact?.call(tester);
              await _boundedPump(tester);
              // Flutter tester's engine uses the square Ahem font for null
              // families. Substitute the same bundled Inter font only for
              // diagnostic PNG readability;
              // widget/interaction errors above remain unmodified evidence.
              FlutterError.onError = (details) =>
                  captureErrors.add(details.exceptionAsString());
              _substituteCaptureFont(tester);
              await tester.pump();
              await tester.runAsync(() async {
                final image =
                    await (key.currentContext!.findRenderObject()
                            as RenderRepaintBoundary)
                        .toImage(pixelRatio: 1);
                final bytes = await image.toByteData(
                  format: ui.ImageByteFormat.png,
                );
                final file = File('$_destination/$filename');
                await file.parent.create(recursive: true);
                await file.writeAsBytes(bytes!.buffer.asUint8List());
                image.dispose();
              });
            } catch (error) {
              errors.add(error.toString());
            }
            findings.add({
              'screen': entry.name,
              'width': device.width.toInt(),
              'height': device.height.toInt(),
              'text_scale': scale,
              'system_navigation_inset': 48,
              'keyboard_inset': entry.keyboard ? 280 : 0,
              'image': filename,
              'errors': errors,
              'capture_font_errors': captureErrors,
            });
            await tester.pumpWidget(const SizedBox());
            await tester.pump();
            c.dispose();
            debugPrint('AUDIT $filename errors=${errors.length}');
          }
        }
      }
      FlutterError.onError = oldError;
      final failures = findings
          .where((f) => (f['errors'] as List).isNotEmpty)
          .toList();
      await _contactSheets(tester, findings, theme);
      await tester.runAsync(() async {
        await File('$_destination/audit-results.json').writeAsString(
          const JsonEncoder.withIndent('  ').convert({
            'generated_at': DateTime.now().toUtc().toIso8601String(),
            'transport': 'in-memory fixture HttpClient, no sockets',
            'photography_limit':
                'One local blouse fixture is reused only for layout; this is not catalogue or outfit-quality validation.',
            'typography_limit':
                'Diagnostic screenshots use bundled production Inter/PlayfairDisplay assets. Null RenderParagraph font family is substituted with the same bundled Inter before capture to avoid Flutter tester Ahem squares. Explicit font families remain unchanged. This is not physical-device rasterization evidence; widget/interaction errors are collected before substitution.',
            'screenshots': findings.length,
            'screens_with_framework_errors': failures.length,
            'fixture_http_requests': fixtureHttp.requests.length,
            'cases': findings,
          }),
        );
      });
      expect(
        failures,
        isEmpty,
        reason:
            'See $_destination/audit-results.json for per-screen diagnostics.',
      );
    },
    timeout: const Timeout(Duration(minutes: 8)),
  );
}

void _substituteCaptureFont(WidgetTester tester) {
  for (final object in tester.allRenderObjects) {
    if (object is! RenderParagraph || object.text is! TextSpan) continue;
    final span = object.text as TextSpan;
    if (span.style?.fontFamily != null) continue;
    object.text = TextSpan(
      text: span.text,
      children: span.children,
      style: (span.style ?? const TextStyle()).copyWith(fontFamily: 'Inter'),
      recognizer: span.recognizer,
      mouseCursor: span.mouseCursor,
      onEnter: span.onEnter,
      onExit: span.onExit,
      semanticsLabel: span.semanticsLabel,
      locale: span.locale,
      spellOut: span.spellOut,
    );
  }
}

Future<void> _contactSheets(
  WidgetTester tester,
  List<Map<String, dynamic>> findings,
  ThemeData theme,
) async {
  tester.view.physicalSize = const Size(840, 1056);
  tester.view.devicePixelRatio = 1;
  tester.view.padding = const FakeViewPadding();
  tester.view.viewPadding = const FakeViewPadding();
  tester.view.viewInsets = const FakeViewPadding();
  for (var offset = 0; offset < findings.length; offset += 12) {
    final slice = findings.skip(offset).take(12).toList();
    final decoded = <ui.Image>[];
    await tester.runAsync(() async {
      for (final entry in slice) {
        final data = await File(
          '$_destination/${entry['image']}',
        ).readAsBytes();
        final codec = await ui.instantiateImageCodec(data);
        decoded.add((await codec.getNextFrame()).image);
        codec.dispose();
      }
    });
    final key = GlobalKey();
    await tester.pumpWidget(
      RepaintBoundary(
        key: key,
        child: MaterialApp(
          debugShowCheckedModeBanner: false,
          theme: theme,
          home: Scaffold(
            body: GridView.count(
              crossAxisCount: 4,
              childAspectRatio: 210 / 352,
              padding: EdgeInsets.zero,
              physics: const NeverScrollableScrollPhysics(),
              children: [
                for (var i = 0; i < slice.length; i++)
                  Padding(
                    padding: const EdgeInsets.all(5),
                    child: Column(
                      children: [
                        Text(
                          '${slice[i]['image']}',
                          style: const TextStyle(fontSize: 9),
                          maxLines: 2,
                          textAlign: TextAlign.center,
                        ),
                        const SizedBox(height: 4),
                        Expanded(
                          child: RawImage(
                            image: decoded[i],
                            fit: BoxFit.contain,
                          ),
                        ),
                      ],
                    ),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
    await tester.pump();
    await tester.runAsync(() async {
      final image =
          await (key.currentContext!.findRenderObject()
                  as RenderRepaintBoundary)
              .toImage();
      final data = await image.toByteData(format: ui.ImageByteFormat.png);
      await File(
        '$_destination/contact-${(offset ~/ 12 + 1).toString().padLeft(2, '0')}.png',
      ).writeAsBytes(data!.buffer.asUint8List());
      image.dispose();
    });
    await tester.pumpWidget(const SizedBox());
    for (final image in decoded) {
      image.dispose();
    }
  }
}

/// No pumpAndSettle: loading/visibility/image animations may never settle.
Future<void> _boundedPump(WidgetTester tester) async {
  await tester.pump(const Duration(milliseconds: 200));
  await tester.runAsync(
    () => Future<void>.delayed(const Duration(milliseconds: 80)),
  );
  await tester.pump(const Duration(milliseconds: 200));
}
