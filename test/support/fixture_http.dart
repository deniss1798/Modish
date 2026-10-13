import 'dart:async';
import 'dart:io';

/// In-memory image transport; unexpected hosts never open a socket.
class FixtureHttpOverrides extends HttpOverrides {
  FixtureHttpOverrides(this.bytes);
  final List<int> bytes;
  final requests = <String>[];
  @override
  HttpClient createHttpClient(SecurityContext? context) =>
      _FixtureClient(bytes, requests);
}

class _FixtureClient implements HttpClient {
  _FixtureClient(this.bytes, this.requests);
  final List<int> bytes;
  final List<String> requests;
  @override
  Future<HttpClientRequest> openUrl(String method, Uri url) async {
    // A missing FakeAPI override cannot silently reach an API through this stub.
    if (url.host != 'ui-fixture.invalid') {
      throw StateError(
        'Unexpected non-fixture HTTP request: ${url.host}${url.path}',
      );
    }
    requests.add(url.toString());
    return _FixtureRequest(bytes);
  }

  @override
  Future<HttpClientRequest> getUrl(Uri url) => openUrl('GET', url);
  @override
  dynamic noSuchMethod(Invocation invocation) => null;
}

class _FixtureHeaders implements HttpHeaders {
  @override
  void forEach(void Function(String, List<String>) action) =>
      action('content-type', ['image/jpeg']);
  @override
  String? value(String name) =>
      name.toLowerCase() == 'content-type' ? 'image/jpeg' : null;
  @override
  dynamic noSuchMethod(Invocation invocation) => null;
}

class _FixtureRequest implements HttpClientRequest {
  _FixtureRequest(this.bytes);
  final List<int> bytes;
  @override
  final HttpHeaders headers = _FixtureHeaders();
  @override
  bool followRedirects = true;
  @override
  int maxRedirects = 8;
  @override
  bool persistentConnection = true;
  @override
  Future<HttpClientResponse> close() async => _FixtureResponse(bytes);
  @override
  void abort([Object? exception, StackTrace? stackTrace]) {}
  @override
  dynamic noSuchMethod(Invocation invocation) => null;
}

class _FixtureResponse extends Stream<List<int>> implements HttpClientResponse {
  _FixtureResponse(this.bytes);
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
  final HttpHeaders headers = _FixtureHeaders();
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
