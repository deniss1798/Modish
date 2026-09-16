import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';
import '../profile/edit_profile_screen.dart';

class SettingsScreen extends StatelessWidget {
  const SettingsScreen({super.key, required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) => Scaffold(
    backgroundColor: AppColors.bg,
    appBar: AppBar(
      title: const Text('Настройки', style: AppTextStyles.sectionTitle),
    ),
    body: ListView(
      padding: const EdgeInsets.all(20),
      children: [
        SoftCard(
          child: Column(
            children: [
              ListTile(
                leading: const Icon(Icons.person_outline),
                title: const Text('Аккаунт'),
                subtitle: SelectableText(controller.email),
              ),
              const Divider(color: AppColors.line),
              MenuTile(
                icon: Icons.tune,
                title: 'Параметры подборки',
                subtitle: 'Пол, размер, рост и бюджет',
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => EditProfileScreen(controller: controller),
                  ),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 20),
        SoftCard(
          child: Column(
            children: [
              const ListTile(
                leading: Icon(Icons.language),
                title: Text('Язык приложения'),
                subtitle: Text('Русский'),
              ),
              const ListTile(
                leading: Icon(Icons.dark_mode_outlined),
                title: Text('Оформление'),
                subtitle: Text('Тёмное'),
              ),
              const Divider(color: AppColors.line),
              MenuTile(
                icon: Icons.help_outline,
                title: 'Как пользоваться Modish',
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(builder: (_) => const HelpScreen()),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 20),
        PrimaryButton(
          label: 'Выйти из аккаунта',
          onPressed: controller.isLoading
              ? null
              : () async {
                  await controller.logout();
                  if (context.mounted) {
                    Navigator.of(context).popUntil((route) => route.isFirst);
                  }
                },
        ),
      ],
    ),
  );
}

class HelpScreen extends StatelessWidget {
  const HelpScreen({super.key});
  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Помощь')),
    body: const SingleChildScrollView(
      padding: EdgeInsets.all(24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Как устроена лента', style: AppTextStyles.sectionTitle),
          SizedBox(height: 12),
          Text(
            'Свайп вправо отмечает понравившуюся вещь, влево — пропускает. Кнопка «Не моё» сообщает, что такой товар вам не подходит. Закладка добавляет его в «Сохранённое».',
          ),
          SizedBox(height: 24),
          Text('Подбор и фильтры', style: AppTextStyles.sectionTitle),
          SizedBox(height: 12),
          Text(
            'Укажите пол, размер и бюджет в параметрах подборки. Фильтры в ленте ищут товары по каталогу. Несколько значений одного фильтра расширяют выбор, разные фильтры применяются вместе. Если подходящих вещей нет, сбросьте часть условий.',
          ),
          SizedBox(height: 24),
          Text('Образы и магазины', style: AppTextStyles.sectionTitle),
          SizedBox(height: 12),
          Text(
            'Во вкладке «Образы» выберите сценарий и нажмите «Обновить образы». Карточку вещи можно открыть и перейти на её страницу в магазине. Покупка, доставка и возврат оформляются у магазина.',
          ),
          SizedBox(height: 24),
          Text('Если фото не загрузилось', style: AppTextStyles.sectionTitle),
          SizedBox(height: 12),
          Text(
            'Нажмите на сообщение под значком фотографии, чтобы повторить загрузку. Если магазин удалил фото или товар, выберите другую вещь.',
          ),
        ],
      ),
    ),
  );
}
