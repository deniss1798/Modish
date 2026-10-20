import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/features/visual/visual_analysis_screen.dart';

import 'app_regressions_test.dart' show FakeApi;

class _VisualApi extends FakeApi {
  int visualCalls = 0;

  @override
  Future<Map<String, dynamic>> visualAnalysis() async {
    visualCalls++;
    return {'image_url': 'https://example.test/style.png'};
  }
}

void main() {
  testWidgets('Missing capability never dispatches optional generation', (
    tester,
  ) async {
    final api = _VisualApi();
    final controller = AppController(apiClient: api);
    late BuildContext context;
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (value) {
            context = value;
            return const Scaffold();
          },
        ),
      ),
    );

    expect(controller.supportsStyleVisual, isFalse);
    await controller.openVisualAnalysis(context);
    await tester.pumpAndSettle();

    expect(api.visualCalls, 0);
    expect(controller.isLoading, isFalse);
    expect(controller.error, contains('Продолжите подбор вещей'));
    expect(find.byType(VisualAnalysisScreen), findsNothing);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });

  testWidgets('Explicit capability permits the existing generation path', (
    tester,
  ) async {
    final api = _VisualApi();
    final controller = AppController(apiClient: api)
      ..activationStatus = {
        'recommendation_features': ['style_visual_v1'],
      };
    late BuildContext context;
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (value) {
            context = value;
            return const Scaffold();
          },
        ),
      ),
    );

    await controller.openVisualAnalysis(context);
    await tester.pumpAndSettle();

    expect(api.visualCalls, 1);
    expect(controller.visualImageUrl, 'https://example.test/style.png');
    expect(controller.error, isNull);
    expect(controller.isLoading, isFalse);
    expect(find.byType(VisualAnalysisScreen), findsOneWidget);
    expect(tester.takeException(), isNull);

    // The next server snapshot can withdraw availability for this account.
    Navigator.of(context).pop();
    await tester.pumpAndSettle();
    controller.activationStatus = {
      'recommendation_features': ['catalogue_discovery_v1'],
    };
    await controller.openVisualAnalysis(context);
    await tester.pumpAndSettle();
    expect(api.visualCalls, 1);
    expect(find.byType(VisualAnalysisScreen), findsNothing);
    expect(controller.error, isNotNull);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });
}
