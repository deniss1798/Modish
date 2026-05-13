import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../network/api_client.dart';
import '../theme/app_colors.dart';

String _normalizeImageUrl(String raw) {
  final u = raw.trim();
  if (u.isEmpty) return '';
  if (u.startsWith('//')) return 'https:$u';
  return u;
}

bool _isDemoOrGenericImageUrl(String url) {
  final uri = Uri.tryParse(url);
  if (uri == null) return false;
  final host = uri.host.toLowerCase();
  if (host.contains('picsum.photos')) return true;
  if (host.contains('placehold.co')) return true;
  if (host.contains('dummyimage.com')) return true;
  return false;
}

Map<String, String> _imageRequestHeaders(String url) {
  const ua =
      'Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36';
  final h = <String, String>{
    'User-Agent': ua,
    'Accept': 'image/jpeg,image/png,image/webp,image/apng,image/*,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
    'Sec-Fetch-Dest': 'image',
    'Sec-Fetch-Mode': 'no-cors',
    'Sec-Fetch-Site': 'cross-site',
  };
  final uri = Uri.tryParse(url);
  if (uri != null && uri.hasScheme && uri.host.isNotEmpty) {
    final host = uri.host.toLowerCase();
    if (host.contains('lmcdn') || host.contains('lamoda')) {
      h['Referer'] = 'https://www.lamoda.ru/';
      h['Origin'] = 'https://www.lamoda.ru';
    } else if (host.contains('befree') ||
        host.contains('mrp.ru') ||
        host.contains('mr-b.ru') ||
        host.contains('mrprice')) {
      h['Referer'] = 'https://befree.ru/';
      h['Origin'] = 'https://befree.ru';
    } else {
      h['Referer'] = '${uri.scheme}://${uri.host}/';
    }
  }
  return h;
}

/// Если эмулятор не ходит в интернет по HTTPS, грузим то же изображение через API-хост.
String _effectiveFetchUrl(String originalHttps) {
  if (!ApiClient.useImageProxyForProductImages()) {
    return originalHttps;
  }
  final base = ApiClient.resolvedBaseUrl().replaceAll(RegExp(r'/+$'), '');
  return '$base/media/proxy-image?url=${Uri.encodeQueryComponent(originalHttps)}';
}

/// Заголовки для прямого запроса к CDN. Для прокси не нужны (сервер сам шлёт Referer).
/// Каскад URL при сбое/таймауте (CDN без Referer, гео-блок и т.д.).
class ProductFillImage extends StatefulWidget {
  const ProductFillImage({
    super.key,
    required this.imageUrl,
    this.borderRadius = BorderRadius.zero,
  });

  final String imageUrl;
  final BorderRadius borderRadius;

  @override
  State<ProductFillImage> createState() => _ProductFillImageState();
}

class _ProductFillImageState extends State<ProductFillImage> {
  static final Dio _dio = Dio(
    BaseOptions(
      connectTimeout: const Duration(seconds: 25),
      receiveTimeout: const Duration(seconds: 45),
      followRedirects: true,
      maxRedirects: 8,
      validateStatus: (code) => code != null && code >= 200 && code < 500,
      responseType: ResponseType.bytes,
    ),
  );

  static const List<String> _fallbackUrls = [];

  late List<String> _candidates;
  int _index = 0;
  int _requestGen = 0;
  Uint8List? _bytes;
  bool _loading = false;
  bool _decodeAdvanceScheduled = false;
  Timer? _stallTimer;

  @override
  void initState() {
    super.initState();
    _candidates = _buildCandidates(_normalizeImageUrl(widget.imageUrl));
    _kickLoad();
  }

  @override
  void dispose() {
    _stallTimer?.cancel();
    super.dispose();
  }

  @override
  void didUpdateWidget(covariant ProductFillImage oldWidget) {
    super.didUpdateWidget(oldWidget);
    final next = _normalizeImageUrl(widget.imageUrl);
    final prev = _normalizeImageUrl(oldWidget.imageUrl);
    if (next != prev) {
      _stallTimer?.cancel();
      _requestGen++;
      setState(() {
        _candidates = _buildCandidates(next);
        _index = 0;
        _bytes = null;
        _loading = false;
        _decodeAdvanceScheduled = false;
      });
      _kickLoad();
    }
  }

  void _armStallTimer(String url, int gen) {
    _stallTimer?.cancel();
    if (_candidates.isEmpty || gen != _requestGen) return;
    _stallTimer = Timer(const Duration(seconds: 20), () {
      if (!mounted || gen != _requestGen) return;
      if (_bytes != null) return;
      debugPrint(
        'ProductFillImage: stall (no bytes in 20s) gen=$gen idx=$_index url=$url',
      );
      _advanceAfterFailure();
    });
  }

  List<String> _buildCandidates(String primary) {
    if (_isDemoOrGenericImageUrl(primary)) return const [];
    if (primary.isEmpty) return const [];
    final out = <String>[primary];
    for (final fb in _fallbackUrls) {
      if (fb != primary && !out.contains(fb)) {
        out.add(fb);
      }
    }
    return out;
  }

