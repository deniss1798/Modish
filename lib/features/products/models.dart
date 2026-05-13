import 'package:flutter/material.dart';

List<String> _imageUrlsFromJson(dynamic raw) {
  if (raw is! List) return const [];
  final out = <String>[];
  for (final e in raw) {
    final s = e?.toString().trim() ?? '';
    if (s.isNotEmpty) out.add(s);
  }
  return out;
}

class Product {
  const Product({
    required this.id,
    required this.title,
    required this.brand,
    required this.shopLabel,
    required this.category,
    required this.price,
    this.oldPrice,
    this.discountPercent,
    required this.currency,
    required this.imageUrl,
    this.imageUrls = const [],
    required this.productUrl,
    this.affiliateUrl,
    this.originalUrl,
    required this.colors,
    required this.availableSizes,
    this.categoryName,
    this.description,
    this.sizeOriginal,
    this.colorOriginal,
  });

  final String id;
  final String title;
  final String brand;
  /// Подпись витрины с бэкенда (Lamoda, бренд и т.д.), без URL.
  final String shopLabel;
  final String category;
  final int price;
  final int? oldPrice;
  final int? discountPercent;
  final String currency;
  final String imageUrl;
  /// Все URL картинок из фида (если бэкенд отдаёт).
  final List<String> imageUrls;
  final String productUrl;
  final String? affiliateUrl;
  final String? originalUrl;
  final List<String> colors;
  final List<String> availableSizes;
  final String? categoryName;
  final String? description;
  final String? sizeOriginal;
  final String? colorOriginal;

  /// Ссылка для открытия витрины (affiliate приоритетнее).
  String get outboundUrl {
    final a = (affiliateUrl ?? '').trim();
    if (a.isNotEmpty) return a;
    return productUrl.trim();
  }

  /// URL для галереи: сначала `imageUrls` с бэкенда, иначе одна `imageUrl`.
  List<String> get galleryUrls {
    String norm(String u) {
      final t = u.trim();
      if (t.isEmpty) return '';
      if (t.startsWith('//')) return 'https:$t';
      return t;
    }

    final fromFeed = imageUrls.map(norm).where((e) => e.isNotEmpty).toList();
    if (fromFeed.isNotEmpty) return fromFeed;
    final one = norm(imageUrl);
    return one.isEmpty ? const <String>[] : <String>[one];
  }

  static String _shopLabelFromJson(Map<String, dynamic> json) {
    final sl = (json['shop_label'] ?? json['shopLabel'] ?? '').toString().trim();
    if (sl.isNotEmpty) return sl;
    final b = (json['brand'] ?? '').toString().trim();
    if (b.isNotEmpty) return b;
    final src = (json['source'] ?? '').toString().trim();
    if (src.isEmpty) return 'Магазин';
    return src.replaceAll('_', ' ');
  }

  factory Product.fromApi(Map<String, dynamic> json) {
    return Product(
      id: json['id'].toString(),
      title: (json['title'] ?? '').toString(),
      brand: (json['brand'] ?? '').toString(),
      shopLabel: _shopLabelFromJson(json),
      category: (json['category'] ?? '').toString(),
      price: (json['price'] as num?)?.toInt() ?? 0,
      oldPrice: (json['old_price'] as num?)?.toInt(),
      discountPercent: (json['discount_percent'] as num?)?.toInt(),
      currency: (json['currency'] ?? 'RUB').toString(),
      imageUrl: (json['image_url'] ?? json['imageUrl'] ?? '').toString(),
      imageUrls: _imageUrlsFromJson(json['image_urls'] ?? json['imageUrls']),
      productUrl: (json['product_url'] ?? '').toString(),
      affiliateUrl: (json['affiliate_url'] ?? json['affiliateUrl'])?.toString(),
      originalUrl: (json['original_url'] ?? json['originalUrl'])?.toString(),
      colors: List<String>.from((json['colors'] as List?) ?? const []),
      availableSizes: List<String>.from((json['available_sizes'] as List?) ?? const []),
      categoryName: json['category_name']?.toString(),
      description: json['description']?.toString(),
      sizeOriginal: json['size_original']?.toString(),
      colorOriginal: json['color_original']?.toString(),
    );
  }
}

class FeedCard {
  const FeedCard({required this.product, required this.reason});
  final Product product;
  final String reason;

  factory FeedCard.fromApi(Map<String, dynamic> json) {
    final p = json['product'];
    return FeedCard(
      product: p is Map<String, dynamic> ? Product.fromApi(p) : Product.fromApi({}),
      reason: (json['reason'] ?? 'Подходит под ваш профиль').toString(),
    );
  }
}

Color colorFromName(String raw) {
  const named = <String, Color>{
    'white': Color(0xFFF4F4F4),
    'navy': Color(0xFF1C2A44),
    'gray': Color(0xFFB8BCC4),
    'graphite': Color(0xFF4A5568),
    'black': Color(0xFF111318),
    'burgundy': Color(0xFF722F37),
    'sand': Color(0xFFD4C4B0),
    'camel': Color(0xFFC19A6B),
    'cream': Color(0xFFFFF8EE),
    'indigo': Color(0xFF3F51B5),
    'olive': Color(0xFF5C6B3A),
    'brown': Color(0xFF6D4C41),
  };
  final key = raw.toLowerCase().trim();
  return named[key] ?? const Color(0xFFE5E7EB);
}
