"""Authorised single-page images against real PostgreSQL and PDFium."""
import io
from uuid import UUID, uuid4

import pytest
from PIL import Image
from sqlalchemy import text
from test_textbook_documents import approved as approved
from test_textbook_documents import private_storage as private_storage
from test_textbook_processing import received as received
from test_textbook_processing import submit
from test_textbook_processing import worker as worker
from test_textbook_rights import action
from test_textbook_rights import platform as platform
from test_textbook_rights import reviewer as reviewer

from wizzy.security import digest


@pytest.fixture
def source(client, received, worker):
    headers, edition, document, path = received
    job, _ = submit(client, received)
    assert worker.consume_one() == 'finished'
    return headers, edition, document, f"{path}/{job['id']}/pages"


def test_real_page_png_no_original_file_disclosure(client, source, private_storage):
    headers, _, _, path = source
    for number in (1, 2, 3):
        response = client.get(f'{path}/{number}/image', headers=headers)
        assert response.status_code == 200, response.text
        assert response.headers['content-type'] == 'image/png'
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['x-content-type-options'] == 'nosniff'
        with Image.open(io.BytesIO(response.content)) as image:
            assert max(image.size) <= 1600
    assert client.get(f'{path}/4/image', headers=headers).status_code == 404
    assert client.get(f'{path}/0/image', headers=headers).status_code == 422
    assert client.get(f'{path}/1/image').status_code == 401


def test_pending_task_and_wrong_binding_never_render(client, received, worker, monkeypatch):
    headers, _, _, path = received
    job, _ = submit(client, received)
    monkeypatch.setattr('wizzy.textbook_processing.render_page', lambda *a, **k: pytest.fail('must not render'))
    assert client.get(f"{path}/{job['id']}/pages/1/image", headers=headers).status_code == 404
    assert client.get(f"{path}/{uuid4()}/pages/1/image", headers=headers).status_code == 404
    # Do not leave a queued job ahead of the next test's source job.
    assert worker.consume_one() == 'finished'


def test_revocation_before_and_during_render_discards_pixels(client, source, monkeypatch):
    headers, edition, _, path = source
    from wizzy.textbook_processing import render_page

    def revoke(*args, **kwargs):
        content = render_page(*args, **kwargs)
        action(client, headers, edition, 'withdraw')
        return content

    monkeypatch.setattr('wizzy.textbook_processing.render_page', revoke)
    response = client.get(f'{path}/1/image', headers=headers)
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'draft_rights_unavailable'
    monkeypatch.setattr('wizzy.textbook_processing.render_page', lambda *a, **k: pytest.fail('must not render'))
    assert client.get(f'{path}/1/image', headers=headers).status_code == 409


def test_source_tampering_never_returns_image(client, source, app):
    headers, _, document, path = source
    source_file = app.state.settings.storage_root / 'textbook-quarantine' / f"{UUID(document['id']).hex}.pdf"
    source_file.write_bytes(source_file.read_bytes().replace(b'First source page', b'Wrong source page'))
    response = client.get(f'{path}/1/image', headers=headers)
    assert response.status_code == 409 and response.json()['error']['code'] == 'preview_source_mismatch'
    assert str(source_file) not in response.text


@pytest.mark.parametrize('when', ['before', 'during'])
@pytest.mark.parametrize('revocation,expected', [('session', 401), ('permission', 403)])
def test_session_and_permission_revocation_discards_pixels(client, source, admin_engine, monkeypatch, when, revocation, expected):
    headers, _, _, path = source
    from wizzy.textbook_processing import render_page

    def revoke():
        with admin_engine.begin() as db:
            if revocation == 'session':
                db.execute(text("UPDATE platform_sessions SET revoked_at=clock_timestamp() WHERE token_hash=:hash"),
                           {'hash': digest(headers['Authorization'].split()[1])})
            else:
                db.execute(text("DELETE FROM platform_role_permissions WHERE role='security_admin' AND permission='platform.textbooks.read'"))

    def render_and_revoke(*args, **kwargs):
        pixels = render_page(*args, **kwargs)
        revoke()
        return pixels

    try:
        if when == 'before':
            revoke()
            monkeypatch.setattr('wizzy.textbook_processing.render_page', lambda *a, **k: pytest.fail('must not render'))
        else:
            monkeypatch.setattr('wizzy.textbook_processing.render_page', render_and_revoke)
        result = client.get(f'{path}/1/image', headers=headers)
        assert result.status_code == expected
        assert result.headers['content-type'] == 'application/json'
    finally:
        if revocation == 'permission':
            with admin_engine.begin() as db:
                db.execute(text("INSERT INTO platform_role_permissions VALUES ('security_admin','platform.textbooks.read') ON CONFLICT DO NOTHING"))


def test_wrong_edition_and_document_binding_never_renders(client, source, monkeypatch):
    headers, edition, document, path = source
    monkeypatch.setattr('wizzy.textbook_processing.render_page', lambda *a, **k: pytest.fail('must not render'))
    for bound_id in (edition['id'], document['id']):
        wrong_path = path.replace(bound_id, str(uuid4()))
        assert client.get(f'{wrong_path}/1/image', headers=headers).status_code == 404