  void _kickLoad() {
    if (_candidates.isEmpty || _index >= _candidates.length) return;
    final url = _candidates[_index];
    final gen = ++_requestGen;
    setState(() {
      _bytes = null;
      _loading = true;
      _decodeAdvanceScheduled = false;
    });
    _armStallTimer(url, gen);

    final headers = _effectiveFetchUrl(url) == url
        ? _imageRequestHeaders(url)
        : const <String, String>{};
    if (kDebugMode) {
      final via = _effectiveFetchUrl(url) == url ? 'direct' : 'proxy';
      debugPrint('ProductFillImage: GET idx=$_index via=$via url=$url');
    }

    _dio
        .get<List<int>>(
          _effectiveFetchUrl(url),
          options: Options(headers: headers),
        )
        .then((resp) {
          if (!mounted || gen != _requestGen) return;
          _stallTimer?.cancel();
          final code = resp.statusCode ?? 0;
          final data = resp.data;
          final ct = (resp.headers.value('content-type') ?? '').toLowerCase();

          if (code != 200 || data == null || data.isEmpty) {
            debugPrint(
              'ProductFillImage: bad response code=$code bytes=${data?.length ?? 0} ct=$ct url=$url',
            );
            _advanceAfterFailure();
            return;
          }
          if (ct.contains('text/html') ||
              ct.contains('application/json') ||
              ct.contains('application/xml')) {
            debugPrint(
              'ProductFillImage: rejected content-type="$ct" (likely error page) url=$url',
            );
            _advanceAfterFailure();
            return;
          }
          if (ct.isNotEmpty &&
              !ct.startsWith('image/') &&
              !ct.contains('octet-stream')) {
            debugPrint(
              'ProductFillImage: unexpected content-type="$ct" url=$url (trying decode anyway)',
            );
          }

          setState(() {
            _bytes = Uint8List.fromList(data);
            _loading = false;
          });
        })
        .catchError((Object e, StackTrace? st) {
          if (!mounted || gen != _requestGen) return;
          _stallTimer?.cancel();
          debugPrint('ProductFillImage: dio error idx=$_index url=$url → $e');
          if (kDebugMode && st != null) {
            debugPrint('$st');
          }
          _advanceAfterFailure();
        });
  }

  void _advanceAfterFailure() {
    if (!mounted) return;
    setState(() {
      _loading = false;
      _bytes = null;
      if (_index + 1 < _candidates.length) {
        _index++;
      } else {
        _index = _candidates.length;
      }
    });
    if (_index < _candidates.length) {
      _kickLoad();
    }
  }

  void _onDecodeError(Object error, StackTrace? stack) {
    if (_decodeAdvanceScheduled) return;
    _decodeAdvanceScheduled = true;
    debugPrint(
      'ProductFillImage: Image.memory decode error idx=$_index → $error',
    );
    if (kDebugMode && stack != null) {
      debugPrint('$stack');
    }
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _advanceAfterFailure();
    });
  }

  @override
  Widget build(BuildContext context) {
    final clip = widget.borderRadius != BorderRadius.zero;
    final norm = _normalizeImageUrl(widget.imageUrl);

    if (norm.isEmpty) {
      return _wrapClip(_placeholder(Icons.image_not_supported_outlined), clip);
    }

    if (_index >= _candidates.length) {
      return _wrapClip(_placeholder(Icons.broken_image_outlined), clip);
    }

    final img = LayoutBuilder(
      builder: (context, c) {
        if (!c.hasBoundedWidth ||
            !c.hasBoundedHeight ||
            c.maxWidth <= 1 ||
            c.maxHeight <= 1) {
          return DecoratedBox(
            decoration: BoxDecoration(
              color: AppColors.line.withValues(alpha: 0.22),
              borderRadius: widget.borderRadius,
            ),
            child: const Center(
              child: SizedBox(
                width: 28,
                height: 28,
                child: CircularProgressIndicator(
                  strokeWidth: 2.5,
                  color: AppColors.accent,
                ),
              ),
            ),
          );
        }

        if (_bytes != null) {
          return Image.memory(
            _bytes!,
            key: ValueKey<int>(_bytes!.length + _index),
            fit: BoxFit.cover,
            width: c.maxWidth,
            height: c.maxHeight,
            gaplessPlayback: true,
            errorBuilder: (context, error, stackTrace) {
              _onDecodeError(error, stackTrace);
              return DecoratedBox(
                decoration: BoxDecoration(
                  color: AppColors.line.withValues(alpha: 0.22),
                  borderRadius: widget.borderRadius,
                ),
                child: const Center(
                  child: SizedBox(
                    width: 28,
                    height: 28,
                    child: CircularProgressIndicator(
                      strokeWidth: 2.5,
                      color: AppColors.accent,
                    ),
                  ),
                ),
              );
            },
          );
        }

        if (_loading) {
          return DecoratedBox(
            decoration: BoxDecoration(
              color: AppColors.line.withValues(alpha: 0.22),
              borderRadius: widget.borderRadius,
            ),
            child: const Center(
              child: SizedBox(
                width: 28,
                height: 28,
                child: CircularProgressIndicator(
                  strokeWidth: 2.5,
                  color: AppColors.accent,
                ),
              ),
            ),
          );
        }

        return DecoratedBox(
          decoration: BoxDecoration(
            color: AppColors.line.withValues(alpha: 0.22),
            borderRadius: widget.borderRadius,
          ),
          child: const Center(
            child: SizedBox(
              width: 28,
              height: 28,
              child: CircularProgressIndicator(
                strokeWidth: 2.5,
                color: AppColors.accent,
              ),
            ),
          ),
        );
      },
    );

    return _wrapClip(img, clip);
  }

  Widget _wrapClip(Widget child, bool clip) {
    if (!clip) return child;
    return ClipRRect(borderRadius: widget.borderRadius, child: child);
  }

  Widget _placeholder(IconData icon) {
    return DecoratedBox(
      decoration: BoxDecoration(
        color: AppColors.line.withValues(alpha: 0.4),
        borderRadius: widget.borderRadius,
      ),
      child: Center(child: Icon(icon, size: 40, color: AppColors.muted)),
    );
  }
}
