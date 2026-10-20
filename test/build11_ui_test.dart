import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/network/api_client.dart';
import 'package:modish/features/catalog/search_screen.dart';
import 'package:modish/features/profile/edit_profile_screen.dart';
import 'package:modish/features/profile/profile_screen.dart';

class SearchApi extends ApiClient {
  final calls = <String>[];
  final pending = <Completer<List<Map<String, dynamic>>>>[];
  @override
  Future<List<Map<String, dynamic>>> productsList({
    int limit = 50,
    int offset = 0,
    String? category,
    String? source,
    String? brand,
    String? query,
  }) {
    calls.add(query!);
    final request = Completer<List<Map<String, dynamic>>>();
    pending.add(request);
    return request.future;
  }
}

class EditController extends AppController {
  bool saved = false;
  @override
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
    saved = true;
  }
}

void main() {
  testWidgets('Search distinguishes empty, failure and stale responses', (
    tester,
  ) async {
    final api = SearchApi();
    final controller = AppController(apiClient: api);
    await tester.pumpWidget(
      MaterialApp(home: SearchScreen(controller: controller)),
    );
    expect(find.text('Недавние'), findsNothing);
    await tester.enterText(find.byType(TextField), 'куртка');
    await tester.pump(const Duration(milliseconds: 500));
    expect(api.calls, ['куртка']);
    await tester.enterText(find.byType(TextField), 'джинсы');
    await tester.pump(const Duration(milliseconds: 500));
    api.pending[1].complete([]);
    await tester.pumpAndSettle();
    expect(find.text('Ничего не найдено'), findsOneWidget);
    api.pending[0].complete([
      {'id': 'old', 'title': 'Старый ответ', 'brand': 'BAON', 'price': 1000},
    ]);
    await tester.pumpAndSettle();
    expect(find.text('Старый ответ'), findsNothing);
    expect(find.text('Ничего не найдено'), findsOneWidget);
    await tester.enterText(find.byType(TextField), 'BAON');
    await tester.pump(const Duration(milliseconds: 500));
    api.pending[2].completeError(Exception('offline'));
    await tester.pumpAndSettle();
    expect(find.text('Повторить поиск'), findsOneWidget);
    expect(find.text('Ничего не найдено'), findsNothing);
    await tester.tap(find.text('Повторить поиск'));
    await tester.pump();
    api.pending[3].complete([
      {'id': 'new', 'title': 'Куртка BAON', 'brand': 'BAON', 'price': 9499},
    ]);
    await tester.pumpAndSettle();
    expect(find.text('Куртка BAON'), findsOneWidget);
    await tester.tap(find.byTooltip('Очистить поиск'));
    await tester.pumpAndSettle();
    expect(find.text('Найдите нужную вещь'), findsOneWidget);
    expect(find.text('Куртка BAON'), findsNothing);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });

  testWidgets('Profile save stays above Android navigation and is tappable', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(360, 640);
    tester.view.devicePixelRatio = 1;
    tester.view.padding = const FakeViewPadding(top: 24, bottom: 48);
    tester.view.viewPadding = const FakeViewPadding(top: 24, bottom: 48);
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.view.resetPadding);
    addTearDown(tester.view.resetViewPadding);
    final controller = EditController()
      ..fitProfile = {
        'height': 178,
        'weight': 65,
        'gender_target': 'womenswear',
        'clothing_size': 'L',
        'shoe_size': '39',
        'budget_max': 25000,
        'activation_segment': 'female_casual',
      };
    await tester.pumpWidget(
      MaterialApp(home: EditProfileScreen(controller: controller)),
    );
    await tester.pumpAndSettle();
    final button = find.byKey(const ValueKey('save-profile'));
    expect(tester.getRect(button).bottom, lessThanOrEqualTo(592));
    expect(find.text('178'), findsOneWidget);
    expect(find.text('65'), findsOneWidget);
    await tester.tap(button);
    await tester.pumpAndSettle();
    expect(controller.saved, isTrue);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });

  testWidgets(
    'Profile uses actual preferences instead of defaults and button-like chips',
    (tester) async {
      final controller = AppController()
        ..summary = {
          'suitable_colors': ['navy', 'graphite'],
          'style_direction_human': ['Минимализм', 'Casual'],
        };
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(body: ProfileScreen(controller: controller)),
        ),
      );
      expect(find.textContaining('Пока мало данных'), findsOneWidget);
      expect(find.text('Минимализм'), findsNothing);
      expect(find.text('navy'), findsNothing);
      controller.tasteProfile = {
        'weights': {
          'colors': {'blue': 2},
          'styles': {'romantic': 3},
        },
        'liked_colors': ['blue'],
        'liked_styles': ['romantic'],
      };
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: ProfileScreen(key: const ValueKey(2), controller: controller),
          ),
        ),
      );
      await tester.tap(find.byKey(const ValueKey('profile-preferences')));
      await tester.pumpAndSettle();
      expect(find.text('Синий'), findsOneWidget);
      expect(find.text('Романтичный'), findsOneWidget);
      expect(find.byType(Chip), findsNothing);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
    },
  );
}
