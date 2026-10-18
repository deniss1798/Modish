import 'dart:async';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/presentation/price_format.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/modish_widgets.dart';
import '../../core/widgets/product_image.dart';
import '../products/models.dart';
import '../products/product_detail_screen.dart';
import 'catalogue_widgets.dart';

class DiscoveryScreen extends StatefulWidget {
  const DiscoveryScreen({
    super.key,
    required this.controller,
    this.initialCategory = '',
    this.initialQuery = '',
  });
  final AppController controller;
  final String initialCategory;
  final String initialQuery;

  @override
  State<DiscoveryScreen> createState() => _DiscoveryScreenState();
}

class _DiscoveryScreenState extends State<DiscoveryScreen> {
  late final TextEditingController _query;
  late String _category;
  late int _sessionEpoch;
  Timer? _debounce;
  int _request = 0;
  List<Product> _products = [];
  String? _cursor;
  bool _hasMore = false;
  bool _loading = false;
  bool _retryMore = false;
  bool _consented = false;
  bool _sessionChanged = false;
  bool _cursorExpired = false;
  String? _error;
  static const _categories = {
    '': 'Все',
    'shoes': 'Обувь',
    'outerwear': 'Верхняя одежда',
  };

  @override
  void initState() {
    super.initState();
    _query = TextEditingController(text: widget.initialQuery.trim());
    _category = _categories.containsKey(widget.initialCategory)
        ? widget.initialCategory
        : '';
    _sessionEpoch = widget.controller.navigationSessionEpoch;
    widget.controller.addListener(_onControllerChanged);
  }

