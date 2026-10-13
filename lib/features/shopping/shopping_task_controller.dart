import 'dart:math';
import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import '../../core/network/api_client.dart';

/// Screen/session state is intentionally separate from learned preferences.
class ShoppingTaskController extends ChangeNotifier {
  ShoppingTaskController(this.api);
  final ApiClient api;
  Map<String, dynamic>? result;
  bool busy = false;
  String? error;
  String? _requestId;
  String? _requestText;
  int _generation = 0;
  final _viewed = <String>{};
  final _positiveActions = <String>{};
  final _viewPending = <String>{};
  bool _disposed = false;

  void clear() {
    _generation++;
    _viewed.clear();
    _positiveActions.clear();
    _viewPending.clear();
    result = null;
    busy = false;
    error = null;
    _requestId = null;
    _requestText = null;
    notifyListeners();
  }

  /// Cached results are useful only while the server session is still active.
  bool discardExpired({DateTime? now}) {
    final expires = DateTime.tryParse('${result?['expires_at'] ?? ''}');
    if (expires == null || expires.isAfter(now ?? DateTime.now())) return false;
    clear();
    error = 'Этот запрос завершён. Создайте новый.';
    return true;
  }

  void _handleError(Object errorValue) {
    if (errorValue is DioException && errorValue.response?.statusCode == 410) {
      result = null;
      _requestId = null;
      _requestText = null;
      _viewed.clear();
      _positiveActions.clear();
      _viewPending.clear();
    }
    error = ApiClient.formatError(errorValue);
  }

  Future<void> submit(
    String text, {
    bool refine = false,
    String? referenceProductId,
  }) async {
    if (busy || text.trim().isEmpty) return;
    final expired = discardExpired();
    if (refine && expired) {
      notifyListeners();
      return;
    }
    final request = ++_generation;
    final previous = result;
    busy = true;
    error = null;
    notifyListeners();
    try {
      Map<String, dynamic> response;
      if (refine && previous != null) {
        try {
          response = await api.refineShoppingIntent(
            previous['id'] as String,
            text.trim(),
            previous['version'] as int,
            referenceProductId: referenceProductId,
            referenceOutfitId: (previous['outfits'] as List? ?? []).isEmpty
                ? null
                : previous['outfits'][0]['id'] as String,
          );
        } on DioException catch (e) {
          if (e.response?.statusCode != 409) rethrow;
          // A lost response or another device may have advanced the session.
          // Reload; never apply "cheaper" twice after an ambiguous timeout.
          response = await api.shoppingIntent(previous['id'] as String);
        }
      } else {
        if (_requestText != text.trim()) {
          _requestText = text.trim();
          final random = Random.secure();
          _requestId = List.generate(
            16,
            (_) => random.nextInt(256).toRadixString(16).padLeft(2, '0'),
          ).join();
        }
        response = await api.createShoppingIntent(text.trim(), _requestId!);
      }
      if (!_disposed && request == _generation) result = response;
    } catch (e) {
      if (!_disposed && request == _generation) {
        _handleError(e);
      }
    } finally {
      if (!_disposed && request == _generation) {
        busy = false;
        notifyListeners();
      }
    }
  }

  Future<void> recordVisibleProduct(
    String productId, {
    String? outfitId,
  }) async {
    final current = result;
    if (_disposed || busy || current == null) return;
    final key =
        '${current['id']}:${current['version']}:$productId:${outfitId ?? ''}';
    if (_viewed.contains(key) || !_viewPending.add(key)) return;
    final generation = _generation;
    try {
      await api.recordRecommendationEvent(
        eventType: 'view',
        productId: productId,
        meta: {
          'surface': 'shopping_intent',
          'intent_id': current['id'],
          'intent_version': current['version'],
          'intent_outfit_id': ?outfitId,
        },
      );
      if (!_disposed && generation == _generation) _viewed.add(key);
    } catch (_) {
      // Scrolling back or reopening the screen retries an unsuccessful view.
    } finally {
      _viewPending.remove(key);
    }
  }

