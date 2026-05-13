import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';

class BrandsScreen extends StatefulWidget {
  const BrandsScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<BrandsScreen> createState() => _BrandsScreenState();
}

class _BrandsScreenState extends State<BrandsScreen> {
  Map<String, int> _brands = {};
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final rows = await widget.controller.api.productsList(limit: 200);
      final counts = <String, int>{};
      for (final r in rows) {
        final b = (r['brand'] ?? '').toString().trim();
        if (b.isEmpty) continue;
        counts[b] = (counts[b] ?? 0) + 1;
      }
      if (!mounted) return;
      setState(() {
        _brands = Map.fromEntries(
          counts.entries.toList()..sort((a, b) => b.value.compareTo(a.value)),
        );
        _loading = false;
      });
    } catch (_) {
      final counts = <String, int>{};
      for (final c in widget.controller.productFeed) {
        final b = c.product.brand.trim();
        if (b.isEmpty) continue;
        counts[b] = (counts[b] ?? 0) + 1;
      }
      if (!mounted) return;
      setState(() {
        _brands = counts;
        _loading = false;
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
          ? const Center(child: CircularProgressIndicator())
          : ListView.separated(
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
    );
  }
}
