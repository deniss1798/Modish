import '../outfits/outfit_presentation.dart';

/// Образ из Wow-moment онбординга (`POST /onboarding/complete`).
class WowOutfit {
  const WowOutfit({
    required this.id,
    required this.title,
    this.explanation,
    required this.itemCount,
    required this.apiData,
  });

  final String id;
  final String title;
  final String? explanation;
  final int itemCount;
  final Map<String, dynamic> apiData;

  factory WowOutfit.fromApi(Map<String, dynamic> json) {
    final products = json['products'];
    final items = json['items'];
    var count = 0;
    if (products is Map) count = products.length;
    if (count == 0 && items is Map) count = items.length;

    final dir = (json['style_direction'] ?? '').toString();
    final title = outfitScenarioLabel(dir);

    return WowOutfit(
      id: '${json['id'] ?? ''}',
      title: title,
      explanation: json['explanation'] as String?,
      itemCount: count,
      apiData: json,
    );
  }
}
