import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/widgets/modish_widgets.dart';

void main() {
  testWidgets('Modish: старт и экран после boot', (tester) async {
    await tester.pumpWidget(const ModishBootstrap());
    await tester.pump();
    await tester.pump(const Duration(seconds: 4));
    await tester.pumpAndSettle();

    expect(find.byType(GoldWordmark), findsWidgets);

    final welcome = find.textContaining('персональный');
    final feedTab = find.text('Подборка');
    expect(
      welcome.evaluate().isNotEmpty || feedTab.evaluate().isNotEmpty,
      isTrue,
      reason: 'Ожидается экран входа или главная после boot',
    );
  });
}
