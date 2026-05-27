/// Слоты образа для подбора «с чем носить».
enum ProductSlot { top, bottom, shoes, accessory, other }

const _topCats = {'футболки', 'рубашки', 'верхний_слой'};
const _bottomCats = {'джинсы', 'брюки'};
const _shoesCats = {'обувь'};
const _accessoryCats = {'аксессуары', 'сумки'};

ProductSlot productSlotFromCategory(String category) {
  final c = category.trim().toLowerCase();
  if (_topCats.contains(c)) return ProductSlot.top;
  if (_bottomCats.contains(c)) return ProductSlot.bottom;
  if (_shoesCats.contains(c)) return ProductSlot.shoes;
  if (_accessoryCats.contains(c)) return ProductSlot.accessory;
  return ProductSlot.other;
}

/// Какие слоты логично предложить к текущей вещи.
Set<ProductSlot> complementSlotsFor(ProductSlot slot) {
  return switch (slot) {
    ProductSlot.top => {ProductSlot.bottom, ProductSlot.shoes},
    ProductSlot.bottom => {ProductSlot.top, ProductSlot.shoes},
    ProductSlot.shoes => {ProductSlot.top, ProductSlot.bottom},
    ProductSlot.accessory => {ProductSlot.top, ProductSlot.bottom},
    ProductSlot.other => {ProductSlot.top, ProductSlot.bottom, ProductSlot.shoes},
  };
}

String complementHintRu(ProductSlot slot) {
  return switch (slot) {
    ProductSlot.top => 'Низ и обувь из вашей ленты',
    ProductSlot.bottom => 'Верх и обувь из вашей ленты',
    ProductSlot.shoes => 'Верх и низ из вашей ленты',
    ProductSlot.accessory => 'Вещи из ленты к аксессуару',
    ProductSlot.other => 'Другие вещи из ленты',
  };
}

bool isKidsProductTitle(String title) {
  final t = title.toLowerCase();
  return t.contains('детск') ||
      t.contains('kids') ||
      t.contains('junior') ||
      t.contains('мальчик') ||
      t.contains('девочк');
}
