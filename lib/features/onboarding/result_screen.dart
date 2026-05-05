import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/widgets/modish_widgets.dart';

class ResultScreen extends StatefulWidget {
  const ResultScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<ResultScreen> createState() => _ResultScreenState();
}

class _ResultScreenState extends State<ResultScreen> {
  Map<String, dynamic> brief = {};
  bool loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => loading = true);
    try {
      brief = await widget.controller.api.profileBrief();
    } catch (_) {
      brief = {};
    }
    if (mounted) setState(() => loading = false);
  }

  @override
  Widget build(BuildContext context) {
    final stats = (brief['stats'] as Map?) ?? const {};
    final events = stats['events']?.toString() ?? '—';
    final saved = stats['saved']?.toString() ?? '—';
    return Scaffold(
      backgroundColor: AppColors.bg,
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(18, 22, 18, 20),
          children: [
            const Center(child: Brand()),
            const SizedBox(height: 12),
            const Text(
              'Готово',
              textAlign: TextAlign.center,
              style: TextStyle(fontFamily: 'Georgia', fontSize: 40, color: AppColors.ink),
            ),
            const SizedBox(height: 10),
            SoftCard(
              child: loading
                  ? const Padding(
                      padding: EdgeInsets.all(18),
                      child: Text('Собираем результаты…', style: TextStyle(color: AppColors.muted)),
                    )
                  : Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('Действий: $events · сохранено: $saved'),
                        const SizedBox(height: 10),
                        const Text(
                          'Дальше',
                          style: TextStyle(fontWeight: FontWeight.w700, fontSize: 18),
                        ),
                        const SizedBox(height: 6),
                        const Text(
                          'Откройте вкладку «Образы» — мы собрали 3 комплекта под ваш профиль.',
                          style: TextStyle(color: AppColors.muted),
                        ),
                      ],
                    ),
            ),
            const SizedBox(height: 14),
            PrimaryButton(
              label: 'Перейти к образам',
              icon: Icons.style,
              onPressed: () {
                widget.controller.setTab(1);
                Navigator.pop(context);
              },
            ),
          ],
        ),
      ),
    );
  }
}

