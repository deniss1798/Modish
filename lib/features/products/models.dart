import 'package:flutter/material.dart';

// Keep the API evidence as received: flat display fields alone cannot detect
// changes in per-size stock, provenance, or canonical construction evidence.
const _recommendationFactFields = {
  'id',
  'external_id',
  'offer_id',
  'source',
  'source_id',
  'title',
  'description',
  'brand',
  'category',
  'category_name',
  'subcategory',
  'merchant_category',
  'merchant_subcategory',
  'outfit_slot',
  'price',
  'old_price',
  'discount_percent',
  'currency',
  'available_sizes',
  'available_sizes_detailed',
  'size_system',
  'size_original',
  'colors',
  'color_family',
  'color_original',
  'material',
  'season',
  'occasion',
  'gender_target',
  'fit',
  'silhouette',
  'style_tags',
  'raw_params_json',
  'feed_raw_json',
  'recommendation_evidence_version',
  'image_quality_score',
  'is_available',
  'availability_status',
  'availability_state',
  'availability_confidence',
  'availability_reason',
  'is_active',
  'is_deleted_from_feed',
};

dynamic _immutableApiValue(dynamic value) {
  if (value is Map) {
    return Map<String, dynamic>.unmodifiable({
      for (final entry in value.entries)
        entry.key.toString(): _immutableApiValue(entry.value),
    });
  }
  if (value is List) {
    return List<dynamic>.unmodifiable(value.map(_immutableApiValue));
  }
  return value;
}

Map<String, dynamic> _recommendationFactsFromApi(Map<String, dynamic> json) =>
    Map<String, dynamic>.unmodifiable({
      for (final field in _recommendationFactFields)
        if (json.containsKey(field)) field: _immutableApiValue(json[field]),
    });

List<String> _imageUrlsFromJson(dynamic raw) {
  if (raw is! List) return const [];
  final out = <String>[];
  for (final e in raw) {
    final s = e?.toString().trim() ?? '';
    if (s.isNotEmpty) out.add(s);
  }
  return out;
}

class MerchantSize {
  const MerchantSize({
    required this.rawSize,
    this.normalizedSize,
    this.sizeSystem = 'UNKNOWN',
    this.availability = 'unknown',
  });
  final String rawSize;
  final String? normalizedSize;
  final String sizeSystem;
  final String availability;

  factory MerchantSize.fromApi(Map<String, dynamic> json) {
    final system = '${json['size_system'] ?? ''}'.toUpperCase();
    final availability = '${json['availability'] ?? ''}';
    return MerchantSize(
      rawSize: '${json['raw_size'] ?? ''}',
      normalizedSize: json['normalized_size']?.toString(),
      sizeSystem: {'RU', 'EU', 'UK', 'US', 'LETTER', 'WAIST'}.contains(system)
          ? system
          : 'UNKNOWN',
      availability: {'available', 'unavailable'}.contains(availability)
          ? availability
          : 'unknown',
    );
  }

