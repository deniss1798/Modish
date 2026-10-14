import 'package:url_launcher/url_launcher.dart';

import 'api_client.dart';

/// A resolved URL is an intent. Count a transition only after the OS accepts it.
/// This does not assert that the merchant page loaded or a purchase occurred.
Future<bool> openMerchantLink(
  ApiClient api,
  String productId, {
  required String surface,
  Map<String, dynamic> contextMeta = const {},
  Future<bool> Function(Uri uri)? openUrl,
  bool recordRecommendationEvent = true,
}) async {
  final session = api.sessionEpoch;
  final response = await api.affiliateClick(productId);
  final uri = Uri.tryParse(response['url']?.toString() ?? '');
  if (uri == null ||
      !{'http', 'https'}.contains(uri.scheme) ||
      uri.host.isEmpty) {
    return false;
  }
  final opened =
      await (openUrl?.call(uri) ??
          launchUrl(uri, mode: LaunchMode.externalApplication));
  if (!opened) return false;
  // The external launch can finish after logout or an account change. Its
  // success remains true, but it cannot be attributed to the next account.
  if (session != api.sessionEpoch) return true;
  if (!recordRecommendationEvent) return true;
  try {
    await api.recordRecommendationEvent(
      eventType: 'affiliate_click',
      productId: productId,
      meta: {
        ...contextMeta,
        'surface': surface,
        'confirmation': 'external_application_opened',
        if (response['click_id'] != null) 'click_id': response['click_id'],
      },
    );
  } catch (_) {
    // An analytics failure must not turn a successful launch into an error.
  }
  return true;
}
