"""Opt-in Android WebView -> HTTPS -> restricted API -> isolated PostgreSQL."""
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree

import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from PIL import Image
from sqlalchemy import text
from starlette.staticfiles import StaticFiles

pytestmark = pytest.mark.skipif(os.environ.get('WIZZY_ANDROID_TESTS') != '1',
                              reason='opt in with WIZZY_ANDROID_TESTS=1 and a disposable emulator')
ROOT = Path(__file__).resolve().parents[2]


def test_native_pair_expiry_restore_logout_and_remote_revoke(app, client, owner, student, admin_engine, tmp_path):
    sdk = Path(os.environ['ANDROID_HOME'])
    serial = os.environ['WIZZY_ANDROID_SERIAL']
    adb = [str(sdk / 'platform-tools/adb'), '-s', serial]
    def device(*args):
        return subprocess.run([*adb, *args], check=True, capture_output=True, text=True, timeout=60).stdout
    assert device('shell', 'getprop', 'ro.kernel.qemu').strip() == '1', 'Use a disposable emulator only'
    assert (ROOT / 'frontend/family/dist/build/h5/index.html').is_file()

    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    origin = f'https://localhost:{port}'
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Wizzy isolated Android test')])
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(UTC) - timedelta(minutes=5))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))
    certfile, keyfile = tmp_path / 'test.crt', tmp_path / 'test.key'
    certfile.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    keyfile.chmod(0o600)

    # Change trust only in this disposable copy; the released app never trusts this CA.
    android = tmp_path / 'android'
    shutil.copytree(ROOT / 'frontend/android', android,
                    ignore=shutil.ignore_patterns('build', '.gradle', 'local.properties'))
    manifest = android / 'app/src/main/AndroidManifest.xml'
    manifest.write_text(manifest.read_text().replace('<application ',
                        '<application android:networkSecurityConfig="@xml/android_test_trust" ', 1))
    resources = android / 'app/src/main/res'
    (resources / 'raw').mkdir(exist_ok=True)
    shutil.copyfile(certfile, resources / 'raw/android_test_ca.crt')
    (resources / 'xml/android_test_trust.xml').write_text('''<network-security-config>
        <base-config cleartextTrafficPermitted="false"><trust-anchors>
            <certificates src="system" /></trust-anchors></base-config>
        <domain-config cleartextTrafficPermitted="false"><domain>localhost</domain>
            <trust-anchors><certificates src="@raw/android_test_ca" /></trust-anchors>
        </domain-config></network-security-config>''')
    built = subprocess.run([str(android / 'gradlew'), '-p', str(android), f'-PwizzyOrigin={origin}',
                            ':app:assembleDebug', '--console=plain'], capture_output=True, text=True, timeout=180)
    assert built.returncode == 0, 'Isolated Android debug build failed'
    device('shell', 'am', 'force-stop', 'com.refineview.wizzy')
    subprocess.run([*adb, 'uninstall', 'com.refineview.wizzy'], capture_output=True, timeout=30)
    device('install', str(android / 'app/build/outputs/apk/debug/app-debug.apk'))
    device('reverse', f'tcp:{port}', f'tcp:{port}')

    app.state.settings.family_public_origin = origin
    redirect_requested = threading.Event()
    redirect_on_load = threading.Event()
    @app.get('/')
    def learning_page():
        from fastapi.responses import FileResponse, RedirectResponse
        if redirect_on_load.is_set():
            redirect_on_load.clear()
            redirect_requested.set()
            return RedirectResponse('https://example.invalid/blocked')
        return FileResponse(ROOT / 'frontend/family/dist/build/h5/index.html')

    app.mount('/', StaticFiles(directory=ROOT / 'frontend/family/dist/build/h5', html=True))
    server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False, loop='asyncio',
                                          ssl_certfile=str(certfile), ssl_keyfile=str(keyfile)))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            threading.Event().wait(0.02)
        assert server.started

        def phase(name, code=None, restart=True):
            if restart:
                device('shell', 'am', 'force-stop', 'com.refineview.wizzy')
                device('shell', 'am', 'start', '-n', 'com.refineview.wizzy/.MainActivity')
            result = subprocess.run(['node', 'tests/real-android.mjs'], cwd=ROOT / 'frontend/family',
                input=json.dumps({'origin': origin, 'serial': serial, 'phase': name, 'code': code}),
                capture_output=True, text=True, timeout=90)
            error = next((line for line in result.stderr.splitlines() if line.startswith('Android check failed at ')),
                         'Android browser check failed')
            assert result.returncode == 0, error
            assert json.loads(result.stdout) == {'phase': name, 'result': 'passed'}

        def pairing():
            return client.post(f"/api/v1/students/{student['id']}/pairing-codes",
                               headers=owner['headers']).json()['data']['code']

        phase('pair', pairing())
        image = tmp_path / 'wizzy-native-picker.jpg'
        Image.new('RGB', (800, 600), 'white').save(image)
        device('push', str(image), '/sdcard/Download/wizzy-native-picker.jpg')
        device('shell', 'am', 'broadcast', '-a', 'android.intent.action.MEDIA_SCANNER_SCAN_FILE',
               '-d', 'file:///sdcard/Download/wizzy-native-picker.jpg')

        def native_tap(label):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                device('shell', 'uiautomator', 'dump', '/sdcard/wizzy-picker.xml')
                screen = ElementTree.fromstring(device('shell', 'cat', '/sdcard/wizzy-picker.xml'))
                node = next((n for n in screen.iter('node') if n.get('text') == label
                             or n.get('content-desc') == label
                             or n.get('content-desc', '').startswith(label + ', ')), None)
                if node is not None:
                    left, top, right, bottom = map(int, re.findall(r'\d+', node.get('bounds')))
                    device('shell', 'input', 'tap', str((left + right) // 2), str((top + bottom) // 2))
                    return
            raise AssertionError(f'Native image picker did not show {label}')

        phase('open-image-picker', restart=False)
        native_tap('Files')
        native_tap('wizzy-native-picker.jpg')
        phase('image-selected', restart=False)
        with admin_engine.begin() as db:
            assert db.execute(text('SELECT count(*) FROM learning_devices')).scalar_one() == 1
            db.execute(text("UPDATE auth_sessions SET expires_at=now()-interval '1 second' WHERE audience='child'"))
        phase('restore-and-logout')
        with admin_engine.connect() as db:
            assert db.execute(text('SELECT count(*) FROM learning_devices')).scalar_one() == 0
            assert db.execute(text('SELECT count(*) FROM auth_sessions WHERE source_session_id IS NOT NULL')).scalar_one() == 1
        phase('signed-out')
        phase('pair', pairing())
        devices = client.get('/api/v1/child-sessions', headers=owner['headers']).json()['data']['items']
        assert len(devices) == 1
        assert client.delete('/api/v1/child-sessions/' + devices[0]['id'], headers=owner['headers']).status_code == 200
        phase('signed-out')
        redirect_on_load.set()
        device('shell', 'am', 'force-stop', 'com.refineview.wizzy')
        device('shell', 'am', 'start', '-n', 'com.refineview.wizzy/.MainActivity')
        assert redirect_requested.wait(5), 'The WebView must receive the test gateway redirect'
        device('shell', 'uiautomator', 'dump', '/sdcard/wizzy-redirect.xml')
        screen = ElementTree.fromstring(device('shell', 'cat', '/sdcard/wizzy-redirect.xml'))
        assert any(node.get('text') == '安全连接异常，请稍后重新连接。' for node in screen.iter('node'))
        retry = next(node for node in screen.iter('node') if node.get('text') == '重新连接')
        left, top, right, bottom = map(int, re.findall(r'\d+', retry.get('bounds')))
        device('shell', 'input', 'tap', str((left + right) // 2), str((top + bottom) // 2))
        phase('signed-out', restart=False)
    finally:
        device('shell', 'rm', '-f', '/sdcard/Download/wizzy-native-picker.jpg')
        device('shell', 'am', 'force-stop', 'com.refineview.wizzy')
        device('reverse', '--remove', f'tcp:{port}')
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()
