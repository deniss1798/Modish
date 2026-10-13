/// Display labels only. Merchant values remain unchanged in requests/models.
const catalogColorLabels = <String, String>{
  'black': 'Чёрный',
  'white': 'Белый',
  'grey': 'Серый',
  'gray': 'Серый',
  'graphite': 'Графитовый',
  'navy': 'Тёмно-синий',
  'blue': 'Синий',
  'light_blue': 'Голубой',
  'beige': 'Бежевый',
  'brown': 'Коричневый',
  'red': 'Красный',
  'burgundy': 'Бордовый',
  'green': 'Зелёный',
  'olive': 'Оливковый',
  'khaki': 'Хаки',
  'pink': 'Розовый',
  'purple': 'Фиолетовый',
  'yellow': 'Жёлтый',
  'orange': 'Оранжевый',
  'cream': 'Кремовый',
  'ivory': 'Слоновая кость',
  'gold': 'Золотистый',
  'silver': 'Серебристый',
  'multicolor': 'Разноцветный',
};

String catalogColorLabel(String raw) =>
    catalogColorLabels[raw.trim().toLowerCase()] ?? raw;
