import 'package:flutter/material.dart';

import '../theme/app_colors.dart';

/// Сеть: User-Agent для CDN; плейсхолдеры при загрузке/ошибке.
/// Если основной URL не открылся — один раз пробуем стабильный Picsum /id/ (битые демо-URL в БД).
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
  static const Map<String, String> _headers = {
    'User-Agent': 'Modish/1.0 (Flutter; fashion demo)',
  };

  static const String _fallbackUrl = 'https://picsum.photos/id/64/600/800';

  late String _url;
  bool _triedFallback = false;
  bool _fallbackScheduled = false;

  @override
  void initState() {
    super.initState();
    _url = widget.imageUrl.trim();
  }

  @override
  void didUpdateWidget(ProductFillImage oldWidget) {
    super.didUpdateWidget(oldWidget);
    final next = widget.imageUrl.trim();
    if (next != oldWidget.imageUrl.trim()) {
      _triedFallback = false;
      _fallbackScheduled = false;
      _url = next;
    }
  }

  void _scheduleFallback() {
    if (_triedFallback || _fallbackScheduled) return;
    if (_url.isEmpty || _url == _fallbackUrl) return;
    _fallbackScheduled = true;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      setState(() {
        _fallbackScheduled = false;
        _triedFallback = true;
        _url = _fallbackUrl;
      });
    });
  }

  @override
  Widget build(BuildContext context) {
    if (_url.isEmpty) {
      return DecoratedBox(
        decoration: BoxDecoration(
          color: AppColors.line.withValues(alpha: 0.45),
          borderRadius: widget.borderRadius,
        ),
        child: const Center(
          child: Icon(Icons.image_not_supported_outlined, size: 40, color: AppColors.muted),
        ),
      );
    }

    Widget image = Image.network(
      _url,
      headers: _headers,
      fit: BoxFit.cover,
      width: double.infinity,
      height: double.infinity,
      gaplessPlayback: true,
      errorBuilder: (context, error, stackTrace) {
        if (_triedFallback) {
          return DecoratedBox(
            decoration: BoxDecoration(
              color: AppColors.line.withValues(alpha: 0.35),
              borderRadius: widget.borderRadius,
            ),
            child: const Center(
              child: Icon(Icons.broken_image_outlined, size: 40, color: AppColors.muted),
            ),
          );
        }
        _scheduleFallback();
        return DecoratedBox(
          decoration: BoxDecoration(
            color: AppColors.line.withValues(alpha: 0.25),
            borderRadius: widget.borderRadius,
          ),
          child: const Center(
            child: SizedBox(
              width: 28,
              height: 28,
              child: CircularProgressIndicator(strokeWidth: 2.5, color: AppColors.accent),
            ),
          ),
        );
      },
      loadingBuilder: (context, child, loadingProgress) {
        if (loadingProgress == null) return child;
        return DecoratedBox(
          decoration: BoxDecoration(
            color: AppColors.line.withValues(alpha: 0.25),
            borderRadius: widget.borderRadius,
          ),
          child: const Center(
            child: SizedBox(
              width: 28,
              height: 28,
              child: CircularProgressIndicator(strokeWidth: 2.5, color: AppColors.accent),
            ),
          ),
        );
      },
    );

    if (widget.borderRadius == BorderRadius.zero) {
      return image;
    }
    return ClipRRect(borderRadius: widget.borderRadius, child: image);
  }
}
