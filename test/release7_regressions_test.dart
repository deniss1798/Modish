import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/features/onboarding/upload_screen.dart';
import 'package:modish/features/recommendations/saved_screen.dart';
import 'package:modish/features/outfits/outfits_screen.dart';
import 'app_regressions_test.dart' show FakeApi, product;

class Release7Api extends FakeApi {
  Completer<void>? analysis;
  List<Map<String, dynamic>> storedOutfits = [];
  final generatedScenarios = <String>[];
  @override
  Future<void> setStyleTarget(String target) async {}
  @override
  Future<void> analyzeStyleProfile(String path) async {
    await analysis?.future;
  }

  @override
  Future<List<Map<String, dynamic>>> outfitsGenerate({
    int count = 3,
    String scenario = 'daily',
  }) async {
    generatedScenarios.add(scenario);
    return [];
  }

  @override
  Future<List<Map<String, dynamic>>> outfitsList({
    String? scenario,
    bool savedOnly = false,
  }) async => savedOnly ? [] : storedOutfits;
}

void main() {
  testWidgets(
    'Saved shows only selected category and removes without resetting feed',
    (tester) async {
      final api = Release7Api();
      final c = AppController(apiClient: api)
        ..savedProductRows = [
          {'product': product('saved')},
        ];
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(body: SavedScreen(controller: c)),
        ),
      );
      expect(find.text('Вещи · 1'), findsOneWidget);
      expect(find.text('Образы · 0'), findsOneWidget);
      expect(find.text('Здесь будут ваши образы'), findsNothing);
      await tester.tap(find.text('Образы · 0'));
      await tester.pumpAndSettle();
      expect(find.text('Здесь будут ваши образы'), findsOneWidget);
      await tester.tap(find.text('Подобрать образ'));
      expect(c.tab, 1);
      await tester.tap(find.text('Вещи · 1'));
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('Действия с вещью'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Убрать из сохранённого'));
      await tester.pumpAndSettle();
      expect(c.savedProductRows, isEmpty);
      expect(find.text('Вещи · 0'), findsOneWidget);
      expect(api.feedCalls, 0);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'Photo reanalysis cancellation keeps profile and returns without loading',
    (tester) async {
      final api = Release7Api();
      final fit = {
        'height_cm': 180,
        'gender_target': 'menswear',
        'clothing_size': 'XL',
        'interest_categories': ['рубашки'],
      };
      final c = AppController(apiClient: api)
        ..token = 'test'
        ..tab = 3
        ..fitProfile = Map.of(fit);
      c.setPhotoPath('old-photo');
      c.goToReanalyze();
      expect(c.photoPaths, isEmpty);
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(body: UploadScreen(controller: c)),
        ),
      );
      expect(find.text('В ленту без фото'), findsNothing);
      expect(find.text('Параметры (быстро)'), findsNothing);
      await tester.ensureVisible(find.text('Отмена'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Отмена'));
      await tester.pumpAndSettle();
      expect(c.stage, AppStage.home);
      expect(c.tab, 3);
      expect(c.fitProfile, fit);
      expect(c.isLoading, isFalse);
      expect(api.feedCalls, 0);
    },
  );

  test(
    'Late analysis response cannot reopen feed after cancellation',
    () async {
      final api = Release7Api()..analysis = Completer<void>();
      final c = AppController(apiClient: api)..token = 'test';
      c.goToReanalyze();
      c.setPhotoPath('photo');
      final pending = c.analyze();
      await Future<void>.delayed(Duration.zero);
      c.exitUploadFlow();
      api.analysis!.complete();
      await pending;
      expect(c.stage, AppStage.home);
      expect(c.tab, 3);
      expect(c.isLoading, isFalse);
      expect(api.feedCalls, 0);
    },
  );

  testWidgets(
    'Empty refresh reports no alternatives despite existing outfits',
    (tester) async {
      final old = <String, dynamic>{
        'id': 'old',
        'style_direction': 'daily',
        'products': <String, dynamic>{},
      };
      final api = Release7Api()..storedOutfits = [old];
      final c = AppController(apiClient: api)..outfits = [old];
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(body: OutfitsScreen(controller: c)),
        ),
      );
      await tester.tap(find.text('Обновить образы'));
      await tester.pumpAndSettle();
      expect(
        find.text('Новых сочетаний пока нет. Текущие образы сохранены.'),
        findsOneWidget,
      );
      expect(c.outfits.single['id'], 'old');
      expect(api.generatedScenarios, ['daily', 'office', 'evening']);
    },
  );
}
