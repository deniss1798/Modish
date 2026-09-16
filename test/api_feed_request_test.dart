import 'dart:typed_data';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/core/network/api_client.dart';

class RecordingAdapter implements HttpClientAdapter {
  Uri? uri;
  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    uri = options.uri;
    return ResponseBody.fromString(
      '[]',
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
      dio.close();
    },
  );
}
