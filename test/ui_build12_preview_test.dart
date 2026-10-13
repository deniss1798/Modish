import 'dart:async';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:modish/app.dart';
import 'package:modish/core/theme/app_theme.dart';
import 'package:modish/features/products/models.dart' as prod;
import 'app_regressions_test.dart' show FakeApi, product;

class _ImageHttp extends HttpOverrides {
  _ImageHttp(this.bytes);
  final List<int> bytes;
  @override
  HttpClient createHttpClient(SecurityContext? context) => _Client(bytes);
}

class _Client implements HttpClient {
  _Client(this.bytes);
  final List<int> bytes;
  @override
  Future<HttpClientRequest> openUrl(String method, Uri url) async =>
      _Request(bytes);
  @override
  dynamic noSuchMethod(Invocation invocation) => null;
}

class _Headers implements HttpHeaders {
  @override
  void forEach(void Function(String, List<String>) action) =>
      action('content-type', ['image/jpeg']);
  @override
  dynamic noSuchMethod(Invocation invocation) => null;
}

class _Request implements HttpClientRequest {
  _Request(this.bytes);
  final List<int> bytes;
  @override
  final HttpHeaders headers = _Headers();
  @override
  Future<HttpClientResponse> close() async => _Response(bytes);
  @override
  dynamic noSuchMethod(Invocation invocation) => null;
}

class _Response extends Stream<List<int>> implements HttpClientResponse {
  _Response(this.bytes);
  final List<int> bytes;
  @override
  int get statusCode => 200;
  @override
  int get contentLength => bytes.length;
  @override
  String get reasonPhrase => 'OK';
  @override
  bool get isRedirect => false;
  @override
  List<RedirectInfo> get redirects => [];
  @override
  HttpClientResponseCompressionState get compressionState =>
      HttpClientResponseCompressionState.notCompressed;
  @override
  final HttpHeaders headers = _Headers();
  @override
  StreamSubscription<List<int>> listen(
    void Function(List<int>)? onData, {
    Function? onError,
    void Function()? onDone,
    bool? cancelOnError,
  }) => Stream.value(bytes).listen(
    onData,
    onError: onError,
    onDone: onDone,
    cancelOnError: cancelOnError,
  );
  @override
  dynamic noSuchMethod(Invocation invocation) => null;
}

void main() {
  testWidgets('Build12 real-photo layout, tabs, system insets and large text', (
    tester,
  ) async {
    final before = HttpOverrides.current;
    addTearDown(() => HttpOverrides.global = before);
    await tester.runAsync(() async {
      HttpOverrides.global = _ImageHttp(
        await File('test/fixtures/build12-blouse.jpg').readAsBytes(),
      );
      await (FontLoader(
        'MaterialIcons',
      )..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf'))).load();
      for (final f in {
        'Arial': 'arial.ttf',
        'Georgia': 'georgia.ttf',
      }.entries) {
        final bytes = await File('C:/Windows/Fonts/${f.value}').readAsBytes();
        await (FontLoader(
          f.key,
        )..addFont(Future.value(ByteData.sublistView(bytes)))).load();
      }
    });
    late ThemeData theme;
    await tester.pumpWidget(
      Builder(
        builder: (context) {
          theme = AppTheme.dark;
          return const SizedBox();
        },
      ),
    );
    final p = {
      ...product('blouse'),
      'title': 'Блузка из воздушного хлопка',
      'brand': 'BAON',
      'shop_label': 'BAON',
      'price': 4599,
      'old_price': 5999,
      'discount_percent': 23,
      'available_sizes': ['M', 'L'],
      'image_url': 'https://preview.test/blouse.jpg',
    };
    final c = AppController(apiClient: FakeApi())
      ..email = 'anna@example.test'
      ..productFeed = [
        prod.FeedCard(
          product: prod.Product.fromApi(p),
          reason: '',
          reasons: ['В вашем бюджете'],
        ),
      ]
      ..savedProductRows = [
        {'product': p},
      ]
      ..fitProfile = {'clothing_size': 'M', 'budget_max': 10000}
      ..outfits = [
        {
          'id': 'o',
          'title': 'Каждый день',
          'style_direction': 'daily',
          'total_price': 8999,
          'products': {
            'top': p,
            'bottom': {...p, 'title': 'Брюки'},
            'shoes': {...p, 'title': 'Обувь'},
          },
        },
      ];
    for (final size in [const Size(360, 640), const Size(390, 844)]) {
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1;
      tester.view.padding = const FakeViewPadding(top: 28, bottom: 48);
      tester.view.viewPadding = const FakeViewPadding(top: 28, bottom: 48);
      for (final tab in [0, 1, 2, 3]) {
        c.tab = tab;
        final key = GlobalKey();
        await tester.pumpWidget(
          RepaintBoundary(
            key: key,
            child: MaterialApp(
              debugShowCheckedModeBanner: false,
              theme: theme,
              builder: (context, child) => MediaQuery(
                data: MediaQuery.of(
                  context,
                ).copyWith(textScaler: const TextScaler.linear(1.15)),
                child: child!,
              ),
              home: HomeScreen(controller: c),
            ),
          ),
        );
        await tester.pumpAndSettle();
        // Image decoding runs on the real IO clock, outside the test clock.
        await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 250)),
        );
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull, reason: 'tab $tab at $size');
        await tester.runAsync(() async {
          final image =
              await (key.currentContext!.findRenderObject()
                      as RenderRepaintBoundary)
                  .toImage();
          final bytes = (await image.toByteData(
            format: ui.ImageByteFormat.png,
          ))!;
          final file = File(
            'docs/modish2/build12-preview/tab$tab-${size.width.toInt()}.png',
          );
          await file.parent.create(recursive: true);
          await file.writeAsBytes(bytes.buffer.asUint8List());
          image.dispose();
        });
        await tester.pumpWidget(const SizedBox());
      }
    }
    c.dispose();
    tester.view.resetPhysicalSize();
    tester.view.resetDevicePixelRatio();
    tester.view.resetPadding();
    tester.view.resetViewPadding();
  });
}
