import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/widgets/modish_widgets.dart';
import 'package:modish/features/onboarding/fit_quiz_screen.dart';
import 'package:modish/features/onboarding/onboarding_v2_screen.dart';
import 'package:modish/features/profile/edit_profile_screen.dart';
import 'app_regressions_test.dart' show FakeApi;
import 'build11_ui_test.dart' show EditController;

class _GenderApi extends FakeApi {
  final chosen = <String>[];
  @override
  Future<void> activationPreferences({
    required String genderTarget,
    required String ageGroup,
    required String clothingSize,
    required String shoeSize,
    required List<String> styles,
    required String priceSegment,
    int? height,
  }) async => chosen.add(genderTarget);

  @override
  Future<Map<String, dynamic>> styleSwipeDeck() async => {
    'products': <Map<String, dynamic>>[],
    'rated': 0,
    'can_continue': false,
  };
}

void main() {
  test('New and cleared fit never imply a gender', () {
    final c = AppController(apiClient: _GenderApi());
    expect(c.styleTarget, isNull);
    c.setStyleTarget('menswear');
    c.fitProfile = {};
    c.goToReanalyze();
    expect(c.styleTarget, isNull);
    c.dispose();
  });

  testWidgets('Legacy quiz starts unselected and cannot continue by default', (
    tester,
  ) async {
    final c = AppController(apiClient: _GenderApi());
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: FitQuizScreen(controller: c)),
      ),
    );
    final chips = tester.widgetList<ChoiceChip>(find.byType(ChoiceChip));
    expect(chips.where((chip) => chip.selected), isEmpty);
    final button = find.widgetWithText(PrimaryButton, 'Продолжить');
    await tester.scrollUntilVisible(
      button,
      300,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(button);
    await tester.pumpAndSettle();
    expect(c.pendingFitPrefs, isNull);
    expect(find.textContaining('Выберите «Мужское»'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    c.dispose();
  });

  for (final gender in [null, 'unknown', 'menswear', 'womenswear']) {
    testWidgets('Profile edit preserves only a known selection: $gender', (
      tester,
    ) async {
      final c = EditController()
        ..fitProfile = {
          'gender_target': gender,
          'height': 170,
          'clothing_size': 'M',
          'shoe_size': '38',
          'budget_max': 10000,
        };
      await tester.pumpWidget(
        MaterialApp(home: EditProfileScreen(controller: c, setupMode: true)),
      );
      final choices = tester
          .widgetList<ChoiceChip>(find.byType(ChoiceChip))
          .where((chip) => chip.selected)
          .map((chip) => (chip.label as Text).data)
          .toList();
      expect(choices, switch (gender) {
        'menswear' => ['Мужское'],
        'womenswear' => ['Женское'],
        _ => isEmpty,
      });
      if (gender == null || gender == 'unknown') {
        await tester.tap(find.byKey(const ValueKey('save-profile')));
        await tester.pumpAndSettle();
        expect(c.saved, isFalse);
        expect(find.textContaining('Выберите «Мужское»'), findsOneWidget);
      }
      await tester.pumpWidget(const SizedBox());
      c.dispose();
    });
  }

  testWidgets('Activation requires a real tap and submits that choice', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(500, 1800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final api = _GenderApi();
    final c = AppController(apiClient: api)
      ..activationStatus = {
        'age_group': '25-34',
        'clothing_size': 'M',
        'shoe_size': '38',
        'height': 170,
        'style_preferences': ['casual', 'minimalism'],
        'price_segment': 'mass',
      };
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: OnboardingV2Screen(controller: c)),
      ),
    );
    final choice = find.byKey(const ValueKey('onboarding-gender-womenswear'));
    final button = find.widgetWithText(PrimaryButton, 'Настроить мой вкус');
    expect(tester.widget<ChoiceChip>(choice).selected, isFalse);
    expect(tester.widget<PrimaryButton>(button).onPressed, isNull);
    expect(api.chosen, isEmpty);
    await tester.tap(choice);
    await tester.pump();
    expect(tester.widget<PrimaryButton>(button).onPressed, isNotNull);
    await tester.ensureVisible(button);
    await tester.tap(button);
    await tester.pumpAndSettle();
    expect(api.chosen, ['womenswear']);
    expect(c.activationStatus['gender_target'], 'womenswear');
    expect(c.styleTarget, 'womenswear');
    await tester.pumpWidget(const SizedBox());
    c.dispose();
  });
}
