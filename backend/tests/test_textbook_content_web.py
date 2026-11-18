"""Built UI -> real HTTP/API/worker/PostgreSQL; only the antivirus is a test double."""
import base64
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest
import uvicorn
from sqlalchemy import text
from test_pdf_processing import pdf_bytes
from test_textbook_processing import worker as worker

from wizzy.security import passwords

pytestmark = pytest.mark.skipif(
    os.environ.get('WIZZY_BROWSER_TESTS') != '1',
    reason='opt in with WIZZY_BROWSER_TESTS=1 after building frontend/platform',
)
ROOT = Path(__file__).resolve().parents[2]


def test_built_textbook_content_with_real_api_worker_and_database(app, admin_engine, worker, monkeypatch):
    frontend = ROOT / 'frontend/platform'
    assert shutil.which('node'), 'Node.js is required for the opt-in browser check'
    assert (frontend / 'dist/index.html').is_file(), 'Build frontend/platform first'
    monkeypatch.setattr('wizzy.platform_identity.generate_captcha_code', lambda: 'K7M2')
    accounts = []
    for role in ('security_admin', 'security_admin', 'operator'):
        account_id, password = uuid4(), secrets.token_urlsafe(32)
        email = f'content-web-{account_id}@example.test'
        with admin_engine.begin() as db:
            db.execute(text('''INSERT INTO platform_accounts
                (id,email,password_hash,encrypted_totp,credential_version,role)
                VALUES(:id,:email,:password,'unused',:version,:role)'''),
                {'id': account_id, 'email': email, 'password': passwords.hash(password),
                 'version': uuid4(), 'role': role})
        accounts.append({'id': str(account_id), 'email': email, 'password': password})
    pdf = pdf_bytes(('First source page', '', 'Third source page'))
    code = 'CW_' + uuid4().hex.upper()
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False, loop='asyncio'))
    api_thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    stopped, worker_failed = threading.Event(), threading.Event()

    def consume():
        try:
            while not stopped.is_set():
                worker.consume_one()
                stopped.wait(0.05)
        except Exception:
            # Worker errors may contain database credentials; expose only a failure flag.
            worker_failed.set()

    worker_thread = threading.Thread(target=consume, daemon=True)
    api_thread.start()
    worker_thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and api_thread.is_alive() and time.monotonic() < deadline:
            stopped.wait(0.02)
        assert server.started, 'Isolated API failed to start'
        result = subprocess.run(
            ['node', 'tests/real-textbook-content.mjs'], cwd=frontend,
            capture_output=True, text=True, timeout=180,
            input=json.dumps({'apiOrigin': f'http://127.0.0.1:{listener.getsockname()[1]}',
                              'accounts': accounts, 'code': code,
                              'pdf': base64.b64encode(pdf).decode()}),
        )
        safe_error = next((line for line in result.stderr.splitlines()
                           if line.startswith('Real textbook browser check failed at ')),
                          'Real textbook browser integration failed')
        assert not worker_failed.is_set(), 'Isolated textbook worker failed'
        assert result.returncode == 0, safe_error
        assert json.loads(result.stdout) == {
            'author': 'passed', 'reviewer': 'passed', 'operator': 'passed',
            'txt_download': 'passed', 'page_preview': 'passed', 'page_errors': 0,
        }
        stopped.set()
        worker_thread.join(10)
        assert not worker_thread.is_alive(), 'Isolated textbook worker failed to stop'
        assert not worker_failed.is_set(), 'Isolated textbook worker failed'
        assert worker.scanner.calls == [pdf]  # Explicit CleanForTest, never real ClamAV evidence.
        with admin_engine.connect() as db:
            edition = db.execute(text('''SELECT e.id,e.current_revision,r.state
                FROM textbook_editions e JOIN textbook_edition_revisions r
                ON r.edition_id=e.id AND r.revision=e.current_revision WHERE e.code=:code'''),
                                 {'code': code}).one()
            assert (edition.current_revision, edition.state) == (3, 'approved')
            document = db.execute(text('SELECT id,sha256,status FROM textbook_documents WHERE edition_id=:id'),
                                  {'id': edition.id}).one()
            assert document.sha256 == hashlib.sha256(pdf).hexdigest()
            assert document.status == 'quarantined'
            job = db.execute(text('SELECT id,state,result FROM textbook_processing_jobs WHERE document_id=:id'),
                             {'id': document.id}).one()
            assert job.state == 'draft_ready'
            assert job.result['draft']['pages'][1]['text'] == ''
            content = db.execute(text('SELECT id,current_revision,state,edition_revision FROM textbook_contents WHERE job_id=:id'),
                                 {'id': job.id}).one()
            assert (content.current_revision, content.state, content.edition_revision) == (5, 'withdrawn', 3)
            revisions = db.execute(text('''SELECT revision,action,state,actor_id,body,sample_pages
                FROM textbook_content_revisions WHERE content_id=:id ORDER BY revision'''),
                {'id': content.id}).all()
            assert [row.action for row in revisions] == ['create', 'revise', 'submit', 'approve', 'withdraw']
            assert [row.state for row in revisions] == ['draft', 'draft', 'pending_review', 'published', 'withdrawn']
            assert [str(row.actor_id) for row in revisions] == [accounts[0]['id']] * 3 + [accounts[1]['id'], accounts[0]['id']]
            assert revisions[0].body['pages'][1] == {'number': 2, 'text': '', 'verified': False}
            corrected = revisions[1].body
            assert corrected['pages'][1]['text'] == '人工核对：原 PDF 本页为空白'
            assert all(page['verified'] for page in corrected['pages'])
            assert corrected['chapters'] == [
                {'code': 'CH1', 'title': '第一章', 'parent_code': None, 'first_page': 1, 'last_page': 3},
                {'code': 'CH1_1', 'title': '第一节', 'parent_code': 'CH1', 'first_page': 1, 'last_page': 2},
            ]
            assert all(row.body == corrected for row in revisions[2:])
            assert revisions[3].sample_pages == [1, 2, 3]
            audit = db.execute(text('''SELECT action,actor_id FROM platform_audit_events
                WHERE actor_id IN (:author,:reviewer) AND action LIKE 'textbooks.content.%' '''),
                {'author': accounts[0]['id'], 'reviewer': accounts[1]['id']}).all()
            assert sorted(row.action for row in audit) == sorted(
                f'textbooks.content.{action}' for action in ('create', 'revise', 'submit', 'approve', 'withdraw'))
            assert str(next(row.actor_id for row in audit if row.action == 'textbooks.content.approve')) == accounts[1]['id']
    finally:
        stopped.set()
        worker_thread.join(10)
        server.should_exit = True
        api_thread.join(10)
        listener.close()
