/// Automatic suggestions are deliberately small; saved collections are not.
const maxOutfitRecommendations = 3;

const noOutfitRecommendationsMessage =
    'Пока нет подходящих образов. Попробуйте изменить предпочтения или посмотреть каталог.';

List<T> limitOutfitRecommendations<T>(Iterable<T> outfits) =>
    outfits.take(maxOutfitRecommendations).toList(growable: false);
