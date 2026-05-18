/// Адрес API для release-сборки на **реальном телефоне**.
///
/// Вариант A (рекомендуется): при сборке передать URL:
/// `flutter build apk --dart-define=MODISH_API_BASE_URL=https://api.ваш-домен.ru`
///
/// Вариант B: укажите URL здесь (без слэша в конце), если не используете dart-define.
///
/// VPS alpha (HTTP). После настройки HTTPS замените на https://api.ваш-домен.ru
const String productionApiBaseUrl = 'http://194.87.118.236';
