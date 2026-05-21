/// Адрес API для release-сборки на **реальном телефоне**.
///
/// Вариант A (рекомендуется): при сборке передать URL:
/// `flutter build apk --dart-define=MODISH_API_BASE_URL=https://api.ваш-домен.ru`
///
/// Вариант B: укажите URL здесь (без слэша в конце), если не используете dart-define.
///
/// Production API (HTTPS). Переопределяется через --dart-define=MODISH_API_BASE_URL=...
const String productionApiBaseUrl = 'https://modish.org.ru';
