import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/network/api_client.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';

class AuthScreen extends StatefulWidget {
  const AuthScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<AuthScreen> createState() => _AuthScreenState();
}

class _AuthScreenState extends State<AuthScreen> {
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
    final registerMode = c.authRegisterMode;
    return MobileViewport(
      child: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(24, 12, 24, 24),
          children: [
            IconButton(
              alignment: Alignment.centerLeft,
              onPressed: () => c.goToWelcome(),
              icon: const Icon(Icons.arrow_back),
            ),
            const SizedBox(height: 8),
            Text(
              registerMode ? 'Создайте аккаунт' : 'С возвращением',
              style: AppTextStyles.displaySm.copyWith(fontSize: 28),
            ),
            const SizedBox(height: 6),
            Text(
              registerMode
                  ? 'Сохраним ваш стиль и подборки'
                  : 'Войдите, чтобы продолжить подбор',
              style: AppTextStyles.bodyMuted,
            ),
            const SizedBox(height: 28),
            Container(
              height: 44,
              padding: const EdgeInsets.all(3),
              decoration: BoxDecoration(
                color: AppColors.card,
                border: Border.all(color: AppColors.line),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Row(
                children: [
                  _AuthTab(
                    label: 'Вход',
                    selected: !registerMode,
                    onTap: () => c.goToAuth(registerMode: false),
                  ),
                  _AuthTab(
                    label: 'Регистрация',
                    selected: registerMode,
                    onTap: () => c.goToAuth(registerMode: true),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 24),
            ModishTextField(
              label: 'Email',
              controller: email,
              hint: 'you@email.com',
              icon: Icons.mail_outline,
            ),
            const SizedBox(height: 16),
            ModishTextField(
              label: 'Пароль',
              controller: password,
              obscure: true,
              hint: 'Минимум 8 символов',
              icon: Icons.lock_outline,
            ),
            const SizedBox(height: 24),
            PrimaryButton(
              label: c.isLoading
                  ? 'Подождите...'
                  : (registerMode ? 'Создать аккаунт' : 'Войти'),
              onPressed: c.isLoading
                  ? null
                  : () async {
                      c.email = email.text.trim();
                      if (registerMode) {
                        await c.register(password.text);
                      } else {
                        await c.login(password.text);
                      }
                    },
            ),
            if (kDebugMode) ...[
              const SizedBox(height: 10),
              Text(
                'API: ${ApiClient.resolvedBaseUrl()}',
                style: AppTextStyles.caption,
              ),
            ],
            if (c.error != null) ...[
              const SizedBox(height: 14),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: AppColors.error.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: AppColors.error.withValues(alpha: 0.35)),
                ),
                child: Text(
                  c.error!,
                  style: const TextStyle(color: AppColors.error, height: 1.35, fontSize: 13),
                ),
              ),
            ],
            const SizedBox(height: 20),
            Center(
              child: Text(
                'Продолжая, вы соглашаетесь с Условиями и Политикой конфиденциальности',
                textAlign: TextAlign.center,
                style: AppTextStyles.caption,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _AuthTab extends StatelessWidget {
  const _AuthTab({
    required this.label,
    required this.selected,
    required this.onTap,
  });
  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: InkWell(
        borderRadius: BorderRadius.circular(6),
        onTap: onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 160),
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: selected ? AppColors.accent : Colors.transparent,
            borderRadius: BorderRadius.circular(6),
          ),
          child: Text(
            label,
            style: TextStyle(
              fontSize: 12,
              fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
              color: selected ? AppColors.onAccent : AppColors.muted,
            ),
          ),
        ),
      ),
    );
  }
}
