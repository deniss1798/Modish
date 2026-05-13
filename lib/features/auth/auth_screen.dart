import 'package:flutter/material.dart';
import '../../app.dart';
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
          padding: const EdgeInsets.fromLTRB(24, 18, 24, 24),
          children: [
            Align(
              alignment: Alignment.centerLeft,
              child: IconButton(
                onPressed: () => c.goToWelcome(),
                icon: const Icon(Icons.arrow_back),
              ),
            ),
            const SizedBox(height: 20),
            const Center(child: Brand(size: 34)),
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
            const SizedBox(height: 26),
            ModishTextField(
              label: 'Email',
              controller: email,
              hint: 'Введите ваш email',
              icon: Icons.mail_outline,
            ),
            const SizedBox(height: 18),
            ModishTextField(
              label: 'Пароль',
              controller: password,
              obscure: true,
              hint: 'Введите пароль',
              icon: Icons.lock_outline,
            ),
            Align(
              alignment: Alignment.centerRight,
              child: TextButton(
                onPressed: () => _soon(context),
                child: const Text(
                  'Забыли пароль?',
                  style: TextStyle(color: AppColors.muted, fontSize: 12),
                ),
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
                style: const TextStyle(color: AppColors.accentSoft),
              ),
            ],
            const SizedBox(height: 24),
            Row(
              children: [
                const Expanded(child: Divider(color: AppColors.line)),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 12),
                  child: Text(
                    'или продолжить через',
                    style: AppTextStyles.caption,
                  ),
                ),
                const Expanded(child: Divider(color: AppColors.line)),
              ],
            ),
            const SizedBox(height: 18),
            Row(
              children: [
                Expanded(
                  child: SocialButton(
                    label: 'Apple',
                    icon: Icons.apple,
                    onPressed: () => _soon(context),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: SocialButton(
                    label: 'Google',
                    icon: Icons.g_mobiledata,
                    onPressed: () => _soon(context),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: SocialButton(
                    label: 'Email',
                    icon: Icons.mail_outline,
                    onPressed: () => _soon(context),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 24),
            Center(
              child: Text(
                'Продолжая, вы соглашаетесь\nс Условиями и Политикой конфиденциальности',
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

void _soon(BuildContext context) {
  ScaffoldMessenger.of(
    context,
  ).showSnackBar(const SnackBar(content: Text('Скоро подключим')));
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
            color: selected ? AppColors.bg : Colors.transparent,
            borderRadius: BorderRadius.circular(6),
          ),
          child: Text(
            label,
            style: TextStyle(
              fontSize: 12,
              fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
              color: selected ? AppColors.ink : AppColors.muted,
            ),
          ),
        ),
      ),
    );
  }
}
