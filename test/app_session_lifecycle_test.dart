import 'dart:async';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';

import 'app_regressions_test.dart' show FakeApi, product;

class LifecycleApi extends FakeApi {
  int disposeCalls = 0;
  @override
  void dispose() {
    disposeCalls++;
    super.dispose();
  }

  Completer<Map<String, dynamic>>? summaryGate;
  Completer<Map<String, dynamic>>? fitGate;
  Completer<List<Map<String, dynamic>>>? savedGate;
  Completer<String>? loginGate;
  Completer<Map<String, dynamic>>? onboardingGate;
  final onboardingStarted = Completer<void>();
  final mutations = <Completer<Map<String, dynamic>>>[];
  int summaryCalls = 0;
  bool failReads = false;

  @override
  Future<Map<String, dynamic>> summary() async {
    summaryCalls++;
    if (failReads) throw StateError('offline');
    return summaryGate?.future ?? {'owner': 'current'};
  }

  @override
  Future<Map<String, dynamic>> fitProfileMe() async {
    if (failReads) throw StateError('offline');
    return fitGate?.future ?? {'owner': 'current'};
  }

  @override
  Future<List<Map<String, dynamic>>> savedProducts() async {
    if (failReads) throw StateError('offline');
    return savedGate?.future ?? [];
  }

  @override
  Future<Map<String, dynamic>> tasteProfileMe() async {
    if (failReads) throw StateError('offline');
    return {'owner': 'current'};
  }

  @override
  Future<List<Map<String, dynamic>>> outfitsList({
    String? scenario,
    bool savedOnly = false,
  }) async {
    if (failReads) throw StateError('offline');
    return [];
  }

  @override
  Future<Map<String, dynamic>> tasteProfilePatch({
    int? priceMin,
    int? priceMax,
    String? preferredFit,
  }) {
    final pending = Completer<Map<String, dynamic>>();
    mutations.add(pending);
    return pending.future;
  }

  @override
  Future<void> logoutSession() async => clearToken();

  @override
  Future<bool> pingHealth() async => true;

  @override
  Future<String> login(String email, String password) => loginGate!.future;
  @override
  Future<String> register(String email, String password) => loginGate!.future;
  @override
  Future<Map<String, dynamic>> onboardingStatus() async {
    if (!onboardingStarted.isCompleted) onboardingStarted.complete();
    return onboardingGate?.future ?? {'completed': true};
  }
}

