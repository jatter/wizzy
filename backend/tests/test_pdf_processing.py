"""Real PDF extraction process with explicit test-only antivirus injection."""
import hashlib
import json
import os
import stat
import subprocess
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    TextStringObject,
)

from wizzy.pdf_processing import _read_draft, process_pdf
from wizzy.pdf_scan import ScanEvidence, ScanUnavailable, UnsafeFile

SCAN = ScanEvidence('test-only-protocol-engine', 1, 'test-only', '2026-10-26T00:00:00+00:00')


def pdf_bytes(texts=('Learning fractions',), *, modify=None):
    import io
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=595, height=842)
        if text:
            font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                     NameObject('/Subtype'): NameObject('/Type1'),
                                     NameObject('/BaseFont'): NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({
                NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
            content = DecodedStreamObject()
            content.set_data(b'BT /F1 12 Tf 50 750 Td (' + text.encode('ascii') + b') Tj ET')
            page[NameObject('/Contents')] = writer._add_object(content)
    if modify:
        modify(writer)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class CleanForTest:
    def __init__(self):
        self.calls = []

    def scan(self, source):
        assert stat.S_IMODE(source.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(source.stat().st_mode) == 0o400
        self.calls.append(source.read_bytes())
        return SCAN


@pytest.fixture
def run_pdf(tmp_path):
    def run(content, scanner=None, **kwargs):
        source = tmp_path / 'quarantined.pdf'
        source.write_bytes(content)
        result = process_pdf(source, size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest(),
                             workspace=tmp_path / 'processing', scanner=scanner or CleanForTest(),
                             **kwargs)
        assert source.read_bytes() == content
        assert list((tmp_path / 'processing').iterdir()) == []
        assert stat.S_IMODE((tmp_path / 'processing').stat().st_mode) == 0o700
        return result
    return run


def test_real_pdf_text_and_source_page_boundaries(run_pdf):
    scanner = CleanForTest()
    content = pdf_bytes(('First source page', '', 'Third source page'))
    result = run_pdf(content, scanner)
    assert result.status == 'draft_ready' and result.code is None
    assert result.scan == SCAN
    assert scanner.calls == [content]
    assert [(page.number, page.text) for page in result.draft.pages] == [
        (1, 'First source page'), (2, ''), (3, 'Third source page')]
    assert result.draft.pages_without_text == (2,)
    assert result.draft.character_count == 34
    assert result.draft.method == 'embedded_text'
    assert result.draft.requires_review and not result.draft.ocr_performed
    assert result.content_available is False and result.draft.content_available is False


def test_empty_pages_are_not_claimed_as_ocr(run_pdf):
    result = run_pdf(pdf_bytes(('', '')))
    assert result.status == 'no_text'
    assert result.draft.pages_without_text == (1, 2)
    assert result.draft.character_count == 0
    assert result.draft.ocr_performed is False


@pytest.mark.parametrize('content', [b'%PDF-1.7\nnot a document\n%%EOF', b'not pdf'])
def test_real_parser_rejects_fake_pdf_despite_test_clean_verdict(run_pdf, content):
    result = run_pdf(content)
    assert result.status == 'rejected' and result.code == 'invalid_pdf'
    assert result.scan == SCAN and result.draft is None


@pytest.mark.parametrize('password', ['', 'private-password'])
def test_encrypted_even_empty_password_is_rejected(run_pdf, password):
    result = run_pdf(pdf_bytes(modify=lambda writer: writer.encrypt(password)))
    assert result.code == 'encrypted_pdf' and result.draft is None


@pytest.mark.parametrize('modify', [
    lambda writer: writer._root_object.update({NameObject('/Names'): DictionaryObject({
        NameObject('/JavaScript'): DictionaryObject({NameObject('/Names'): ArrayObject([
            TextStringObject('script'), DictionaryObject({NameObject('/S'): NameObject('/JavaScript'),
                NameObject('/JS'): TextStringObject('app.alert("private-script")')})])})})}),
    lambda writer: writer.add_attachment('payload.txt', b'private attachment'),
    lambda writer: writer._root_object.update({NameObject('/OpenAction'): ArrayObject()}),
    lambda writer: writer._root_object.update({NameObject('/XFA'): ArrayObject()}),
    lambda writer: writer._root_object.update({NameObject('/AA'): DictionaryObject()}),
    lambda writer: writer.pages[0].update({NameObject('/A'): DictionaryObject({
        NameObject('/S'): NameObject('/Launch')})}),
    lambda writer: writer.pages[0].update({NameObject('/A'): DictionaryObject({
        NameObject('/S'): writer._add_object(NameObject('/GoToE'))})}),
])
def test_active_or_embedded_features_are_rejected(run_pdf, modify):
    result = run_pdf(pdf_bytes(modify=modify))
    assert result.status == 'rejected' and result.code == 'unsupported_pdf_features'
    assert result.draft is None


def test_page_limit_and_zero_pages(run_pdf):
    for texts in [('',) * 501, ()]:
        result = run_pdf(pdf_bytes(texts))
        assert result.code == 'page_limit_exceeded' and result.draft is None


def test_per_page_and_document_character_limit(run_pdf):
    for texts in [('x' * 100_001,), ('x' * 91_000,) * 11]:
        result = run_pdf(pdf_bytes(texts))
        assert result.code == 'extraction_limit_exceeded' and result.draft is None


@pytest.mark.parametrize(('exception', 'status', 'code'), [
    (ScanUnavailable, 'unavailable', 'scanner_unavailable'),
    (UnsafeFile, 'rejected', 'scanner_rejected'),
])
def test_scanner_failure_never_starts_extraction(run_pdf, monkeypatch, exception, status, code):
    from wizzy import pdf_processing

    class Failing:
        def scan(self, source):
            raise exception('sensitive daemon detail')

    monkeypatch.setattr(pdf_processing, '_extract', lambda *args: pytest.fail('must not extract'))
    result = run_pdf(pdf_bytes(), Failing())
    assert (result.status, result.code) == (status, code)
    assert result.scan is None and result.draft is None
    assert 'sensitive' not in repr(result)


def test_source_mismatch_never_scans(tmp_path):
    source, workspace = tmp_path / 'source.pdf', tmp_path / 'processing'
    content = pdf_bytes()
    source.write_bytes(content)
    scanner = CleanForTest()
    for size, digest in [(len(content) + 1, hashlib.sha256(content).hexdigest()),
                         (len(content), '0' * 64)]:
        result = process_pdf(source, size_bytes=size, sha256=digest, workspace=workspace,
                             scanner=scanner)
        assert result.code == 'source_mismatch'
        assert result.draft is None and list(workspace.iterdir()) == []
    assert scanner.calls == []


@pytest.mark.parametrize('kind', ['absent', 'symlink', 'directory', 'fifo'])
def test_no_follow_regular_file_requirement(tmp_path, kind):
    source, workspace = tmp_path / 'source.pdf', tmp_path / 'processing'
    if kind == 'symlink':
        target = tmp_path / 'target.pdf'
        target.write_bytes(b'%PDF-')
        source.symlink_to(target)
    elif kind == 'directory':
        source.mkdir()
    elif kind == 'fifo':
        os.mkfifo(source)
    scanner = CleanForTest()
    result = process_pdf(source, size_bytes=5, sha256='0' * 64, workspace=workspace, scanner=scanner)
    assert result.status in ('rejected', 'unavailable')
    assert result.draft is None and scanner.calls == [] and list(workspace.iterdir()) == []


@pytest.mark.parametrize(('size', 'digest'), [
    (0, '0' * 64), (50 * 1024 * 1024 + 1, '0' * 64), (True, '0' * 64),
    (5, 'F' * 64), (5, '0' * 63), (5, '../source.pdf'), (5, None),
])
def test_metadata_rejected_before_access(tmp_path, size, digest):
    with pytest.raises(ValueError):
        process_pdf(tmp_path / 'absent.pdf', size_bytes=size, sha256=digest,
                    workspace=tmp_path / 'processing', scanner=CleanForTest())
    assert not (tmp_path / 'processing').exists()


def test_workspace_symlink_is_refused(tmp_path):
    target = tmp_path / 'target'
    target.mkdir(mode=0o755)
    workspace = tmp_path / 'workspace'
    workspace.symlink_to(target)
    result = process_pdf(tmp_path / 'absent', size_bytes=5, sha256='0' * 64,
                         workspace=workspace, scanner=CleanForTest())
    assert result.code == 'storage_unavailable'
    assert stat.S_IMODE(target.stat().st_mode) == 0o755


def test_child_environment_and_timeout_cleanup(run_pdf, monkeypatch):
    from wizzy import pdf_processing

    def expired(command, **kwargs):
        assert command[1:4] == ['-I', '-m', 'wizzy.pdf_extract']
        assert kwargs['timeout'] == 40 and kwargs['env'] == {'LANG': 'C.UTF-8'}
        assert 'POSTGRES_PASSWORD' not in kwargs['env']
        raise subprocess.TimeoutExpired(command, 40, output=b'private PDF content')

    monkeypatch.setattr(pdf_processing.subprocess, 'run', expired)
    result = run_pdf(pdf_bytes())
    assert result.code == 'extraction_timeout' and result.scan == SCAN and result.draft is None
    assert 'private PDF content' not in repr(result)


@pytest.mark.parametrize(('returncode', 'stdout', 'code'), [
    (-9, b'', 'extraction_failed'), (0, b'private text', 'extraction_failed'),
    (1, b'private traceback', 'extraction_failed'), (1, b'extraction_failed\n', 'extraction_failed'),
    (1, b'invalid_pdf\n', 'invalid_pdf'), (0, b'ok\n', 'extraction_failed'),
])
def test_child_failure_is_sanitized(run_pdf, monkeypatch, returncode, stdout, code):
    from wizzy import pdf_processing
    monkeypatch.setattr(pdf_processing.subprocess, 'run', lambda *args, **kwargs:
                        SimpleNamespace(returncode=returncode, stdout=stdout))
    result = run_pdf(pdf_bytes())
    assert result.code == code and result.draft is None
    assert 'private' not in repr(result)


def test_invalid_scanner_return_fails_closed(run_pdf, monkeypatch):
    scanner = CleanForTest()
    scanner.scan = lambda source: 'clean'
    result = run_pdf(pdf_bytes(), scanner)
    assert result.code == 'scanner_unavailable' and result.draft is None


def test_projection_rejects_forged_draft(tmp_path):
    valid = {'page_count': 1, 'pages': [{'number': 1, 'text': 'abc'}], 'character_count': 3,
             'pages_without_text': [], 'method': 'embedded_text', 'requires_review': True,
             'ocr_performed': False, 'content_available': False}
    path = tmp_path / 'draft.json'
    path.write_text(json.dumps(valid))
    assert _read_draft(path).pages[0].text == 'abc'
    for change in [
        {'content_available': True}, {'requires_review': False}, {'ocr_performed': True},
        {'page_count': True}, {'character_count': True}, {'character_count': 2},
        {'pages_without_text': [1]}, {'method': 'fake_ocr'}, {'extra': 'not projected'},
        {'pages': [{'number': True, 'text': 'abc'}]},
        {'pages': [{'number': 2, 'text': 'abc'}]}, {'pages': [{'number': 1, 'text': 7}]},
        {'pages': [{'number': 1, 'text': 'a\x00b'}]},
        {'pages': [{'number': 1, 'text': 'a\ud800b'}]},
    ]:
        path.write_text(json.dumps(valid | change))
        with pytest.raises(ValueError):
            _read_draft(path)


@pytest.mark.parametrize('platform', ['linux', 'darwin'])
def test_parser_resource_limits_are_platform_explicit(monkeypatch, platform):
    from wizzy import pdf_extract

    applied = {}
    monkeypatch.setattr(pdf_extract.sys, 'platform', platform)
    monkeypatch.setattr(pdf_extract.resource, 'getrlimit',
                        lambda kind: (pdf_extract.resource.RLIM_INFINITY,) * 2)
    monkeypatch.setattr(pdf_extract.resource, 'setrlimit',
                        lambda kind, limits: applied.update({kind: limits}))
    pdf_extract._limits()
    assert applied[pdf_extract.resource.RLIMIT_CPU] == (20, 20)
    assert applied[pdf_extract.resource.RLIMIT_FSIZE] == (8 * 1024 ** 2,) * 2
    assert applied[pdf_extract.resource.RLIMIT_NOFILE] == (64, 64)
    assert applied[pdf_extract.resource.RLIMIT_CORE] == (0, 0)
    if platform == 'linux':
        assert applied[pdf_extract.resource.RLIMIT_AS] == (1024 ** 3,) * 2
    else:
        assert pdf_extract.resource.RLIMIT_AS not in applied


def test_parser_does_not_ignore_resource_limit_failures(monkeypatch):
    from wizzy import pdf_extract

    def refused(kind, limits):
        raise ValueError('cannot enforce resource limit')

    monkeypatch.setattr(pdf_extract.resource, 'setrlimit', refused)
    with pytest.raises(ValueError):
        pdf_extract._limits()
