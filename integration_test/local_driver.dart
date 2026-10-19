// Host-side SDK driver for the isolated local journey. No app credentials,
// response bodies, or screenshot bytes are printed or written to metadata.
import 'dart:convert';
import 'dart:io';

import 'package:integration_test/integration_test_driver.dart';

const _captureNames = {
  'wow',
  'feed',
  'catalogue-consent',
  'catalogue-unknown-size',
};
const _pngSignature = [137, 80, 78, 71, 13, 10, 26, 10];

Future<void> _saveArtifacts(Map<String, dynamic>? data) async {
  if (data == null ||
      data['scope'] != 'isolated_local_fixture_real_flutter_http' ||
      data['result'] != 'passed') {
    throw StateError('The driver received no successful local-fixture report.');
  }
  final runId = data['fixture_run_id'];
  if (runId is! String || !RegExp(r'^[0-9a-f]{32}$').hasMatch(runId)) {
    throw StateError('The local fixture run ID is missing or invalid.');
  }
  final captures = data['screenshots'];
  if (captures is! List || captures.length != _captureNames.length) {
    throw StateError('The local journey must provide all four screenshots.');
  }
  final pngs = <String, List<int>>{};
  for (final capture in captures) {
    if (capture is! Map) throw StateError('Invalid screenshot report.');
    final name = capture['screenshotName'];
    final rawBytes = capture['bytes'];
    if (name is! String ||
        !_captureNames.contains(name) ||
        pngs.containsKey(name) ||
        rawBytes is! List ||
        rawBytes.length < 24 ||
        rawBytes.any((byte) => byte is! int || byte < 0 || byte > 255)) {
      throw StateError('Unexpected, duplicated, or empty screenshot.');
    }
    final bytes = rawBytes.cast<int>();
    for (var index = 0; index < _pngSignature.length; index++) {
      if (bytes[index] != _pngSignature[index]) {
        throw StateError('A local screenshot is not a PNG.');
      }
    }
    pngs[name] = bytes;
  }

  final workspace = await Directory.current.resolveSymbolicLinks();
  final sep = Platform.pathSeparator;
  if (!File('$workspace${sep}pubspec.yaml').existsSync() ||
      !File(
        '$workspace${sep}integration_test${sep}local_driver.dart',
      ).existsSync()) {
    throw StateError('Run the local driver from the Modish workspace.');
  }
  final temporary = Directory('$workspace$sep.tmp');
  await temporary.create();
  if (await temporary.resolveSymbolicLinks() != '$workspace$sep.tmp') {
    throw StateError('Local artifacts must remain inside the workspace.');
  }
  final artifacts = Directory('${temporary.path}${sep}device-alpha-artifacts');
  await artifacts.create();
  if (await artifacts.resolveSymbolicLinks() != artifacts.path) {
    throw StateError('Local artifacts must remain inside the workspace.');
  }
  final timestamp = DateTime.now().toUtc().toIso8601String().replaceAll(
    RegExp(r'[^0-9TZ]'),
    '',
  );
  final output = Directory('${artifacts.path}$sep$timestamp-$runId');
  if (output.existsSync()) {
    throw StateError('Artifact directory already exists.');
  }
  await output.create();
  for (final capture in pngs.entries) {
    await File(
      '${output.path}$sep${capture.key}.png',
    ).writeAsBytes(capture.value, flush: true);
  }
  final metadata = Map<String, dynamic>.from(data)
    ..remove('screenshots')
    ..remove('screenshots_on_device')
    ..['screenshots'] = [
      for (final capture in pngs.entries)
        {'file': '${capture.key}.png', 'bytes': capture.value.length},
    ];
  await File('${output.path}${sep}journey.json').writeAsString(
    const JsonEncoder.withIndent('  ').convert(metadata),
    flush: true,
  );
  stdout.writeln('Local journey artifacts saved: ${output.path}');
}

Future<void> main() => integrationDriver(
  timeout: const Duration(minutes: 6),
  responseDataCallback: _saveArtifacts,
);
