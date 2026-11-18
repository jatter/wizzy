"""Built daily homework UI against real restricted API and disposable PostgreSQL."""
import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from sqlalchemy import text
from test_homework_import_worker import (  # noqa: F401
    database,
    output,
    platform,
    provider,
    worker,
)

from wizzy.homework_import_worker import process_next

pytestmark = pytest.mark.skipif(os.environ.get('WIZZY_BROWSER_TESTS') != '1',
                                reason='Opt in after building family H5 with WIZZY_BROWSER_TESTS=1')
ROOT = Path(__file__).resolve().parents[2]


def test_daily_homework_real_api_browser(app, admin_engine, owner, student, provider, worker):  # noqa: F811
    assert (ROOT / 'frontend/family/dist/build/h5/index.html').is_file()
    with admin_engine.connect() as db:
        email = db.execute(text('''SELECT u.email FROM users u JOIN memberships m ON m.user_id=u.id
            WHERE m.tenant_id=:tenant AND m.role='owner' '''), {'tenant': owner['tenant_id']}).scalar_one()
    # Real HTTP transport and the restricted worker process this fixed independent provider fixture.
    # Live image-model quality is measured separately with the six supplied screenshot crops.
    reply = output()
    reply['items'].append({'subject': 'chinese', 'item_type': 'preparation', 'title': '准备2本语文本',
                           'raw_text': '准备2本语文本', 'date_raw': None})
    provider['replies'][:] = [reply]
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False, loop='asyncio'))
    server_thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    stop = threading.Event()
    worker_errors = []

    def drain_imports():
        while not stop.is_set():
            try:
                if not process_next(worker, app.state.settings):
                    stop.wait(.05)
            except Exception as error:
                worker_errors.append(type(error).__name__)
                stop.wait(.2)

    worker_thread = threading.Thread(target=drain_imports, daemon=True)
    server_thread.start()
    worker_thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and server_thread.is_alive() and time.monotonic() < deadline:
            threading.Event().wait(.02)
        assert server.started
        result = subprocess.run(['node', 'tests/real-daily-api.mjs'], cwd=ROOT / 'frontend/family',
                                capture_output=True, text=True, timeout=180,
                                input=json.dumps({'apiOrigin': f'http://127.0.0.1:{listener.getsockname()[1]}',
                                    'owner': {'email': email, 'password': 'Test-only-strong-passphrase'},
                                    'studentId': student['id']}))
        safe_error = next((line for line in result.stderr.splitlines()
                           if line.startswith('Daily homework browser failed at ')), 'Daily homework browser failed')
        assert result.returncode == 0, safe_error
        assert json.loads(result.stdout) == {'manual': 'passed', 'import': 'passed', 'child': 'passed',
                                            'verification': 'passed', 'page_errors': 0}
        assert not worker_errors
        with admin_engine.connect() as db:
            imported = db.execute(text('''SELECT i.* FROM homework_items i JOIN homework_item_sources s ON s.item_id=i.id
                WHERE i.student_id=:id AND i.item_type='written' '''), {'id': student['id']}).mappings().all()
            assert len(imported) == 1
            assert imported[0]['title'] == '家长核对后的小册第16课'
            assert imported[0]['work_state'] == 'completed'
            assert imported[0]['verification_state'] == 'guardian_confirmed'
            assert db.execute(text('SELECT count(*) FROM homework_import_runs WHERE student_id=:id'),
                              {'id': student['id']}).scalar_one() == 1
            assert db.execute(text('''SELECT count(*) FROM homework_item_events
                WHERE student_id=:id AND actor_audience='child' '''), {'id': student['id']}).scalar_one() >= 1
    finally:
        stop.set()
        worker_thread.join(timeout=10)
        server.should_exit = True
        server_thread.join(timeout=10)
        listener.close()
        assert not server_thread.is_alive() and not worker_thread.is_alive()
