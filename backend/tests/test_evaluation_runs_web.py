"""Built model-evaluation UI -> restricted API -> disposable PostgreSQL."""
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
from test_evaluation_runs import corpus as corpus  # noqa: F401
from test_evaluation_runs import evaluators as evaluators  # noqa: F401
from test_evaluation_runs import execution as execution  # noqa: F401
from test_evaluation_runs import platform as platform  # noqa: F401
from test_evaluation_runs import readable_result as readable_result  # noqa: F401
from test_evaluation_runs import result_context as result_context  # noqa: F401
from test_evaluation_runs import source as source  # noqa: F401

from wizzy.security import passwords

pytestmark = pytest.mark.skipif(
    os.environ.get('WIZZY_BROWSER_TESTS') != '1',
    reason='opt in with WIZZY_BROWSER_TESTS=1 after building frontend/platform',
)


def test_built_evaluation_run_import_and_report(app, admin_engine, corpus, tmp_path, monkeypatch):
    frontend = Path(__file__).resolve().parents[2] / 'frontend/platform'
    assert (frontend / 'dist/index.html').is_file(), 'Build frontend/platform first'
    dataset, model, sample = corpus
    identifier, password = uuid4(), secrets.token_urlsafe(32)
    email = f'run-web-{identifier}@example.test'
    monkeypatch.setattr('wizzy.platform_identity.generate_captcha_code', lambda: 'K7M2')
    with admin_engine.begin() as db:
        db.execute(text('''INSERT INTO platform_accounts
            (id,email,password_hash,encrypted_totp,credential_version,role)
            VALUES(:id,:email,:password,'unused',:version,'security_admin')'''),
            {'id': identifier, 'email': email, 'password': passwords.hash(password),
             'version': uuid4()})
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
            ['node', 'tests/evaluation-runs-real-api.mjs'], cwd=frontend, capture_output=True, text=True,
            input=json.dumps({'apiOrigin': f'http://127.0.0.1:{listener.getsockname()[1]}',
                              'account': {'email': email, 'password': password},
                              'datasetId': dataset['id'], 'modelId': model['id'], 'sampleId': sample,
                              'screenshotDirectory': str(tmp_path)}), timeout=100,
        )
        safe_error = next((line for line in result.stderr.splitlines()
                           if line.startswith(('Evaluation browser failed at ', 'Layout overflow '))),
                          'Evaluation browser integration failed')
        assert result.returncode == 0, safe_error
        outcome = json.loads(result.stdout)
        assert outcome['page_errors'] == 0 and outcome['production_eligible'] is False
        with admin_engine.connect() as db:
            item = db.execute(text('SELECT state,revision,manifest FROM evaluation_runs WHERE id=:id'),
                              {'id': outcome['run_id']}).one()
            assert (item.state, item.revision) == ('completed', 2)
            assert item.manifest['dataset']['locked_digest'] == dataset['locked_digest']
            assert db.execute(text('SELECT count(*) FROM evaluation_run_predictions WHERE run_id=:id'),
                              {'id': outcome['run_id']}).scalar_one() == 1
            assert db.execute(text("SELECT count(*) FROM platform_audit_events WHERE actor_id=:actor AND action LIKE 'evaluation.runs.%'"),
                              {'actor': identifier}).scalar_one() == 2
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
