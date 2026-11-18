"""Opt-in built browser -> same-origin proxy -> restricted API -> disposable PostgreSQL."""
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
from sqlalchemy import create_engine, text

from wizzy.security import passwords

pytestmark = pytest.mark.skipif(
    os.environ.get('WIZZY_BROWSER_TESTS') != '1',
    reason='opt in with WIZZY_BROWSER_TESTS=1 after building frontend/platform',
)
ROOT = Path(__file__).resolve().parents[2]


def test_built_platform_browser_with_real_api(app, admin_engine, database, monkeypatch):
    frontend = ROOT / 'frontend/platform'
    assert shutil.which('node'), 'Node.js is required for the opt-in browser check'
    assert (frontend / 'dist/index.html').is_file(), 'Build frontend/platform first'
    with admin_engine.connect() as db:
        family_counts = tuple(db.execute(text(
            'SELECT (SELECT count(*) FROM students), (SELECT count(*) FROM student_access)'
        )).one())
    monkeypatch.setattr('wizzy.platform_identity.generate_captcha_code', lambda: 'K7M2')
    accounts = []
    for role in ('security_admin', 'operator', 'security_admin'):
        account_id, password = uuid4(), secrets.token_urlsafe(32)
        email = f'web-{account_id}@example.test'
        with admin_engine.begin() as db:
            db.execute(text('''INSERT INTO platform_accounts
                (id,email,password_hash,encrypted_totp,credential_version,role)
                VALUES(:id,:email,:password,'unused',:version,:role)'''),
                {'id': account_id, 'email': email, 'password': passwords.hash(password),
                 'version': uuid4(), 'role': role})
        accounts.append({'email': email, 'password': password, 'id': str(account_id)})
    reviewer = accounts.pop()
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
        model_key = 'browser-' + uuid4().hex
        result = subprocess.run(
            ['node', 'tests/real-api.mjs'], cwd=frontend, capture_output=True, text=True,
            input=json.dumps({'apiOrigin': f'http://127.0.0.1:{listener.getsockname()[1]}',
                              'accounts': accounts, 'reviewer': reviewer, 'modelKey': model_key}), timeout=90,
        )
        # Never expose arbitrary browser errors or subprocess input (temporary credentials).
        safe_error = next((line for line in result.stderr.splitlines()
                           if line.startswith('Real API browser check failed at ')), 'Real browser integration failed')
        assert result.returncode == 0, safe_error
        assert json.loads(result.stdout) == {'security_admin': 'passed', 'operator': 'passed', 'page_errors': 0}
        with admin_engine.connect() as db:
            evaluation = db.execute(text('''SELECT id,state,split,revision,handbook_version
                FROM evaluation_datasets WHERE name=:name'''),
                {'name': f'真实联调评测-{model_key}'}).one()
            assert (evaluation.state, evaluation.split, evaluation.revision, evaluation.handbook_version) == (
                'draft', 'validation', 1, 'browser-handbook-v1')
            assert db.execute(text('SELECT count(*) FROM evaluation_samples WHERE dataset_id=:id'),
                              {'id': evaluation.id}).scalar_one() == 0
            assert db.execute(text('''SELECT count(*) FROM platform_audit_events
                WHERE actor_id=:id AND action='evaluation.datasets.create' '''),
                {'id': accounts[0]['id']}).scalar_one() == 1
            textbook = db.execute(text('SELECT id,current_revision FROM textbook_editions WHERE code=:code'),
                                  {'code': model_key.upper()}).one()
            assert textbook.current_revision == 6
            textbook_revisions = db.execute(text('''SELECT revision,actor_id,action,state,config
                FROM textbook_edition_revisions WHERE edition_id=:id ORDER BY revision'''),
                {'id': textbook.id}).all()
            assert [row.action for row in textbook_revisions] == ['create', 'submit', 'approve', 'withdraw', 'revise', 'submit']
            assert [row.state for row in textbook_revisions] == ['draft', 'pending_review', 'approved', 'withdrawn', 'draft', 'pending_review']
            assert [str(row.actor_id) for row in textbook_revisions] == [accounts[0]['id'], accounts[0]['id'], reviewer['id'],
                                                                       accounts[0]['id'], accounts[0]['id'], accounts[0]['id']]
            assert textbook_revisions[0].config['rights']['reference'] == 'CONTRACT-BROWSER-001'
            assert textbook_revisions[4].config['title'] == '真实联调数学修订版'
            document = db.execute(text('''SELECT id,edition_revision,status,filename,size_bytes,sha256,created_by
                FROM textbook_documents WHERE edition_id=:id'''), {'id': textbook.id}).one()
            pdf = b'%PDF-1.7\nreal browser isolation check\n%%EOF'
            assert document.edition_revision == 3 and document.status == 'quarantined'
            assert document.filename == 'real-textbook.pdf' and document.size_bytes == len(pdf)
            assert document.sha256 == hashlib.sha256(pdf).hexdigest()
            assert str(document.created_by) == accounts[0]['id']
            processing = db.execute(text('''SELECT document_id,edition_revision,created_by,state,attempts,reason
                FROM textbook_processing_jobs WHERE document_id=:id'''), {'id': document.id}).one()
            assert processing.document_id == document.id and processing.edition_revision == 3
            assert str(processing.created_by) == accounts[0]['id']
            assert processing.state == 'queued' and processing.attempts == 0
            assert processing.reason == '真实联调申请扫描与文本提取'
            assert db.execute(text('''SELECT count(*) FROM platform_audit_events
                WHERE actor_id=:id AND action='textbooks.processing.request' '''),
                {'id': accounts[0]['id']}).scalar_one() == 1
            stored = app.state.settings.storage_root / 'textbook-quarantine' / f'{document.id.hex}.pdf'
            assert stored.read_bytes() == pdf
            assert stored.stat().st_mode & 0o777 == 0o600
            assert set(db.execute(text('''SELECT action FROM platform_audit_events WHERE actor_id=:id
                AND action IN ('textbooks.documents.intent','textbooks.documents.received')'''),
                {'id': accounts[0]['id']}).scalars()) == {'textbooks.documents.intent', 'textbooks.documents.received'}
            assert db.execute(text('''SELECT count(*) FROM platform_sessions
                WHERE account_id=:id AND revoked_at IS NOT NULL'''), {'id': reviewer['id']}).scalar_one() == 1
            assert db.execute(text('''SELECT count(*) FROM platform_audit_events
                WHERE actor_id=:id AND action='textbooks.approve' '''),
                {'id': reviewer['id']}).scalar_one() == 1
            model = db.execute(text('SELECT id,current_revision FROM model_catalog WHERE model_key=:key'),
                               {'key': model_key}).one()
            assert model.current_revision == 2
            revisions = db.execute(text('''SELECT revision,actor_id,config FROM model_catalog_revisions
                WHERE model_id=:id ORDER BY revision'''), {'id': model.id}).all()
            assert [row.revision for row in revisions] == [1, 2]
            assert all(str(row.actor_id) == accounts[0]['id'] for row in revisions)
            assert revisions[0].config['enabled'] is False
            assert revisions[1].config['enabled'] is True
            assert revisions[1].config['secret_ref'] == 'secret://providers/browser-test/primary'
            assert revisions[1].config['contract_status'] == 'approved'
            school = db.execute(text('SELECT id,current_revision FROM school_directory WHERE code=:code'),
                                {'code': model_key.upper()}).one()
            assert school.current_revision == 2
            school_revisions = db.execute(text('''SELECT revision,actor_id,config FROM school_directory_revisions
                WHERE school_id=:id ORDER BY revision'''), {'id': school.id}).all()
            assert [row.revision for row in school_revisions] == [1, 2]
            assert all(str(row.actor_id) == accounts[0]['id'] for row in school_revisions)
            assert school_revisions[0].config == {
                'name': '真实联调实验学校', 'region': '上海市 / 浦东新区',
                'academic_year': '2026-2027', 'status': 'active',
            }
            assert school_revisions[1].config == {
                'name': '真实联调实验学校（修订）', 'region': '上海市 / 浦东新区',
                'academic_year': '2027-2028', 'status': 'disabled',
            }
            nodes = db.execute(text('''SELECT id,code,kind,academic_year,current_revision
                FROM school_structure_nodes WHERE school_id=:id ORDER BY code'''),
                {'id': school.id}).all()
            assert [(row.code, row.kind, row.academic_year, row.current_revision) for row in nodes] == [
                ('CLASS_A', 'class', '2026-2027', 2),
                ('CLASS_B', 'class', '2026-2027', 1),
                ('GRADE_A', 'grade', '2026-2027', 1),
                ('GRADE_B', 'grade', '2026-2027', 2),
            ]
            node_ids = {row.code: str(row.id) for row in nodes}
            structure_revisions = db.execute(text('''SELECT n.code,r.revision,r.actor_id,
                r.config,r.reason,r.operation_id FROM school_structure_revisions r
                JOIN school_structure_nodes n ON n.id=r.node_id
                WHERE n.school_id=:id ORDER BY n.code,r.revision'''), {'id': school.id}).all()
            assert [(row.code, row.revision, row.config, row.reason) for row in structure_revisions] == [
                ('CLASS_A', 1, {'name': '联调一班', 'status': 'active', 'parent_id': node_ids['GRADE_A']},
                 '真实联调组织登记'),
                ('CLASS_A', 2, {'name': '联调一班', 'status': 'active', 'parent_id': node_ids['GRADE_B']},
                 '真实联调班级归属调整'),
                ('CLASS_B', 1, {'name': '联调二班', 'status': 'active', 'parent_id': node_ids['GRADE_B']},
                 '真实联调组织登记'),
                ('GRADE_A', 1, {'name': '联调一年级', 'status': 'active', 'parent_id': None},
                 '真实联调组织登记'),
                ('GRADE_B', 1, {'name': '联调二年级', 'status': 'active', 'parent_id': None},
                 '真实联调组织登记'),
                ('GRADE_B', 2, {'name': '联调二年级', 'status': 'disabled', 'parent_id': None},
                 '真实联调停用年级'),
            ]
            assert all(str(row.actor_id) == accounts[0]['id'] for row in structure_revisions)
            assert len({row.operation_id for row in structure_revisions}) == 6
            student = db.execute(text('''SELECT id FROM school_roster_students
                WHERE school_id=:id AND code='STU_WEB_01' '''), {'id': school.id}).scalar_one()
            enrollment = db.execute(text('''SELECT id,current_revision FROM school_enrollments
                WHERE student_id=:id AND school_id=:school AND academic_year='2026-2027' '''),
                {'id': student, 'school': school.id}).one()
            assert enrollment.current_revision == 3
            roster_revisions = db.execute(text('''SELECT revision,actor_id,config,reason,operation_id
                FROM school_enrollment_revisions WHERE enrollment_id=:id ORDER BY revision'''),
                {'id': enrollment.id}).all()
            assert [(r.revision, r.config, r.reason) for r in roster_revisions] == [
                (1, {'display_name': '联调学生', 'class_id': node_ids['CLASS_A'], 'status': 'enrolled'},
                 '真实联调学生登记'),
                (2, {'display_name': '联调学生', 'class_id': node_ids['CLASS_B'], 'status': 'enrolled'},
                 '真实联调学生调班'),
                (3, {'display_name': '联调学生', 'class_id': None, 'status': 'paused'},
                 '真实联调学生休学'),
            ]
            assert all(str(r.actor_id) == accounts[0]['id'] for r in roster_revisions)
            assert len({r.operation_id for r in roster_revisions}) == 3
            assert tuple(db.execute(text(
                'SELECT (SELECT count(*) FROM students), (SELECT count(*) FROM student_access)'
            )).one()) == family_counts
            target = db.execute(text('''SELECT role,status,governance_revision FROM platform_accounts
                WHERE id=:id'''), {'id': accounts[1]['id']}).one()
            assert tuple(target) == ('operator', 'active', 2)
            changes = db.execute(text('''SELECT c.actor_id,c.expected_revision,c.previous_role,c.previous_status,
                c.role,c.status,c.requires_approval,d.decision,d.resulting_revision,d.actor_id AS decided_by
                FROM platform_account_changes c LEFT JOIN platform_account_decisions d ON d.change_id=c.id
                WHERE c.account_id=:id ORDER BY c.created_at,c.id'''), {'id': accounts[1]['id']}).all()
            assert len(changes) == 3
            assert all(str(row.actor_id) == accounts[0]['id'] for row in changes)
            assert [row.expected_revision for row in changes] == [0, 1, 2]
            assert [row.previous_role for row in changes] == ['operator'] * 3
            assert [row.previous_status for row in changes] == ['active', 'disabled', 'active']
            assert [row.role for row in changes] == ['operator', 'operator', 'security_admin']
            assert [row.status for row in changes] == ['disabled', 'active', 'active']
            assert [row.requires_approval for row in changes] == [False, False, True]
            assert [row.decision for row in changes] == ['applied', 'applied', None]
            assert [row.resulting_revision for row in changes] == [1, 2, None]
            assert all(str(row.decided_by) == accounts[0]['id'] for row in changes[:2])
            assert changes[2].decided_by is None
            for account in accounts:
                assert db.execute(text('''SELECT count(*) FROM platform_sessions
                    WHERE account_id=:id AND revoked_at IS NOT NULL'''),
                    {'id': account['id']}).scalar_one() == 1
                actions = set(db.execute(text('SELECT action FROM platform_audit_events WHERE actor_id=:id'),
                                         {'id': account['id']}).scalars())
                assert {'login', 'me', 'roles'} <= actions
                if account is accounts[0]:
                    assert {'accounts', 'audit', 'logout_all', 'models.create', 'models.revise', 'accounts.change', 'schools.create', 'schools.revise',
                            'schools.structure.create', 'schools.structure.revise',
                            'schools.roster.create', 'schools.roster.revise',
                            'textbooks.create', 'textbooks.submit', 'textbooks.withdraw', 'textbooks.revise'} <= actions
                else:
                    assert 'logout' in actions
                    assert not {'accounts', 'audit', 'models.create', 'models.revise', 'accounts.change', 'schools.create', 'schools.revise',
                            'schools.structure.create', 'schools.structure.revise',
                            'schools.roster.create', 'schools.roster.revise',
                            'textbooks.create', 'textbooks.submit', 'textbooks.approve', 'textbooks.withdraw', 'textbooks.revise'} & actions
        # Complete the revoked job through the restricted capability, preserving history
        # and leaving no active work for subsequent global-queue tests.
        worker_engine = create_engine(database['worker_url'], hide_parameters=True)
        try:
            with worker_engine.begin() as db:
                assert db.execute(text(
                    "SELECT public.textbook_processing_worker('claim',NULL,NULL,NULL)"
                )).scalar_one() == {'status': 'blocked'}
            with admin_engine.connect() as db:
                blocked = db.execute(text('''SELECT state,attempts,result FROM textbook_processing_jobs
                    WHERE document_id=:id'''), {'id': document.id}).one()
                assert blocked.state == 'blocked' and blocked.attempts == 0
                assert blocked.result == {'code': 'authorization_changed'}
        finally:
            worker_engine.dispose()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive(), 'Isolated API did not stop'