  String get label => sizeSystem == 'UNKNOWN' || sizeSystem == 'LETTER'
      ? rawSize
      : '$rawSize ($sizeSystem)';
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
    this.source = '',
    this.groupId = '',
    this.vendorCode = '',
    this.outfitSlot,
    this.recommendationFacts = const {},
    this.catalogueDiscovery = false,
    this.sizeVerification = '',
    this.merchantSizes = const [],
    this.sizeNotice = '',
  });

  final String id;
  final String title;
  final String brand;

  /// Подпись витрины с бэкенда (Lamoda, бренд и т.д.), без URL.
  final String shopLabel;
  final String category;
  final num price;
  final num? oldPrice;
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
  final String source;
  final String groupId;
  final String vendorCode;
  final String? outfitSlot;

  /// Immutable API facts supporting the recommendation that supplied this item.
  /// Presentation URLs and observation timestamps do not change these facts.
  final Map<String, dynamic> recommendationFacts;
  final bool catalogueDiscovery;
  final String sizeVerification;
  final List<MerchantSize> merchantSizes;
  final String sizeNotice;

  Set<String> get identityKeys {
    final keys = <String>{'id:$id'};
    // The merchant page ties size offers together even when their photos differ.
    // Keep identity-bearing query parameters; remove tracking and size only.
    final merchant = Uri.tryParse(originalUrl?.trim() ?? '');
    if (merchant != null &&
        {'http', 'https'}.contains(merchant.scheme) &&
        merchant.host.isNotEmpty &&
        merchant.path.trim() != '/' &&
        merchant.path.isNotEmpty) {
      final query = <String, dynamic>{};
      for (final key in merchant.queryParametersAll.keys.toList()..sort()) {
        if (key.toLowerCase().startsWith('utm_') ||
            {
              'gclid',
              'yclid',
              'fbclid',
              'size',
              'size_id',
            }.contains(key.toLowerCase())) {
          continue;
        }
        query[key] = merchant.queryParametersAll[key];
      }
      final path = merchant.path.replaceFirst(RegExp(r'/+$'), '');
      keys.add(
        'merchant:${merchant.host.toLowerCase().replaceFirst(RegExp(r'^www\.'), '')}$path${query.isEmpty ? '' : '?${Uri(queryParameters: query).query}'}',
      );
    }
    for (final pair in [('group_id', groupId), ('vendor_code', vendorCode)]) {
      if (source.isNotEmpty &&
          pair.$2.isNotEmpty &&
          !{'0', 'none', 'null', 'unknown'}.contains(pair.$2.toLowerCase())) {
        keys.add('${pair.$1}:${source.toLowerCase()}:${pair.$2}');
      }
    }
    final uri = Uri.tryParse(
      imageUrl.startsWith('//') ? 'https:$imageUrl' : imageUrl,
    );
    if (uri != null && uri.host.isNotEmpty && uri.path.isNotEmpty) {
      final query = uri.hasQuery && !uri.path.split('/').last.contains('.')
          ? '?${uri.query}'
          : '';
      keys.add('image:${uri.host.toLowerCase()}${uri.path}$query');
    }
    return keys;
  }

  /// Ссылка для открытия витрины (affiliate приоритетнее).
  String get outboundUrl {
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

    final fromFeed = {
      imageUrl,
      ...imageUrls,
    }.map(norm).where((e) => e.isNotEmpty).toList();
    if (fromFeed.isNotEmpty) return fromFeed;
    final one = norm(imageUrl);
    return one.isEmpty ? const <String>[] : <String>[one];
  }

  static String _shopLabelFromJson(Map<String, dynamic> json) {
    final sl = (json['shop_label'] ?? json['shopLabel'] ?? '')
        .toString()
        .trim();
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
      price: (json['price'] as num?) ?? 0,
      oldPrice: json['old_price'] as num?,
      discountPercent: (json['discount_percent'] as num?)?.toInt(),
      currency: (json['currency'] ?? 'RUB').toString(),
      imageUrl: (json['image_url'] ?? json['imageUrl'] ?? '').toString(),
      imageUrls: _imageUrlsFromJson(json['image_urls'] ?? json['imageUrls']),
      productUrl: (json['outbound_url'] ?? json['product_url'] ?? '')
          .toString(),
      affiliateUrl: (json['affiliate_url'] ?? json['affiliateUrl'])?.toString(),
      originalUrl: (json['original_url'] ?? json['originalUrl'])?.toString(),
      colors: List<String>.from((json['colors'] as List?) ?? const []),
      availableSizes: List<String>.from(
        (json['available_sizes'] as List?) ?? const [],
      ),
      categoryName: json['category_name']?.toString(),
      description: json['description']?.toString(),
      sizeOriginal: json['size_original']?.toString(),
      colorOriginal: json['color_original']?.toString(),
      source: (json['source'] ?? '').toString().trim(),
      groupId: (json['group_id'] ?? '').toString().trim(),
      vendorCode: (json['vendor_code'] ?? '').toString().trim(),
      outfitSlot: json['outfit_slot']?.toString(),
      recommendationFacts: _recommendationFactsFromApi(json),
      catalogueDiscovery: json['catalogue_discovery'] == true,
      sizeVerification: '${json['size_verification'] ?? ''}',
      merchantSizes: List<MerchantSize>.unmodifiable(
        (json['merchant_sizes'] as List? ?? const [])
            .whereType<Map>()
            .map((row) => MerchantSize.fromApi(Map<String, dynamic>.from(row)))
            .where((size) => size.rawSize.trim().isNotEmpty),
      ),
      sizeNotice: '${json['size_notice'] ?? ''}',
    );
  }
}

class FeedCard {
  const FeedCard({
    required this.product,
    required this.reason,
    this.reasons = const [],
  });
  final Product product;
  final String reason;
  final List<String> reasons;

  factory FeedCard.fromApi(Map<String, dynamic> json) {
    final p = json['product'];
    final rs = json['reasons'];
    return FeedCard(
      product: p is Map<String, dynamic>
          ? Product.fromApi(p)
          : Product.fromApi({}),
      reason: (json['reason'] ?? 'Подходит под ваш профиль').toString(),
      reasons: rs is List ? rs.map((e) => e.toString()).toList() : const [],
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
