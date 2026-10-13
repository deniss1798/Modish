import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/features/recommendations/feed_screen.dart';

import 'app_regressions_test.dart' show FakeApi, product;

class ViewApi extends FakeApi {
  final attempts = <String>[];
  final accepted = <String>[];
  bool failViews = false;
  Completer<void>? pendingView;

  @override
  Future<Map<String, dynamic>> recordRecommendationEvent({
    required String eventType,
    String? productId,
    String? outfitId,
    Map<String, dynamic>? meta,
  }) async {
    if (eventType == 'view') {
      expect(meta?['surface'], 'feed');
      attempts.add(productId!);
      await pendingView?.future;
      if (failViews) throw Exception('offline');
      accepted.add(productId);
    }
    return {};
  }
}

void main() {
  testWidgets('Failed visible-card view retries after returning to feed', (
    tester,
  ) async {
    final api = ViewApi()
      ..failViews = true
      ..rows = [
        {'product': product('first')},
      ];
    final controller = AppController(apiClient: api);
    await controller.refreshFeedFromUser();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: FeedScreen(controller: controller)),
      ),
    );
    await tester.pumpAndSettle();
    expect(api.attempts, isNotEmpty);
    expect(api.accepted, isEmpty);

    controller.setTab(1);
    await tester.pumpAndSettle();
    final attemptsBeforeReturn = api.attempts.length;
    api.failViews = false;
    await tester.pumpAndSettle();
    expect(api.attempts.length, attemptsBeforeReturn);

    controller.setTab(0);
    await tester.pumpAndSettle();
    expect(api.accepted, ['first']);
    expect(api.attempts.length, attemptsBeforeReturn + 1);
    controller.setTab(1);
    controller.setTab(0);
    await tester.pumpAndSettle();
    expect(api.accepted, ['first']);
    expect(api.relatedCalls, 1);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });

  testWidgets('Rebuilds do not duplicate an in-flight view', (tester) async {
    final api = ViewApi()
      ..pendingView = Completer<void>()
      ..rows = [
        {'product': product('pending')},
      ];
    final controller = AppController(apiClient: api);
    await controller.refreshFeedFromUser();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: FeedScreen(controller: controller)),
      ),
    );
    await tester.pumpAndSettle();
    for (var i = 0; i < 3; i++) {
      controller.setTab(0);
      await tester.pumpAndSettle();
    }
    expect(api.attempts, ['pending']);
    api.pendingView!.complete();
    await tester.pumpAndSettle();
    expect(api.accepted, ['pending']);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });
}
