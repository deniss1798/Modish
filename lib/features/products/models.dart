import 'package:flutter/material.dart';

class Product {
  const Product({
    required this.id,
    required this.title,
    required this.brand,
    required this.category,
    required this.price,
    required this.currency,
    required this.imageUrl,
    required this.productUrl,
    required this.colors,
    required this.availableSizes,
  });

  final String id;
  final String title;
  final String brand;
  final String category;
  final int price;
  final String currency;
  final String imageUrl;
  final String productUrl;
  final List<String> colors;
  final List<String> availableSizes;

  factory Product.fromApi(Map<String, dynamic> json) {
    return Product(
      id: json['id'].toString(),
      title: (json['title'] ?? '').toString(),
      brand: (json['brand'] ?? '').toString(),
      category: (json['category'] ?? '').toString(),
      price: (json['price'] as num?)?.toInt() ?? 0,
      currency: (json['currency'] ?? 'RUB').toString(),
      imageUrl: (json['image_url'] ?? json['imageUrl'] ?? '').toString(),
      productUrl: (json['product_url'] ?? '').toString(),
      colors: List<String>.from((json['colors'] as List?) ?? const []),
      availableSizes: List<String>.from((json['available_sizes'] as List?) ?? const []),
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

