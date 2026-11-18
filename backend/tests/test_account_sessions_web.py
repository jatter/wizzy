"""Real browser session revocation with lost receipt against disposable PostgreSQL."""
import json
import os
import secrets
import socket
import subprocess
import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest
import uvicorn
from sqlalchemy import text
from test_account_governance import database  # noqa: F401

from wizzy.security import passwords

pytestmark = pytest.mark.skipif(
    os.environ.get('WIZZY_BROWSER_TESTS') != '1',
    reason='opt in with WIZZY_BROWSER_TESTS=1 after building frontend/platform',
)


def test_built_session_revocation_preserves_new_login(app, admin_engine, tmp_path, monkeypatch):
    frontend = Path(__file__).resolve().parents[2] / 'frontend/platform'
    assert (frontend / 'dist/index.html').is_file(), 'Build frontend/platform first'
    monkeypatch.setattr('wizzy.platform_identity.generate_captcha_code', lambda: 'K7M2')
    accounts = []
    for role in ('security_admin', 'operator'):
        identifier, password = uuid4(), secrets.token_urlsafe(32)
        account = {'id': str(identifier), 'email': f'session-web-{identifier}@example.test', 'password': password}
        with admin_engine.begin() as db:
            db.execute(text('''INSERT INTO platform_accounts
                (id,email,password_hash,encrypted_totp,credential_version,role)
                VALUES(:id,:email,:password,'unused',:version,:role)'''),
                {**account, 'password': passwords.hash(password), 'version': uuid4(), 'role': role})
        accounts.append(account)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False, loop='asyncio'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            threading.Event().wait(0.02)
        assert server.started, 'Isolated API failed to start'
        result = subprocess.run(
            ['node', 'tests/account-sessions-real-api.mjs'], cwd=frontend, capture_output=True, text=True,
            input=json.dumps({'apiOrigin': f'http://127.0.0.1:{listener.getsockname()[1]}',
                              'accounts': accounts, 'screenshotDirectory': str(tmp_path)}), timeout=90,
        )
        safe_error = next((line for line in result.stderr.splitlines()
                           if line.startswith('Session browser failed at ')), 'Session browser integration failed')
        assert result.returncode == 0, safe_error
        assert json.loads(result.stdout) == {'page_errors': 0, 'old_session': 401, 'new_session': 200, 'attempts': 2}
        with admin_engine.connect() as db:
            receipt = db.execute(text('SELECT account_id,actor_id,reason,revoked_count FROM platform_session_revocations')).one()
            assert (str(receipt.account_id), str(receipt.actor_id), receipt.reason, receipt.revoked_count) == (
                accounts[1]['id'], accounts[0]['id'], '真实联调安全核查', 1)
            assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE action='accounts.sessions.revoke'")).scalar_one() == 1
            sessions = db.execute(text('SELECT revoked_at FROM platform_sessions WHERE account_id=:id ORDER BY created_at'), {'id': accounts[1]['id']}).scalars().all()
            assert len(sessions) == 2 and sessions[0] is not None and sessions[1] is None
            assert db.execute(text('SELECT governance_revision FROM platform_accounts WHERE id=:id'), {'id': accounts[1]['id']}).scalar_one() == 0
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
