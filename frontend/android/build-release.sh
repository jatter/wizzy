#!/bin/bash
set -euo pipefail
ANDROID_PROJECT="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$ANDROID_PROJECT/../.." && pwd)"
if [[ -d /opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home ]]; then
  export JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
fi
if [[ -z "${ANDROID_HOME:-}" && -d /opt/homebrew/share/android-commandlinetools ]]; then
  export ANDROID_HOME=/opt/homebrew/share/android-commandlinetools
fi
: "${ANDROID_HOME:?Set ANDROID_HOME to an Android SDK with platform 35 and build-tools 35.0.0}"
export PATH="${JAVA_HOME:+$JAVA_HOME/bin:}$PATH"
python3 - "$PROJECT_ROOT" <<'PY'
import os
import secrets
import subprocess
import sys
from pathlib import Path

directory = Path(sys.argv[1]) / '.local/android-signing'
directory.mkdir(parents=True, exist_ok=True, mode=0o700)
properties = directory / 'release.properties'
keystore = directory / 'wizzy-release.p12'
if not properties.exists():
    if keystore.exists():
        raise SystemExit('Signing keystore exists but its properties are missing; restore the properties instead of replacing the key.')
    password = secrets.token_hex(32)
    subprocess.run(['keytool', '-genkeypair', '-keystore', str(keystore), '-storetype', 'PKCS12',
                    '-alias', 'wizzy', '-keyalg', 'RSA', '-keysize', '3072', '-validity', '10000',
                    '-dname', 'CN=Wizzy, OU=Family Learning, O=RefineView',
                    '-storepass:env', 'WIZZY_SIGNING_PASSWORD', '-keypass:env', 'WIZZY_SIGNING_PASSWORD'],
                   env=os.environ | {'WIZZY_SIGNING_PASSWORD': password}, check=True, capture_output=True)
    properties.write_text(f'storeFile={keystore.name}\nkeyAlias=wizzy\nstorePassword={password}\nkeyPassword={password}\n')
    properties.chmod(0o600)
    keystore.chmod(0o600)
elif not keystore.is_file():
    raise SystemExit('Restore the original signing keystore; a new key would prevent existing users from upgrading.')
PY
"$ANDROID_PROJECT/gradlew" -p "$ANDROID_PROJECT" :app:assembleRelease :app:lintRelease --console=plain
"$ANDROID_HOME/build-tools/35.0.0/apksigner" verify --verbose --print-certs "$ANDROID_PROJECT/app/build/outputs/apk/release/app-release.apk"
python3 - "$PROJECT_ROOT" "$ANDROID_HOME" <<'PY'
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

root, sdk = map(Path, sys.argv[1:])
apk = root / 'frontend/android/app/build/outputs/apk/release/app-release.apk'
metadata = json.loads((apk.parent / 'output-metadata.json').read_text())
release = metadata['elements'][0]
output = root / '.local/android-release'
(output / 'downloads/android').mkdir(parents=True, exist_ok=True)
(output / '.well-known').mkdir(parents=True, exist_ok=True)
filename = f"wizzy-{release['versionName']}.apk"
shutil.copyfile(apk, output / 'downloads/android' / filename)
value = {'version': release['versionName'], 'version_code': release['versionCode'], 'package': metadata['applicationId'],
         'size': apk.stat().st_size, 'sha256': hashlib.sha256(apk.read_bytes()).hexdigest(),
         'url': '/downloads/android/' + filename, 'minimum_android': '8.0'}
(output / 'downloads/android/latest.json').write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
certificate = subprocess.run([str(sdk / 'build-tools/35.0.0/apksigner'), 'verify', '--print-certs', str(apk)],
                             check=True, capture_output=True, text=True).stdout
digest = re.search(r'Signer #1 certificate SHA-256 digest: ([0-9a-fA-F]{64})', certificate).group(1).upper()
fingerprint = ':'.join(digest[i:i + 2] for i in range(0, len(digest), 2))
links = [{'relation': ['delegate_permission/common.get_login_creds'], 'target': {
    'namespace': 'android_app', 'package_name': metadata['applicationId'], 'sha256_cert_fingerprints': [fingerprint]}}]
(output / '.well-known/assetlinks.json').write_text(json.dumps(links, indent=2) + '\n')
print(f"Signed Android release: {filename}, {value['size']} bytes, SHA-256 {value['sha256']}")
PY
