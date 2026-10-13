import 'dart:typed_data';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/core/network/api_client.dart';

class RecordingAdapter implements HttpClientAdapter {
  Uri? uri;
  dynamic data;
  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    uri = options.uri;
    data = options.data;
    return ResponseBody.fromString(
      options.path == '/fit-profile/me' ? '{}' : '[]',
      200,
      headers: {
        Headers.contentTypeHeader: [Headers.jsonContentType],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

void main() {
  test('Size systems are explicit in onboarding and edited profile', () async {
    final adapter = RecordingAdapter();
    final dio = Dio(BaseOptions(baseUrl: 'https://example.test'))
      ..httpClientAdapter = adapter;
    final api = ApiClient(dio: dio);
    await api.activationPreferences(
      ageGroup: '25-34',
      clothingSize: '44',
      shoeSize: '38',
      styles: ['casual', 'minimalism'],
      priceSegment: 'mass',
      height: 168,
    );
    expect(adapter.data['clothing_size_system'], 'RU');
    expect(adapter.data['shoe_size_system'], 'EU');
    expect(adapter.data['height'], 168);
    await api.fitProfilePatch(
      height: 170,
      genderTarget: 'womenswear',
      clothingSize: 'M',
      shoeSize: '39',
      budgetMin: 0,
      budgetMax: 10000,
    );
    expect(adapter.data['clothing_size_system'], 'LETTER');
    expect(adapter.data['shoe_size_system'], 'EU');
    dio.close();
  });
  test('Catalogue search sends the query, not a brand filter', () async {
    final adapter = RecordingAdapter();
    final dio = Dio(BaseOptions(baseUrl: 'https://example.test'))
      ..httpClientAdapter = adapter;
    await ApiClient(
      dio: dio,
    ).productsList(query: '  чёрные брюки  ', offset: 40);
    expect(adapter.uri!.queryParameters['q'], 'чёрные брюки');
    expect(adapter.uri!.queryParameters['offset'], '40');
    expect(adapter.uri!.queryParameters.containsKey('brand'), isFalse);
    dio.close();
  });
  test('Complement request targets the selected garment', () async {
    final adapter = RecordingAdapter();
    final dio = Dio(BaseOptions(baseUrl: 'https://example.test'))
      ..httpClientAdapter = adapter;
    final api = ApiClient(dio: dio);
    expect(await api.productComplements('selected-item'), isEmpty);
    expect(adapter.uri!.path, '/products/selected-item/complements');
    dio.close();
  });
  test(
    'Selected filters reach the HTTP endpoint with repeated query keys',
    () async {
      final adapter = RecordingAdapter();
      final dio = Dio(BaseOptions(baseUrl: 'https://example.test'))
        ..httpClientAdapter = adapter;
      final api = ApiClient(dio: dio);
      await api.productFeed(
        minPrice: 500,
        maxPrice: 20000,
        categories: ['tops', 'bottoms'],
        sizes: ['M', '50'],
        colors: ['Черный', 'Белый'],
      );
      expect(adapter.uri!.path, '/feed');
      expect(adapter.uri!.queryParametersAll, {
        'limit': ['30'],
        'min_price': ['500'],
        'max_price': ['20000'],
        'categories': ['tops', 'bottoms'],
        'sizes': ['M', '50'],
        'colors': ['Черный', 'Белый'],
      });
      await api.productFeed();
      expect(adapter.uri!.queryParametersAll, {
        'limit': ['30'],
      });
      await api.productFeed(scenario: 'office');
      expect(adapter.uri!.queryParameters['scenario'], 'office');
      dio.close();
    },
  );
}
