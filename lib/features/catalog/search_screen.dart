import 'dart:async';
import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';
import '../../core/widgets/product_image.dart';
import '../products/models.dart' as prod;
import '../products/product_detail_screen.dart';

class SearchScreen extends StatefulWidget {
  const SearchScreen({
    super.key,
    required this.controller,
    this.initialQuery = '',
  });
  final AppController controller;
  final String initialQuery;
  @override
  State<SearchScreen> createState() => _SearchScreenState();
}

class _SearchScreenState extends State<SearchScreen> {
  final _query = TextEditingController();
  List<prod.Product> _results = [];
  Timer? _debounce;
  int _request = 0;
  bool _loading = false;
  bool _hasMore = false;
  String _submitted = '';
  String? _error;
  static const _pageSize = 40;
  static const _examples = ['Куртки', 'Джинсы', 'Платья', 'Обувь'];

  @override
  void initState() {
    super.initState();
    final initial = widget.initialQuery.trim();
    if (initial.isEmpty) return;
    _query.text = initial;
    _loading = true;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _search();
    });
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _query.dispose();
    super.dispose();
  }

  void _changed(String text) {
    _debounce?.cancel();
    _request++;
    setState(() {
      _results = [];
      _submitted = '';
      _hasMore = false;
      _error = null;
      _loading = text.trim().isNotEmpty;
    });
    if (text.trim().isNotEmpty) {
      _debounce = Timer(const Duration(milliseconds: 450), () => _search());
    }
  }

  Future<void> _search({bool more = false}) async {
    _debounce?.cancel();
    final text = _query.text.trim();
    if (text.isEmpty) {
      _changed('');
      return;
    }
    final request = ++_request;
    final offset = more ? _results.length : 0;
    setState(() {
      _loading = true;
      _error = null;
      _submitted = text;
      if (!more) _results = [];
    });
    try {
      final rows = await widget.controller.api.productsList(
        limit: _pageSize,
        offset: offset,
        query: text,
      );
      if (!mounted || request != _request) return;
      setState(() {
        _results = [..._results, ...rows.map(prod.Product.fromApi)];
        _hasMore = rows.length == _pageSize;
        _loading = false;
      });
    } catch (_) {
      if (!mounted || request != _request) return;
      setState(() {
        _loading = false;
        _error =
            'Не удалось выполнить поиск. Проверьте подключение и попробуйте снова.';
      });
    }
  }

  void _submit([String? text]) {
    if (text != null) _query.text = text;
    FocusScope.of(context).unfocus();
    _search();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        toolbarHeight: 72,
        leading: IconButton(
          tooltip: 'Назад',
          icon: const Icon(Icons.arrow_back),
          onPressed: () => Navigator.maybePop(context),
        ),
        title: TextField(
          controller: _query,
          autofocus: widget.initialQuery.trim().isEmpty,
          maxLength: 160,
          textInputAction: TextInputAction.search,
          decoration: InputDecoration(
            hintText: 'Поиск по каталогу',
            counterText: '',
            suffixIcon: _query.text.isEmpty
                ? null
                : IconButton(
                    tooltip: 'Очистить поиск',
                    icon: const Icon(Icons.close, size: 20),
                    onPressed: () {
                      _query.clear();
                      _changed('');
                    },
                  ),
          ),
          onChanged: _changed,
          onSubmitted: (_) => _submit(),
        ),
        actions: [
          IconButton(
            tooltip: 'Найти',
            icon: const Icon(Icons.search),
            onPressed: () => _submit(),
          ),
        ],
      ),
      body: SafeArea(
        top: false,
        child: ListView(
          keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
          padding: const EdgeInsets.all(20),
          children: [
            if (_submitted.isEmpty && !_loading) ...[
              const Text(
                'Найдите нужную вещь',
                style: AppTextStyles.sectionTitle,
              ),
              const SizedBox(height: 8),
              const Text(
                'Введите название, категорию или бренд — например, «чёрные брюки» или «BAON».',
                style: AppTextStyles.bodyMuted,
              ),
              const SizedBox(height: 20),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: _examples
                    .map(
                      (text) => ActionChip(
                        label: Text(text),
                        onPressed: () => _submit(text),
                      ),
                    )
                    .toList(),
              ),
            ],
            if (_submitted.isNotEmpty && _results.isNotEmpty) ...[
              Text(
                'Результаты: «$_submitted»',
                style: AppTextStyles.sectionTitle,
              ),
              const SizedBox(height: 6),
              const Text(
                'Каталог с учётом пола в вашем профиле.',
                style: AppTextStyles.caption,
              ),
              const SizedBox(height: 16),
            ],
            if (!_loading &&
                _error == null &&
                _submitted.isNotEmpty &&
                _results.isEmpty)
              SoftCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'Ничего не найдено',
                      style: AppTextStyles.sectionTitle,
                    ),
                    const SizedBox(height: 8),
                    Text(
                      'По запросу «$_submitted» нет доступных товаров. Попробуйте более общее название или другой бренд.',
                      style: AppTextStyles.bodyMuted,
                    ),
                  ],
                ),
              ),
            ..._results.map(
              (p) => Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: SoftCard(
                  padding: const EdgeInsets.all(12),
                  child: InkWell(
                    onTap: () => Navigator.push(
                      context,
                      MaterialPageRoute(
                        builder: (_) => ProductDetailScreen(
                          controller: widget.controller,
                          card: prod.FeedCard(product: p, reason: ''),
                        ),
                      ),
                    ),
                    child: Row(
                      children: [
                        SizedBox(
                          width: 72,
                          height: 96,
                          child: ProductFillImage(
                            imageUrl: p.imageUrl,
                            borderRadius: BorderRadius.circular(12),
                          ),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(p.brand, style: AppTextStyles.caption),
                              Text(
                                p.title,
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                                style: AppTextStyles.body.copyWith(
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                              const SizedBox(height: 6),
                              Text(
                                '${p.price} ₽',
                                style: AppTextStyles.price.copyWith(
                                  fontSize: 16,
                                ),
                              ),
                            ],
                          ),
                        ),
                        const Icon(Icons.chevron_right, color: AppColors.muted),
                      ],
                    ),
                  ),
                ),
              ),
            ),
            if (_loading)
              const Center(
                child: Padding(
                  padding: EdgeInsets.all(24),
                  child: CircularProgressIndicator(),
                ),
              ),
            if (_error != null) ...[
              Text(_error!, style: AppTextStyles.bodyMuted),
              TextButton(
                onPressed: () => _search(more: _results.isNotEmpty),
                child: const Text('Повторить поиск'),
              ),
            ],
            if (_hasMore && !_loading && _error == null)
              SecondaryButton(
                label: 'Показать ещё',
                onPressed: () => _search(more: true),
              ),
          ],
        ),
      ),
    );
  }
}
