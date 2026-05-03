import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/widgets/modish_widgets.dart';

class AuthScreen extends StatefulWidget {
  const AuthScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<AuthScreen> createState() => _AuthScreenState();
}

class _AuthScreenState extends State<AuthScreen> {
  bool registerMode = true;
  late TextEditingController email;
  final password = TextEditingController();

  @override
  void initState() {
    super.initState();
    email = TextEditingController(text: widget.controller.email);
  }

  @override
  void dispose() {
    email.dispose();
    password.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    return MobileViewport(
      child: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(22, 36, 22, 24),
          children: [
            const Center(child: Brand()),
            const SizedBox(height: 48),
            const Text(
              'Добро пожаловать',
              textAlign: TextAlign.center,
              style: TextStyle(
                fontSize: 48,
                fontFamily: 'Georgia',
                color: AppColors.ink,
              ),
            ),
            const Text(
              'Создайте аккаунт и откройте свой стиль',
              textAlign: TextAlign.center,
              style: TextStyle(color: AppColors.muted),
            ),
            const SizedBox(height: 24),
            SoftCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SegmentedButton<bool>(
                    segments: const [
                      ButtonSegment(value: false, label: Text('Вход')),
                      ButtonSegment(value: true, label: Text('Регистрация')),
                    ],
                    selected: {registerMode},
                    onSelectionChanged: (value) =>
                        setState(() => registerMode = value.first),
                  ),
                  const SizedBox(height: 16),
                  const Text('Email'),
                  TextField(
                    controller: email,
                    decoration: const InputDecoration(
                      prefixIcon: Icon(Icons.mail_outline),
                      hintText: 'Введите ваш email',
                    ),
                  ),
                  const SizedBox(height: 14),
                  const Text('Пароль'),
                  TextField(
                    controller: password,
                    obscureText: true,
                    decoration: const InputDecoration(
                      prefixIcon: Icon(Icons.lock_outline),
                      hintText: 'Введите пароль',
                    ),
                  ),
                  const SizedBox(height: 20),
                  PrimaryButton(
                    label: c.isLoading ? 'Подождите...' : 'Продолжить',
                    onPressed: c.isLoading
                        ? null
                        : () async {
                            c.email = email.text;
                            if (registerMode) {
                              await c.register(password.text);
                            } else {
                              await c.login(password.text);
                            }
                          },
                  ),
                  if (c.error != null) ...[
                    const SizedBox(height: 12),
                    Text(
                      c.error!,
                      style: const TextStyle(color: AppColors.accent),
                    ),
                  ],
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
