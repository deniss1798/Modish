"""Exercise the real Gradle signing gate using only synthetic negative inputs.

No APK is built, private configuration is read, or raw Gradle output is printed.
Run explicitly after Flutter dependencies are available in the local SDK cache.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic


WORKSPACE = Path(__file__).resolve().parents[1]
FIELDS = (
    'MODISH_SIGNING_STORE_FILE',
    'MODISH_SIGNING_STORE_PASSWORD',
    'MODISH_SIGNING_KEY_ALIAS',
    'MODISH_SIGNING_KEY_PASSWORD',
)
REQUIRED = 'MODISH_RELEASE_SIGNING_REQUIRED'
INVALID = 'MODISH_RELEASE_SIGNING_INVALID'


def run_case(name, environment, *, release, expected_marker=None):
    task = ':app:assembleRelease' if release else ':app:assembleDebug'
    wrapper = WORKSPACE / 'android' / ('gradlew.bat' if os.name == 'nt' else 'gradlew')
    command = [str(wrapper), task, '--dry-run', '--offline', '--no-daemon',
        '--no-configuration-cache', '--console=plain']
    started = monotonic()
    try:
        result = subprocess.run(command, cwd=WORKSPACE / 'android', env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180,
            encoding='utf-8', errors='replace')
    except (OSError, subprocess.TimeoutExpired):
        return {'case': name, 'passed': False, 'reason': 'Gradle unavailable or timed out'}
    output = result.stdout
    private_values = [environment[field] for field in FIELDS if environment.get(field)]
    redacted = all(value not in output for value in private_values)
    if release:
        expected = result.returncode != 0 and expected_marker in output
    else:
        expected = result.returncode == 0 and REQUIRED not in output and INVALID not in output
    return {'case': name, 'passed': expected and redacted,
        'expected_result': expected, 'synthetic_values_redacted': redacted,
        'seconds': round(monotonic() - started, 3)}


def main():
    if (WORKSPACE / 'android' / 'key.properties').exists():
        # Do not open, move, replace, or temporarily hide a private signing file.
        print('Signing checks require a checkout without private key.properties.')
        return 2
    environment = dict(os.environ)
    for field in FIELDS:
        environment.pop(field, None)
    absent_store = WORKSPACE / '.tmp' / 'synthetic-missing-signing-store.keystore'
    if absent_store.exists():
        print('Signing checks require the synthetic missing store to remain absent.')
        return 2
    partial = dict(environment, MODISH_SIGNING_STORE_PASSWORD='SYNTHETIC_SIGNING_SECRET_NOT_FOR_RELEASE')
    missing_store = dict(environment,
        MODISH_SIGNING_STORE_FILE=str(absent_store),
        MODISH_SIGNING_STORE_PASSWORD='SYNTHETIC_SIGNING_STORE_PASSWORD',
        MODISH_SIGNING_KEY_ALIAS='SYNTHETIC_RELEASE_ALIAS',
        MODISH_SIGNING_KEY_PASSWORD='SYNTHETIC_SIGNING_KEY_PASSWORD')
    cases = (
        ('release_missing_configuration', environment, True, REQUIRED),
        ('release_partial_environment', partial, True, INVALID),
        ('release_missing_store', missing_store, True, INVALID),
        ('debug_with_partial_release_environment', partial, False, None),
    )
    results = []
    for name, case_environment, release, marker in cases:
        result = run_case(name, case_environment, release=release, expected_marker=marker)
        results.append(result)
        print(json.dumps(result))
        if not result['passed']:
            # Preserve the phase/result, never echo configuration or build logs.
            return 1
    print(json.dumps({'passed': len(results), 'apk_built': False, 'keystore_created': False}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
