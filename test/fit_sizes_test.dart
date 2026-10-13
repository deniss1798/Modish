import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/features/profile/fit_sizes.dart';
import 'package:modish/features/profile/edit_profile_screen.dart';
import 'package:modish/features/onboarding/onboarding_v2_screen.dart';
import 'package:modish/core/widgets/modish_widgets.dart';
import 'app_regressions_test.dart' show FakeApi;
import 'build11_ui_test.dart' show EditController;

class FitStatusApi extends FakeApi {
  bool missing = true;
  @override
  Future<Map<String, dynamic>> onboardingStatus() async => {
    'completed': true,
    'rated': 20,
    'fit_confirmation_required': missing,
  };
}

void main() {
  test('Size entry keeps systems separate and never substitutes M', () {
    expect(sizeChoice(''), isNull);
    expect(sizeChoice('48'), {'value': '48', 'system': 'RU'});
    expect(sizeChoice('EU 48'), {'value': '48', 'system': 'EU'});
    expect(sizeChoice('38', footwear: true), {'value': '38', 'system': 'EU'});
    expect(sizeChoice('M'), {'value': 'M', 'system': 'LETTER'});
    expect(sizeChoice('W28L32'), {'value': 'W28L32', 'system': 'WAIST'});
    expect(sizeChoice('M/L'), isNull);
    expect(sizeChoice('RU M'), isNull);
    expect(sizeChoice('M', footwear: true), isNull);
    expect(
      sizeFieldValue({
        'size_profile': {
          'sizes': {
            'top': {'value': 'M', 'system': 'LETTER', 'state': 'size_unknown'},
          },
        },
      }, 'top'),
      '',
    );
  });

  test('Warm account confirms fit without repeating Style Swipe', () async {
    final api = FitStatusApi();
    final controller = AppController(apiClient: api);
    await controller.finishFitConfirmation();
    expect(controller.stage, AppStage.fitConfirmation);
    expect(api.feedCalls, 0);
    api.missing = false;
    await controller.finishFitConfirmation();
    expect(controller.stage, AppStage.home);
    expect(api.feedCalls, 1);
    controller.dispose();
  });

  testWidgets('Unconfirmed legacy M is blank and cannot be saved as real fit', (
    tester,
  ) async {
    final controller = EditController()
      ..fitProfile = {
        'height': 170,
        'clothing_size': 'M',
        'gender_target': 'womenswear',
        'size_profile': {'height_confirmed': false, 'sizes': {}},
      };
    await tester.pumpWidget(
      MaterialApp(
        home: EditProfileScreen(controller: controller, setupMode: true),
      ),
    );
    for (final field in ['top', 'bottom', 'dress', 'shoe']) {
      final input = tester.widget<TextField>(
        find.byKey(ValueKey('fit-$field-size')),
      );
      expect(input.controller!.text, '');
    }
    await tester.tap(find.byKey(const ValueKey('save-profile')));
    await tester.pumpAndSettle();
    expect(controller.saved, isFalse);
    expect(find.textContaining('Укажите рост и размеры'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });

  testWidgets('Onboarding requires height even when all other signals exist', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(500, 1800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final controller = AppController()
      ..activationStatus = {
        'age_group': '25-34',
        'clothing_size': 'M',
        'shoe_size': '38',
        'style_preferences': ['casual', 'minimalism'],
        'price_segment': 'mass',
      };
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: OnboardingV2Screen(controller: controller)),
      ),
    );
    expect(
      tester.widget<PrimaryButton>(find.byType(PrimaryButton)).onPressed,
      isNull,
    );
    await tester.enterText(
      find.byKey(const ValueKey('onboarding-height')),
      '168',
    );
    await tester.pump();
    expect(
      tester.widget<PrimaryButton>(find.byType(PrimaryButton)).onPressed,
      isNotNull,
    );
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });
}
