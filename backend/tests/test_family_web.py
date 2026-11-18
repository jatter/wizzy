"""Opt-in built family H5 -> restricted API -> disposable PostgreSQL integration."""
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
from test_correction_attempts import wrong_question as wrong_question  # noqa: F401
from test_execution import execution as execution  # noqa: F401
from test_mistakes import drain
from test_result_read import readable_result as readable_result  # noqa: F401
from test_results import result_context as result_context  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.environ.get('WIZZY_BROWSER_TESTS') != '1',
    reason='opt in with WIZZY_BROWSER_TESTS=1 after building frontend/family H5',
)
ROOT = Path(__file__).resolve().parents[2]


def test_built_family_browser_with_real_api(app, admin_engine, client, owner, wrong_question, database, readable_result):
    drain(database)
    frontend = ROOT / 'frontend/family'
    assert shutil.which('node'), 'Node.js is required for the opt-in browser check'
    assert (frontend / 'dist/build/h5/index.html').is_file(), 'Build frontend/family H5 first'
    registration = {'email': f'family-web-{uuid4().hex}@example.test', 'password': secrets.token_urlsafe(32)}
    with admin_engine.begin() as db:
        # The current mistake bank projects structured homework outputs, while the
        # conservative fixture below still supplies guardian revisions for reports.
        content = json.dumps({'pages': [{'position': 1, 'subject': 'math', 'note': '',
            'questions': [{'number': '第1题', 'verdict': 'incorrect', 'student_answer': '21',
                           'reasoning': '12 + 8 = 20，需要重新核对加法。', 'steps': [], 'answer': '20'}]},
            *[{'position': position, 'subject': 'unknown', 'note': '', 'questions': []} for position in (2, 3)]]},
                             ensure_ascii=False)
        db.execute(text("""INSERT INTO homework_grading_batches
            (submission_id,event_id,token,lease_until,state,model_config,prompts,finished_at)
            VALUES(:submission,:event,:token,now(),'settled','{}','[]',now())"""),
            {'submission': readable_result[3], 'event': readable_result[0]['event_id'], 'token': uuid4()})
        db.execute(text("""INSERT INTO homework_model_outputs
            (submission_id,tenant_id,student_id,sequence,prompt_id,prompt_name,prompt_content,
             prompt_priority,model_id,model_name,model_priority,status,content,finished_at)
            SELECT id,tenant_id,student_id,1,:prompt,'浏览器验收','已核对的结构化结果',10,
                   :model,'browser-fixture',1,'succeeded',:content,now()
            FROM submissions WHERE id=:submission"""),
            {'submission': readable_result[3], 'prompt': uuid4(), 'model': uuid4(), 'content': content})
        # Keep the confirmed error due inside the current month, including on its last day.
        db.execute(text("""UPDATE verdict_revisions SET created_at=
            (date_trunc('month',clock_timestamp() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai')
            WHERE question_id=:id AND provenance='guardian' AND verdict='incorrect'"""),
            {'id': wrong_question['question_id']})
        owner_email = db.execute(text('''SELECT u.email FROM users u
            JOIN memberships m ON m.user_id=u.id WHERE m.tenant_id=:id AND m.role='owner' '''),
            {'id': owner['tenant_id']}).scalar_one()
    source_student = client.get(f'/api/v1/submissions/{readable_result[3]}', headers=owner['headers']).json()['data']['student_id']
    bank = client.get(f'/api/v1/students/{source_student}/mistake-bank', headers=owner['headers'])
    assert bank.status_code == 200
    assert len(bank.json()['data']['items']) == 1
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
            ['node', 'tests/real-api.mjs'], cwd=frontend, capture_output=True, text=True,
            input=json.dumps({'apiOrigin': f'http://127.0.0.1:{listener.getsockname()[1]}',
                              'registration': registration,
                              'owner': {'email': owner_email, 'password': 'Test-only-strong-passphrase'}}),
            timeout=120,
        )
        # Do not echo arbitrary browser errors or input containing temporary credentials.
        safe_error = next((line for line in result.stderr.splitlines()
                           if line.startswith('Real family API browser check failed at ')),
                          'Real family browser integration failed')
        assert result.returncode == 0, safe_error
        assert json.loads(result.stdout) == {'registration': 'passed', 'timetable': 'passed', 'reports': 'passed', 'review': 'passed', 'upload': 'passed', 'history': 'passed', 'page_errors': 0}
        with admin_engine.connect() as db:
            student = db.execute(text('''SELECT s.id,s.nickname,s.grade,s.revision,s.consent_active
                FROM students s JOIN memberships m ON m.tenant_id=s.tenant_id
                JOIN users u ON u.id=m.user_id WHERE u.email=:email'''),
                {'email': registration['email']}).one()
            assert (student.nickname, student.grade, student.revision, student.consent_active) == ('小禾同学', 4, 2, False)
            assert db.execute(text('SELECT granted FROM consent_records WHERE student_id=:id ORDER BY created_at'),
                              {'id': student.id}).scalars().all() == [True, False]
            assert db.execute(text('''SELECT granted FROM evaluation_consent_records
                WHERE student_id=:id ORDER BY revision'''), {'id': student.id}).scalars().all() == [True, False]
            assert db.execute(text('''SELECT count(*) FROM auth_sessions WHERE student_id=:id
                AND audience='child' AND revoked_at IS NOT NULL'''), {'id': student.id}).scalar_one() == 1
            term = db.execute(text('''SELECT id,revision FROM terms
                WHERE tenant_id=:id AND name='浏览器验收秋季' '''), {'id': owner['tenant_id']}).one()
            # One successful template, confirmed conflict, removal, and competing update.
            assert db.execute(text('''SELECT count(*) FROM timetable_versions
                WHERE term_id=:id AND kind='template' '''), {'id': term.id}).scalar_one() == 4
            overrides = db.execute(text('''SELECT action,reason FROM lesson_overrides
                WHERE term_id=:id ORDER BY revision'''), {'id': term.id}).all()
            assert [tuple(row) for row in overrides] == [
                ('move', '学校活动调课'), ('cancel', '临时停课'),
                ('restore', '恢复正常上课'), ('cancel', '回执丢失后核对')]
            task = db.execute(text('''SELECT id,draft_answer,draft_revision,state FROM review_tasks
                WHERE question_id=:id'''), {'id': wrong_question['question_id']}).one()
            assert (task.draft_answer, task.draft_revision, task.state) == ('先算 12 + 8，答案是 20。', 2, 'awaiting_review')
            attempt = db.execute(text('''SELECT outcome,student_answer,independent_correct FROM review_attempts
                WHERE task_id=:id'''), {'id': task.id}).one()
            assert (attempt.outcome, attempt.student_answer, attempt.independent_correct) == ('submitted', task.draft_answer, False)
            actions = set(db.execute(text('SELECT action FROM audit_events WHERE resource_id=:id'),
                                     {'id': task.id}).scalars())
            assert {'review.draft_saved', 'review.attempted'} <= actions
            plans = db.execute(text('SELECT id,report_id FROM review_plans WHERE tenant_id=:id'),
                               {'id': owner['tenant_id']}).all()
            assert len(plans) == 1
            assert db.execute(text('SELECT task_id FROM review_plan_entries WHERE plan_id=:id'),
                              {'id': plans[0].id}).scalar_one() == task.id
            assert db.execute(text("SELECT count(*) FROM audit_events WHERE action='review_plan.generated' AND resource_id=:id"),
                              {'id': plans[0].id}).scalar_one() == 1
            reports = db.execute(text('''SELECT id,revision,source_count,summary FROM reports
                WHERE tenant_id=:id AND source_count>0 ORDER BY revision'''),
                {'id': owner['tenant_id']}).all()
            assert len(reports) == 2
            assert reports[0].id == plans[0].report_id
            assert (reports[0].revision, reports[1].revision) == (1, 2)
            assert reports[1].source_count == reports[0].source_count + 1
            assert reports[0].summary['reviews']['attempts'] == 0
            assert reports[1].summary['reviews']['awaiting_review'] == 1
            assert reports[1].summary['reviews']['completed'] == 0
            assert db.execute(text("SELECT count(*) FROM report_sources WHERE report_id=:id AND kind='review'"),
                              {'id': reports[1].id}).scalar_one() == 1
            assert db.execute(text("SELECT count(*) FROM audit_events WHERE action='report.rebuilt' AND resource_id=:id"),
                              {'id': reports[1].id}).scalar_one() == 1
            created = db.execute(text("""SELECT s.id,s.status,j.id AS job_id,j.status AS job_status
                FROM submissions s JOIN jobs j ON j.submission_id=s.id
                WHERE s.tenant_id=:tenant AND s.id<>:original"""),
                {'tenant': owner['tenant_id'], 'original': readable_result[3]}).one()
            assert (created.status, created.job_status) == ('cancelled', 'cancelled')
            pages = db.execute(text("""SELECT p.position,m.width,m.height,m.status
                FROM submission_pages p JOIN media_assets m ON m.id=p.media_id
                WHERE p.submission_id=:id ORDER BY p.position"""), {'id': created.id}).all()
            assert [tuple(page) for page in pages] == [(1, 100, 100, 'ready'), (2, 200, 100, 'ready')]
            reservation = db.execute(text("""SELECT pages,status,consumed_pages FROM quota_reservations
                WHERE submission_id=:id"""), {'id': created.id}).one()
            assert tuple(reservation) == (2, 'released', 0)
            account = db.execute(text("""SELECT reserved_pages,used_pages FROM quota_accounts
                WHERE tenant_id=:tenant"""), {'tenant': owner['tenant_id']}).one()
            assert tuple(account) == (0, 3)
            for action, resource in [('submission.created', created.id), ('job.cancelled', created.job_id)]:
                assert db.execute(text('SELECT count(*) FROM audit_events WHERE action=:action AND resource_id=:id'),
                                  {'action': action, 'id': resource}).scalar_one() == 1
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive(), 'Isolated API did not stop'
