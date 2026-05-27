import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/network/api_client.dart';

class BrandsScreen extends StatefulWidget {
  const BrandsScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<BrandsScreen> createState() => _BrandsScreenState();
}

class _BrandsScreenState extends State<BrandsScreen> {
  Map<String, int> _brands = {};
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Map<String, int> _brandsFromFeed() {
    final counts = <String, int>{};
    for (final c in widget.controller.productFeed) {
      final b = c.product.brand.trim();
      if (b.isEmpty) continue;
      counts[b] = (counts[b] ?? 0) + 1;
    }
    return counts;
  }

  Future<void> _load() async {
    final feedBrands = _brandsFromFeed();
    if (feedBrands.isNotEmpty && mounted) {
      setState(() {
        _brands = feedBrands;
        _loading = false;
        _error = null;
      });
    }

    try {
      final rows = await widget.controller.api
          .productsBrands(limit: 100)
          .timeout(const Duration(seconds: 20));
      final counts = <String, int>{};
      for (final r in rows) {
        final b = (r['brand'] ?? '').toString().trim();
        if (b.isEmpty) continue;
        counts[b] = (r['count'] as num?)?.toInt() ?? 1;
      }
      if (!mounted) return;
      setState(() {
        if (counts.isNotEmpty) _brands = counts;
        _loading = false;
        _error = _brands.isEmpty ? 'Бренды не найдены в каталоге' : null;
      });
    } catch (e) {
      if (!mounted) return;
      final fallback = feedBrands.isNotEmpty ? feedBrands : _brandsFromFeed();
      setState(() {
        _brands = fallback;
        _loading = false;
        _error = fallback.isEmpty ? ApiClient.formatError(e) : null;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        backgroundColor: AppColors.bg,
        elevation: 0,
        leading: IconButton(
          icon: const Icon(Icons.arrow_back),
          onPressed: () => Navigator.pop(context),
        ),
        title: const Text('Бренды', style: AppTextStyles.sectionTitle),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator(color: AppColors.accent))
          : _brands.isEmpty
              ? Center(
                  child: Padding(
                    padding: const EdgeInsets.all(24),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(
                          _error ?? 'Список брендов пуст',
                          textAlign: TextAlign.center,
                          style: AppTextStyles.bodyMuted,
                        ),
                        const SizedBox(height: 16),
                        TextButton(onPressed: _load, child: const Text('Повторить')),
                      ],
                    ),
                  ),
                )
              : RefreshIndicator(
                  color: AppColors.accent,
                  onRefresh: _load,
                  child: ListView.separated(
                    padding: const EdgeInsets.all(20),
                    itemCount: _brands.length,
                    separatorBuilder: (context, index) =>
                        const Divider(color: AppColors.line),
                    itemBuilder: (context, i) {
                      final e = _brands.entries.elementAt(i);
                      return ListTile(
                        contentPadding: EdgeInsets.zero,
                        title: Text(
                          e.key,
                          style: const TextStyle(
                            fontSize: 18,
                            fontWeight: FontWeight.w500,
                          ),
                        ),
                        trailing: Text(
                          '${e.value} товаров',
                          style: AppTextStyles.bodyMuted,
                        ),
                        onTap: () => Navigator.pop(context, e.key),
                      );
                    },
                  ),
                ),
    );
  }
}