  Map<String, dynamic> get eventMeta => result == null
      ? {}
      : {
          'surface': 'shopping_intent',
          'intent_id': result!['id'],
          'intent_version': result!['version'],
        };

  Map<String, dynamic> eventMetaFor({String? outfitId}) => {
    ...eventMeta,
    if (outfitId != null && result != null) 'intent_outfit_id': outfitId,
  };

  bool hasReacted(String productId, String event) =>
      _positiveActions.contains('$productId:$event');

  Future<void> react(String productId, String event, {String? outfitId}) async {
    if (!{'like', 'save'}.contains(event) || hasReacted(productId, event)) {
      return;
    }
    final current = result;
    if (current == null) return;
    final meta = eventMetaFor(outfitId: outfitId);
    await _action(() async {
      final generation = _generation;
      await api.recordRecommendationEvent(
        eventType: event,
        productId: productId,
        meta: meta,
      );
      if (!_disposed && generation == _generation) {
        _positiveActions.add('$productId:$event');
      }
      return current;
    });
  }

  Future<void> recordOpenProduct(String productId, {String? outfitId}) async {
    final meta = eventMetaFor(outfitId: outfitId);
    if (meta.isEmpty) return;
    try {
      await api.recordRecommendationEvent(
        eventType: 'open_product',
        productId: productId,
        meta: meta,
      );
    } catch (_) {}
  }

  Future<bool> close(String outcome) async {
    if (busy ||
        result == null ||
        !{'completed', 'abandoned'}.contains(outcome)) {
      return false;
    }
    final current = result!;
    final generation = _generation;
    var acknowledged = false;
    await _action(() async {
      await api.closeShoppingIntent(
        current['id'] as String,
        current['version'] as int,
        outcome,
      );
      acknowledged = true;
      return current;
    });
    if (acknowledged && !_disposed && _generation == generation + 1) {
      clear();
      return true;
    }
    return false;
  }

  Future<void> feedback(String productId, [String? reason]) async {
    final current = result;
    if (current == null) return;
    await _action(
      () => api.giveIntentFeedback(
        current['id'] as String,
        productId,
        reason,
        current['version'] as int,
      ),
    );
  }

  Future<void> replaceItem(String outfitId, String slot) async {
    final current = result;
    if (current == null) return;
    await _action(
      () => api.replaceIntentOutfitItem(
        current['id'] as String,
        outfitId,
        slot,
        current['version'] as int,
      ),
    );
  }

  Future<void> saveOutfit(String outfitId) async {
    final current = result;
    if (current == null) return;
    await _action(() async {
      await api.saveIntentOutfit(
        current['id'] as String,
        outfitId,
        current['version'] as int,
      );
      return {
        ...current,
        'outfits': [
          for (final row in current['outfits'] as List? ?? [])
            if (row['id'] == outfitId)
              {...row as Map<String, dynamic>, 'is_saved': true}
            else
              row,
        ],
      };
    });
  }

  Future<void> _action(Future<Map<String, dynamic>> Function() action) async {
    if (busy || result == null) return;
    if (discardExpired()) {
      notifyListeners();
      return;
    }
    final request = ++_generation;
    final id = result!['id'] as String;
    busy = true;
    error = null;
    notifyListeners();
    try {
      Map<String, dynamic> response;
      try {
        response = await action();
      } on DioException catch (e) {
        if (e.response?.statusCode != 409) rethrow;
        response = await api.shoppingIntent(id);
      }
      if (!_disposed && request == _generation) result = response;
    } catch (e) {
      if (!_disposed && request == _generation) {
        _handleError(e);
      }
    } finally {
      if (!_disposed && request == _generation) {
        busy = false;
        notifyListeners();
      }
    }
  }

  @override
  void dispose() {
    _disposed = true;
    _generation++;
    super.dispose();
  }
}
