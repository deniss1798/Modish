/// Global source availability is separate from a personal size/style match.
enum CatalogAvailability {
  unknown,
  ready,
  refreshRequired,
  empty;

  static CatalogAvailability fromApi(Map<String, dynamic> value) {
    if (value['scope'] != 'global' || value['personalized'] != false) {
      return unknown;
    }
    return switch (value['status']) {
      'ready' => ready,
      'refresh_required' => refreshRequired,
      'empty' => empty,
      _ => unknown,
    };
  }

  bool get blocksSelection => this == refreshRequired || this == empty;

  String? get title => switch (this) {
    refreshRequired => 'Подборка временно недоступна',
    empty => 'В каталоге пока нет доступных вещей',
    _ => null,
  };

  String? get message => switch (this) {
    refreshRequired =>
      'Нужно обновить данные магазинов о наличии. Ваши параметры подборки сохранены.',
    empty =>
      'Когда появятся доступные товары, мы сможем подобрать вещи. Ваши параметры сохранены.',
    _ => null,
  };
}
