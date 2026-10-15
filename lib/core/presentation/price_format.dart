/// Formats a price already expressed in rubles; API values are never rescaled.
/// Whole-ruble labels stay unchanged, while kopecks always use two digits.
String formatPriceAmount(num amount) {
  final fixed = amount.toStringAsFixed(2);
  return fixed.endsWith('.00')
      ? fixed.substring(0, fixed.length - 3)
      : fixed.replaceFirst('.', ',');
}