Future<void> drain() => Future<void>.delayed(Duration.zero);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('Late account reads cannot repopulate state after logout', () async {
    final api = LifecycleApi()
      ..summaryGate = Completer()
      ..fitGate = Completer()
      ..savedGate = Completer();
    final controller = AppController(apiClient: api)
      ..email = 'first@example.test'
      ..token = 'first-session'
      ..stage = AppStage.home;
    await controller.refreshRemoteData();
    expect(api.summaryCalls, 1);
    final epoch = controller.navigationSessionEpoch;
    await controller.logout();
    controller.email = 'second@example.test';
    controller.token = 'second-session';
    controller.fitProfile = {'owner': 'second'};
    controller.savedProductRows = [
      {'product': product('second')},
    ];
    api.summaryGate!.complete({'owner': 'first'});
    api.fitGate!.complete({'owner': 'first'});
    api.savedGate!.complete([
      {'product': product('first')},
    ]);
    await drain();
    expect(controller.navigationSessionEpoch, epoch + 1);
    expect(controller.summary, isEmpty);
    expect(controller.fitProfile['owner'], 'second');
    expect(controller.savedProductRows.single['product']['id'], 'second');
    expect(controller.stage, AppStage.welcome);
    controller.dispose();
  });

  test('Unauthorized clears all account data and temporary onboarding', () {
    final api = LifecycleApi();
    final controller = AppController(apiClient: api)
      ..token = 'old'
      ..stage = AppStage.home
      ..fitProfile = {'height_cm': 170}
      ..tasteProfile = {
        'colors': ['red'],
      }
      ..summary = {'viewed': 10}
      ..billing = {'active': true}
      ..savedRows = [
        {'recommendation_id': 'old'},
      ]
      ..savedProductRows = [
        {'id': 'old'},
      ]
      ..savedOutfits = [
        {'id': 'old'},
      ]
      ..outfits = [
        {'id': 'old'},
      ]
      ..pendingFitPrefs = {'clothing_size': 'M'}
      ..photoConfirmPending = {'photo': 'old'}
      ..selectedPhotoPath = 'old-photo'
      ..isLoading = true
      ..feedRefreshing = true;
    controller.savedIds.add('old');
    controller.photoPaths.add('old-photo');
    controller.shoppingTask.result = {'id': 'old-task'};
    final epoch = controller.navigationSessionEpoch;
    api.onUnauthorized!();
    expect(controller.navigationSessionEpoch, epoch + 1);
    expect(controller.token, isNull);
    expect(controller.stage, AppStage.auth);
    expect(controller.fitProfile, isEmpty);
    expect(controller.tasteProfile, isEmpty);
    expect(controller.summary, isEmpty);
    expect(controller.billing, isEmpty);
    expect(controller.savedRows, isEmpty);
    expect(controller.savedIds, isEmpty);
    expect(controller.savedProductRows, isEmpty);
    expect(controller.savedOutfits, isEmpty);
    expect(controller.outfits, isEmpty);
    expect(controller.pendingFitPrefs, isNull);
    expect(controller.photoPaths, isEmpty);
    expect(controller.photoConfirmPending, isNull);
    expect(controller.shoppingTask.result, isNull);
    expect(controller.isLoading, isFalse);
    expect(controller.feedRefreshing, isFalse);
    controller.dispose();
  });

  test('Failed secondary reads preserve last confirmed collections', () async {
    final api = LifecycleApi()..failReads = true;
    final controller = AppController(apiClient: api)
      ..summary = {'owner': 'confirmed'}
      ..fitProfile = {'height_cm': 170}
      ..tasteProfile = {'fit': 'relaxed'}
      ..savedProductRows = [
        {'id': 'confirmed-product'},
      ]
      ..outfits = [
        {'id': 'confirmed-outfit'},
      ]
      ..savedOutfits = [
        {'id': 'confirmed-saved-outfit'},
      ];
    await controller.refreshRemoteData();
    await drain();
    expect(controller.summary['owner'], 'confirmed');
    expect(controller.fitProfile['height_cm'], 170);
    expect(controller.tasteProfile['fit'], 'relaxed');
    expect(controller.savedProductRows.single['id'], 'confirmed-product');
    expect(controller.outfits.single['id'], 'confirmed-outfit');
    expect(controller.savedOutfits.single['id'], 'confirmed-saved-outfit');
    controller.dispose();
  });

  test('Concurrent remote refreshes share secondary reads', () async {
    final api = LifecycleApi()..summaryGate = Completer();
    final controller = AppController(apiClient: api);
    await controller.refreshRemoteData();
    await controller.refreshRemoteData();
    expect(api.summaryCalls, 1);
    api.summaryGate!.complete({'owner': 'current'});
    await drain();
    await controller.refreshRemoteData();
    expect(api.summaryCalls, 2);
    await drain();
    controller.dispose();
  });

  test('Disposal invalidates feed, secondary reads and callbacks', () async {
    final api = LifecycleApi()
      ..nextFeed = Completer<List<Map<String, dynamic>>>();
    final feed = api.nextFeed!;
    final controller = AppController(apiClient: api);
    final loading = controller.refreshRemoteData();
    await drain();
    controller.dispose();
    feed.complete([
      {'product': product('late')},
    ]);
    await loading;
    await drain();
    expect(controller.productFeed, isEmpty);
    expect(api.summaryCalls, 0);
    expect(api.onUnauthorized, isNull);
    expect(api.onSessionChanged, isNull);
    expect(api.disposeCalls, 0, reason: 'Injected API belongs to its caller');
  });

  test(
    'An earlier action cannot clear busy while another is pending',
    () async {
      final api = LifecycleApi();
      final controller = AppController(apiClient: api);
      final first = controller.updateTasteProfile(
        priceMin: 0,
        priceMax: 1000,
        preferredFit: 'regular',
      );
      final second = controller.updateTasteProfile(
        priceMin: 0,
        priceMax: 2000,
        preferredFit: 'relaxed',
      );
      api.mutations[0].complete({'owner': 'first'});
      await first;
      expect(controller.isLoading, isTrue);
      api.mutations[1].complete({'owner': 'second'});
      await second;
      expect(controller.isLoading, isFalse);
      await drain();
      controller.dispose();
    },
  );

  test(
    'An action completing after logout does not overwrite the new session',
    () async {
      final api = LifecycleApi();
      final controller = AppController(apiClient: api);
      final first = controller.updateTasteProfile(
        priceMin: 0,
        priceMax: 1000,
        preferredFit: 'regular',
      );
      await controller.logout();
      controller.tasteProfile = {'owner': 'second'};
      controller.error = 'second-error';
      api.mutations.single.complete({'owner': 'first'});
      await first;
      expect(controller.tasteProfile['owner'], 'second');
      expect(controller.error, 'second-error');
      expect(api.feedCalls, 0);
      controller.dispose();
    },
  );

  test('Pending login does not restore a controller after logout', () async {
    final api = LifecycleApi()..loginGate = Completer();
    final controller = AppController(apiClient: api);
    final login = controller.login('test-password');
    await drain();
    await controller.logout();
    api.loginGate!.complete('obsolete-token');
    await login;
    expect(controller.token, isNull);
    expect(controller.stage, AppStage.welcome);
    expect(controller.error, isNull);
    controller.dispose();
  });

  test(
    'Token rotation preserves navigation identity and logout follows its write',
    () async {
      const channel = MethodChannel(
        'plugins.it_nomads.com/flutter_secure_storage',
      );
      final writeStarted = Completer<void>();
      final writeGate = Completer<void>();
      final operations = <String>[];
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(channel, (call) async {
            operations.add(call.method);
            if (call.method == 'write') {
              writeStarted.complete();
              await writeGate.future;
            }
            return null;
          });
      addTearDown(
        () => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
            .setMockMethodCallHandler(channel, null),
      );
      final api = LifecycleApi();
      final controller = AppController(apiClient: api);
      final epoch = controller.navigationSessionEpoch;
      final rotation = api.onSessionChanged!('rotated-access', 'refresh');
      await writeStarted.future;
      expect(controller.navigationSessionEpoch, epoch);
      final logout = controller.logout();
      await drain();
      expect(operations, ['write']);
      writeGate.complete();
      await Future.wait([rotation, logout]);
      expect(operations, ['write', 'delete']);
      expect(controller.navigationSessionEpoch, epoch + 1);
      expect(controller.token, isNull);
      controller.dispose();
    },
  );

  test(
    'Disposing a former injected-client owner keeps the new owner callbacks',
    () async {
      final api = LifecycleApi();
      final first = AppController(apiClient: api)..token = 'old';
      final current = AppController(apiClient: api)
        ..token = 'current'
        ..stage = AppStage.home;
      first.dispose();
      expect(api.onUnauthorized, isNotNull);
      expect(api.onSessionChanged, isNotNull);
      final epoch = current.navigationSessionEpoch;
      api.onUnauthorized!();
      expect(current.token, isNull);
      expect(current.stage, AppStage.auth);
      expect(current.navigationSessionEpoch, epoch + 1);
      expect(api.disposeCalls, 0);
      await drain();
      current.dispose();
      expect(api.onUnauthorized, isNull);
      expect(api.onSessionChanged, isNull);
    },
  );

  for (final authOperation in ['login', 'register']) {
    test(
      '$authOperation follow-up routing errors belong to the accepted session',
      () async {
        const channel = MethodChannel(
          'plugins.it_nomads.com/flutter_secure_storage',
        );
        TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
            .setMockMethodCallHandler(channel, (_) async => null);
        addTearDown(
          () => TestDefaultBinaryMessengerBinding
              .instance
              .defaultBinaryMessenger
              .setMockMethodCallHandler(channel, null),
        );
        final api = LifecycleApi()
          ..loginGate = Completer()
          ..onboardingGate = Completer();
        final controller = AppController(apiClient: api)..stage = AppStage.auth;
        final epoch = controller.navigationSessionEpoch;
        final auth = authOperation == 'login'
            ? controller.login('test-password')
            : controller.register('test-password');
        api.loginGate!.complete('accepted-session');
        await api.onboardingStarted.future;
        expect(controller.navigationSessionEpoch, epoch + 1);
        expect(controller.isLoading, isTrue);
        api.onboardingGate!.completeError(Exception('routing unavailable'));
        await auth;
        expect(
          controller.error,
          'Не удалось выполнить действие. Повторите попытку.',
        );
        expect(controller.error, isNot(contains('routing unavailable')));
        expect(controller.token, 'accepted-session');
        expect(controller.stage, AppStage.auth);
        expect(controller.isLoading, isFalse);
        controller.dispose();
      },
    );
  }

  test(
    'Fit confirmation routes within the current account without resetting it',
    () async {
      final api = LifecycleApi()
        ..fitGate = Completer()
        ..onboardingGate = Completer();
      final controller = AppController(apiClient: api)
        ..stage = AppStage.fitConfirmation
        ..token = 'accepted-session'
        ..fitProfile = {'height_cm': 170, 'clothing_size': 'M'};
      final epoch = controller.navigationSessionEpoch;
      final confirmation = controller.finishFitConfirmation();
      expect(controller.navigationSessionEpoch, epoch);
      expect(controller.fitProfile['clothing_size'], 'M');
      expect(controller.isLoading, isTrue);
      api.fitGate!.complete({'height_cm': 170, 'clothing_size': 'M'});
      await api.onboardingStarted.future;
      api.onboardingGate!.complete({'completed': true});
      await confirmation;
      expect(controller.stage, AppStage.home);
      expect(controller.navigationSessionEpoch, epoch);
      expect(controller.isLoading, isFalse);
      await drain();
      controller.dispose();
    },
  );
}
