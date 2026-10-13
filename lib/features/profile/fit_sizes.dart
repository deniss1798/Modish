/// Numeric input follows the explicit RU (clothing) / EU (shoes) form labels.
/// Other systems must be written as a prefix; no cross-system conversion occurs.
Map<String, String>? sizeChoice(String text, {bool footwear = false}) {
  var value = text.trim().toUpperCase().replaceAll(' ', '');
  if (value.isEmpty) return null;
  String? system;
  final prefix = RegExp(r'^(RU|EU|US|UK)[:\-]?(.*)$').firstMatch(value);
  if (prefix != null) {
    system = prefix[1];
    value = prefix[2]!;
  }
  if (RegExp(r'^\d{1,2}(?:\.5)?$').hasMatch(value)) {
    system ??= footwear ? 'EU' : 'RU';
  } else if (!footwear &&
      RegExp(r'^(XXXS|XXS|XS|S|M|L|XL|XXL|XXXL|[2-6]XL)$').hasMatch(value)) {
    if (system != null) return null;
    system = 'LETTER';
  } else if (!footwear && RegExp(r'^W\d{2}(?:L\d{2})?$').hasMatch(value)) {
    if (system != null) return null;
    system = 'WAIST';
  } else {
    return null;
  }
  return {'value': value, 'system': system};
}

String sizeFieldValue(Map<String, dynamic> fit, String slot) {
  final profile = fit['size_profile'] as Map?;
  final sizes = profile?['sizes'] as Map?;
  final choice = sizes?[slot] as Map?;
  if (choice?['state'] != 'confirmed') return '';
  final value = choice?['value']?.toString() ?? '';
  if (value.isEmpty) return '';
  final system = choice?['system']?.toString();
  if (system == null || system == 'LETTER' || system == 'WAIST') return value;
  return '$system $value';
}
