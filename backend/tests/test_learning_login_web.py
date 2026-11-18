"""Built H5 and real WebAuthn through Chrome, restricted API and PostgreSQL."""
import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from starlette.staticfiles import StaticFiles

pytestmark = pytest.mark.skipif(os.environ.get('WIZZY_BROWSER_TESTS') != '1',
                              reason='opt in with WIZZY_BROWSER_TESTS=1 after building family H5')
ROOT = Path(__file__).resolve().parents[2]


def test_real_passkey_browser_login_and_remote_revocation(app, client, owner, student):
    code = client.post(f"/api/v1/students/{student['id']}/pairing-codes", headers=owner['headers']).json()['data']['code']
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    origin = f'http://localhost:{listener.getsockname()[1]}'
    app.state.settings.family_public_origin = origin
    app.mount('/', StaticFiles(directory=ROOT / 'frontend/family/dist/build/h5', html=True))
    server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False, loop='asyncio'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            threading.Event().wait(0.02)
        assert server.started
        result = subprocess.run(['node', 'tests/real-passkeys.mjs'], cwd=ROOT / 'frontend/family',
                                input=json.dumps({'origin': origin, 'code': code, 'owner': owner['headers']}),
                                capture_output=True, text=True, timeout=120)
        error = next((line for line in result.stderr.splitlines() if line.startswith('Passkey browser check failed at ')),
                     'Passkey browser check failed')
        assert result.returncode == 0, error
        assert json.loads(result.stdout) == {'registration': 'passed', 'login': 'passed', 'reload': 'passed',
                                           'guardian_gate': 'passed', 'remote_revocation': 'passed', 'page_errors': 0}
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()