  @override
  void didUpdateWidget(covariant DiscoveryScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.controller, widget.controller)) {
      oldWidget.controller.removeListener(_onControllerChanged);
      widget.controller.addListener(_onControllerChanged);
      _invalidateConsent();
    }
  }

  @override
  void dispose() {
    _request++;
    _debounce?.cancel();
    widget.controller.removeListener(_onControllerChanged);
    _query.dispose();
    super.dispose();
  }

  void _onControllerChanged() {
    if (!mounted || _sessionEpoch == widget.controller.navigationSessionEpoch) {
      return;
    }
    _invalidateConsent();
  }

  bool get _hasCurrentConsent =>
      _consented &&
      !_sessionChanged &&
      _sessionEpoch == widget.controller.navigationSessionEpoch;

  void _invalidateConsent() {
    _request++;
    _debounce?.cancel();
    setState(() {
      _sessionEpoch = widget.controller.navigationSessionEpoch;
      _consented = false;
      _sessionChanged = true;
      _products = [];
      _cursor = null;
      _hasMore = false;
      _loading = false;
      _retryMore = false;
      _cursorExpired = false;
      _error = 'Вы сменили аккаунт. Откройте каталог заново.';
    });
  }

  void _acceptConsent() {
    if (!mounted || _sessionChanged || _consented) return;
    if (_sessionEpoch != widget.controller.navigationSessionEpoch) {
      _invalidateConsent();
      return;
    }
    setState(() => _consented = true);
    _load();
  }

  void _reset() {
    if (!mounted || !_hasCurrentConsent) return;
    _request++;
    setState(() {
      _products = [];
      _cursor = null;
      _hasMore = false;
      _error = null;
      _loading = true;
    });
  }

  void _queryChanged(String value) {
    _debounce?.cancel();
    if (!mounted || !_hasCurrentConsent) return;
    _reset();
    _debounce = Timer(const Duration(milliseconds: 450), _load);
  }

  void _categoryChanged(String category) {
    if (!mounted || !_hasCurrentConsent || _category == category) return;
    _debounce?.cancel();
    _category = category;
    _load();
  }

  Future<void> _load({bool more = false}) async {
    // Consent is local to this route and account. Guard every fetch path,
    // including refresh, debounce and pagination, rather than just initState.
    if (!mounted || !_hasCurrentConsent) return;
    if (more && (_loading || !_hasMore || _cursor == null)) return;
    _debounce?.cancel();
    final request = ++_request;
    final session = widget.controller.navigationSessionEpoch;
    final cursor = more ? _cursor : null;
    setState(() {
      _loading = true;
      _error = null;
      _retryMore = more;
      _cursorExpired = false;
      if (!more) {
        _products = [];
        _cursor = null;
        _hasMore = false;
      }
    });
    try {
      if (!widget.controller.supportsCatalogueDiscovery) {
        throw StateError('Catalogue discovery unavailable');
      }
      final data = await widget.controller.api.catalogueDiscovery(
        category: _category,
        query: _query.text.trim(),
        cursor: cursor,
      );
      if (!mounted ||
          !_hasCurrentConsent ||
          request != _request ||
          session != widget.controller.navigationSessionEpoch) {
        return;
      }
      if (data['mode'] != 'catalogue_discovery' ||
          data['personalized'] != false ||
          data['size_verification'] != 'not_verified' ||
          data['products'] is! List) {
        throw const FormatException('Invalid catalogue response');
      }
      final rows = (data['products'] as List).map((row) {
        if (row is! Map) {
          throw const FormatException('Invalid catalogue product');
        }
        final product = Product.fromApi(Map<String, dynamic>.from(row));
        if (!product.catalogueDiscovery ||
            product.sizeVerification != 'not_verified') {
          throw const FormatException('Missing catalogue product metadata');
        }
        return product;
      }).toList();
      final hasMore = data['has_more'] == true;
      final nextCursor = data['next_cursor'] as String?;
      if (hasMore &&
          (nextCursor == null || nextCursor.isEmpty || nextCursor == cursor)) {
        throw const FormatException('Invalid catalogue cursor');
      }
      final merged = [..._products];
      final identities = merged
          .expand((product) => product.identityKeys)
          .toSet();
      for (final product in rows) {
        if (identities.intersection(product.identityKeys).isEmpty) {
          merged.add(product);
          identities.addAll(product.identityKeys);
        }
      }
      setState(() {
        _products = merged;
        _cursor = hasMore ? nextCursor : null;
        _hasMore = hasMore;
        _loading = false;
      });
    } catch (error) {
      if (!mounted ||
          !_hasCurrentConsent ||
          request != _request ||
          session != widget.controller.navigationSessionEpoch) {
        return;
      }
      setState(() {
        _loading = false;
        _cursorExpired =
            cursor != null &&
            error is DioException &&
            error.response?.statusCode == 422;
        if (_cursorExpired) {
          _cursor = null;
          _hasMore = false;
          _retryMore = false;
        }
        _error = _cursorExpired
            ? 'Каталог изменился. Обновите список товаров.'
            : widget.controller.supportsCatalogueDiscovery
            ? 'Не удалось загрузить каталог. Проверьте интернет и попробуйте снова.'
            : 'Каталог пока недоступен. Попробуйте открыть его позже.';
      });
    }
  }

  void _openProduct(Product product) {
    if (!mounted || !_hasCurrentConsent) return;
    FocusScope.of(context).unfocus();
    // Discovery deliberately does not emit recommendation/Taste events.
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => ProductDetailScreen(
          controller: widget.controller,
          card: FeedCard(product: product, reason: ''),
          eventMeta: const {'surface': 'catalogue_discovery'},
        ),
      ),
    );
  }

  Widget _consentNotice() => ListView(
    padding: const EdgeInsets.fromLTRB(16, 20, 16, 24),
    children: [
      SoftCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Icon(
              Icons.straighten_outlined,
              color: AppColors.accent,
              size: 32,
            ),
            const SizedBox(height: 16),
            const Text('Размер не проверен', style: AppTextStyles.sectionTitle),
            const SizedBox(height: 12),
            const Text(
              'В этом каталоге можно посмотреть товары магазинов. Мы не проверяли, подойдёт ли вам их размер.',
              style: AppTextStyles.bodyMuted,
            ),
            const SizedBox(height: 12),
            const Text(
              'Перед покупкой сверьте размер с таблицей на сайте магазина. Персональная лента и образы остаются отдельными подборками с проверкой размера.',
              style: AppTextStyles.bodyMuted,
            ),
            const SizedBox(height: 24),
            SizedBox(
              width: double.infinity,
              child: FilledButton(
                key: const ValueKey('catalogue-consent-accept'),
                onPressed: _acceptConsent,
                style: FilledButton.styleFrom(
                  backgroundColor: AppColors.accent,
                  foregroundColor: AppColors.onAccent,
                  minimumSize: const Size(0, 54),
                  padding: const EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 14,
                  ),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(16),
                  ),
                ),
                child: const Text(
                  'Понимаю, показать каталог',
                  textAlign: TextAlign.center,
                  style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700),
                ),
              ),
            ),
            const SizedBox(height: 12),
            SecondaryButton(
              key: const ValueKey('catalogue-consent-cancel'),
              label: 'Вернуться',
              onPressed: () => Navigator.maybePop(context),
            ),
          ],
        ),
      ),
    ],
  );

  Widget _changedSessionNotice() => ListView(
    padding: const EdgeInsets.all(16),
    children: [SoftCard(child: Text(_error!, style: AppTextStyles.bodyMuted))],
  );

  @override
  Widget build(BuildContext context) => Scaffold(
    backgroundColor: AppColors.bg,
    appBar: AppBar(title: const Text('Каталог магазинов')),
    body: SafeArea(
      top: false,
      child: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 680),
          child: _sessionChanged
              ? _changedSessionNotice()
              : !_consented
              ? _consentNotice()
              : RefreshIndicator(
                  onRefresh: _load,
                  child: CustomScrollView(
                    keyboardDismissBehavior:
                        ScrollViewKeyboardDismissBehavior.onDrag,
                    physics: const AlwaysScrollableScrollPhysics(),
                    slivers: [
                      SliverPadding(
                        padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
                        sliver: SliverToBoxAdapter(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const CatalogueSizeNotice(),
                              const SizedBox(height: 16),
                              TextField(
                                key: const ValueKey('catalogue-query'),
                                controller: _query,
                                enabled: !_sessionChanged,
                                maxLength: 160,
                                textInputAction: TextInputAction.search,
                                decoration: InputDecoration(
                                  hintText: 'Название, категория или бренд',
                                  counterText: '',
                                  prefixIcon: const Icon(Icons.search),
                                  suffixIcon: _query.text.isEmpty
                                      ? null
                                      : IconButton(
                                          tooltip: 'Очистить поиск',
                                          icon: const Icon(Icons.close),
                                          onPressed: () {
                                            _query.clear();
                                            _queryChanged('');
                                          },
                                        ),
                                ),
                                onChanged: _queryChanged,
                                onSubmitted: (_) {
                                  FocusScope.of(context).unfocus();
                                  _load();
                                },
                              ),
                              const SizedBox(height: 12),
                              Wrap(
                                spacing: 8,
                                runSpacing: 8,
                                children: _categories.entries
                                    .map(
                                      (entry) => ChoiceChip(
                                        key: ValueKey(
                                          'catalogue-category-${entry.key}',
                                        ),
                                        label: Text(entry.value),
                                        selected: _category == entry.key,
                                        onSelected: _sessionChanged
                                            ? null
                                            : (_) =>
                                                  _categoryChanged(entry.key),
                                      ),
                                    )
                                    .toList(),
                              ),
                            ],
                          ),
                        ),
                      ),
                      SliverPadding(
                        padding: const EdgeInsets.symmetric(horizontal: 16),
                        sliver: SliverLayoutBuilder(
                          builder: (context, constraints) {
                            final width =
                                (constraints.crossAxisExtent - 12) / 2;
                            final scale = MediaQuery.textScalerOf(
                              context,
                            ).scale(1).clamp(1.0, double.infinity);
                            return SliverGrid.builder(
                              itemCount: _products.length,
                              gridDelegate:
                                  SliverGridDelegateWithFixedCrossAxisCount(
                                    crossAxisCount: 2,
                                    crossAxisSpacing: 12,
                                    mainAxisSpacing: 12,
                                    mainAxisExtent:
                                        (width - 24) * 1.25 + 24 + 180 * scale,
                                  ),
                              itemBuilder: (context, index) {
                                final product = _products[index];
                                return Semantics(
                                  button: true,
                                  label:
                                      'Открыть ${product.title}. Размер нужно уточнить у магазина.',
                                  child: SoftCard(
                                    padding: const EdgeInsets.all(12),
                                    child: InkWell(
                                      key: ValueKey(
                                        'catalogue-product-${product.id}',
                                      ),
                                      onTap: () => _openProduct(product),
                                      borderRadius: BorderRadius.circular(12),
                                      child: Column(
                                        crossAxisAlignment:
                                            CrossAxisAlignment.start,
                                        children: [
                                          AspectRatio(
                                            aspectRatio: 0.8,
                                            child: ProductFillImage(
                                              imageUrl: product.imageUrl,
                                              showRetry: false,
                                              borderRadius:
                                                  BorderRadius.circular(12),
                                            ),
                                          ),
                                          const SizedBox(height: 10),
                                          Text(
                                            product.brand,
                                            maxLines: 1,
                                            overflow: TextOverflow.ellipsis,
                                            style: AppTextStyles.caption,
                                          ),
                                          const SizedBox(height: 4),
                                          Text(
                                            product.title,
                                            maxLines: 2,
                                            overflow: TextOverflow.ellipsis,
                                            style: AppTextStyles.body.copyWith(
                                              fontWeight: FontWeight.w600,
                                            ),
                                          ),
                                          const SizedBox(height: 8),
                                          Text(
                                            '${formatPriceAmount(product.price)} ${product.currency == 'RUB' ? '₽' : product.currency}',
                                            style: AppTextStyles.price.copyWith(
                                              fontSize: 16,
                                            ),
                                          ),
                                          const SizedBox(height: 6),
                                          MerchantSizeSummary(product: product),
                                        ],
                                      ),
                                    ),
                                  ),
                                );
                              },
                            );
                          },
                        ),
                      ),
                      SliverPadding(
                        padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
                        sliver: SliverToBoxAdapter(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              if (_loading)
                                const Padding(
                                  padding: EdgeInsets.all(24),
                                  child: Center(
                                    child: CircularProgressIndicator(),
                                  ),
                                ),
                              if (_error != null)
                                SoftCard(
                                  child: Column(
                                    crossAxisAlignment:
                                        CrossAxisAlignment.start,
                                    children: [
                                      Text(
                                        _error!,
                                        style: AppTextStyles.bodyMuted,
                                      ),
                                      if (!_sessionChanged) ...[
                                        const SizedBox(height: 12),
                                        SecondaryButton(
                                          label: _cursorExpired
                                              ? 'Обновить каталог'
                                              : 'Повторить',
                                          onPressed: () =>
                                              _load(more: _retryMore),
                                        ),
                                      ],
                                    ],
                                  ),
                                ),
                              if (!_loading &&
                                  _error == null &&
                                  _products.isEmpty)
                                const SoftCard(
                                  child: Column(
                                    crossAxisAlignment:
                                        CrossAxisAlignment.start,
                                    children: [
                                      Text(
                                        'Пока ничего не найдено',
                                        style: AppTextStyles.sectionTitle,
                                      ),
                                      SizedBox(height: 8),
                                      Text(
                                        'Попробуйте другую категорию или более общий запрос.',
                                        style: AppTextStyles.bodyMuted,
                                      ),
                                    ],
                                  ),
                                ),
                              if (_hasMore && !_loading && _error == null)
                                SecondaryButton(
                                  key: const ValueKey('catalogue-more'),
                                  label: 'Показать ещё',
                                  onPressed: () => _load(more: true),
                                ),
                            ],
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
        ),
      ),
    ),
  );
}
