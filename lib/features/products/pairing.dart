/// Слоты для подбора «с чем носить» (дополняющие категории).
enum ProductSlot { top, bottom, shoes, accessory, other }

const _topCats = {'футболки', 'рубашки', 'верхний_слой'};
const _bottomCats = {'джинсы', 'брюки'};
const _shoesCats = {'обувь'};
const _accessoryCats = {'аксессуары', 'сумки'};

ProductSlot productSlot(String? category) {
  final c = (category ?? '').trim().toLowerCase();
  if (_topCats.contains(c)) return ProductSlot.top;
  if (_bottomCats.contains(c)) return ProductSlot.bottom;
  if (_shoesCats.contains(c)) return ProductSlot.shoes;
  if (_accessoryCats.contains(c)) return ProductSlot.accessory;
  return ProductSlot.other;
}

/// Какие слоты логично показать рядом с текущим товаром.
Set<ProductSlot> complementSlots(ProductSlot slot) {
  return switch (slot) {
    ProductSlot.top => {ProductSlot.bottom, ProductSlot.shoes},
    ProductSlot.bottom => {ProductSlot.top, ProductSlot.shoes},
    ProductSlot.shoes => {ProductSlot.top, ProductSlot.bottom},
    ProductSlot.accessory => {ProductSlot.top, ProductSlot.bottom},
    ProductSlot.other => {ProductSlot.top, ProductSlot.bottom, ProductSlot.shoes},
  };
}

String pairingHint(ProductSlot slot) {
  return switch (slot) {
    ProductSlot.top => 'Низ и обувь из вашей ленты',
    ProductSlot.bottom => 'Верх и обувь из вашей ленты',
    ProductSlot.shoes => 'Верх и низ из вашей ленты',
    ProductSlot.accessory => 'Вещи из ленты, которые сочетаются',
    ProductSlot.other => 'Дополняющие вещи из ленты',
  };
}

bool _isKidsTitle(String title) {
  final t = title.toLowerCase();
  return t.contains('детск') ||
      t.contains('для детей') ||
      t.contains('kids') ||
      t.contains('junior');
}

/// Сортировка кандидатов: сначала нужный слот, затем близкая цена.
int pairingScore({
  required ProductSlot anchorSlot,
  required int anchorPrice,
  required ProductSlot candidateSlot,
  required int candidatePrice,
  required String candidateTitle,
}) {
  if (_isKidsTitle(candidateTitle)) return -1000;
  final targets = complementSlots(anchorSlot);
  var score = targets.contains(candidateSlot) ? 100 : 0;
  if (anchorPrice > 0 && candidatePrice > 0) {
    final ratio = candidatePrice / anchorPrice;
    if (ratio >= 0.35 && ratio <= 2.5) score += 30;
    if (ratio >= 0.6 && ratio <= 1.8) score += 10;
  }
  return score;
}
