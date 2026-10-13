import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';

import 'app_regressions_test.dart' show FakeApi;

class NavigationApi extends FakeApi {
  @override
  Future<void> logoutSession() async => clearToken();
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const storageChannel = MethodChannel(
    'plugins.it_nomads.com/flutter_secure_storage',
  );
  setUp(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(storageChannel, (_) async => null);
  });
  tearDown(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(storageChannel, null);
  });
  Future<AppController> openPrivateRoute(WidgetTester tester) async {
    final controller = AppController(apiClient: NavigationApi())
      ..token = 'current-account'
      ..stage = AppStage.welcome;
    await tester.pumpWidget(
      AppScope(notifier: controller, child: const ModishApp()),
    );
    final navigator = tester.state<NavigatorState>(find.byType(Navigator));
    navigator.push(
      MaterialPageRoute<void>(
        builder: (_) =>
            const Scaffold(body: Center(child: Text('Private account route'))),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Private account route'), findsOneWidget);
    return controller;
  }

  for (final boundary in ['logout', 'expired']) {
    testWidgets('$boundary discards pushed routes belonging to the account', (
      tester,
    ) async {
      final controller = await openPrivateRoute(tester);
      final epoch = controller.navigationSessionEpoch;
      if (boundary == 'logout') {
        await tester.runAsync(controller.logout);
      } else {
        controller.api.onUnauthorized!();
      }
      await tester.pumpAndSettle();
      expect(controller.navigationSessionEpoch, greaterThan(epoch));
      expect(find.text('Private account route'), findsNothing);
      expect(
        tester.state<NavigatorState>(find.byType(Navigator)).canPop(),
        isFalse,
      );
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    });
  }

  testWidgets('Access-token rotation preserves the current pushed route', (
    tester,
  ) async {
    final controller = await openPrivateRoute(tester);
    final epoch = controller.navigationSessionEpoch;
    await controller.api.onSessionChanged!('rotated-access', 'rotated-refresh');
    controller.notifyListeners();
    await tester.pumpAndSettle();
    expect(controller.navigationSessionEpoch, epoch);
    expect(controller.token, 'rotated-access');
    expect(find.text('Private account route'), findsOneWidget);
    expect(
      tester.state<NavigatorState>(find.byType(Navigator)).canPop(),
      isTrue,
    );
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });
}
