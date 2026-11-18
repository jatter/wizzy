"""Private, bounded PDF preflight for durable textbook processing jobs.

This component does not change a receipt's state or make any content available.
It requires an explicitly configured antivirus; there is no clean/mock fallback.
"""
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from wizzy.pdf_extract import (
    MAX_CHARACTERS,
    MAX_PAGE_CHARACTERS,
    MAX_PAGES,
    MAX_RESULT_BYTES,
    REJECTION_CODES,
)
from wizzy.pdf_scan import MAX_PDF_BYTES, ClamAVScanner, ScanEvidence, ScanUnavailable, UnsafeFile


@dataclass(frozen=True)
class ExtractedPage:
    number: int
    text: str


@dataclass(frozen=True)
class TextDraft:
    pages: tuple[ExtractedPage, ...]
    pages_without_text: tuple[int, ...]
    character_count: int
    method: str = 'embedded_text'
    requires_review: bool = True
    ocr_performed: bool = False
    content_available: bool = False


@dataclass(frozen=True)
class ProcessingResult:
    status: str
    code: str | None = None
    scan: ScanEvidence | None = None
    draft: TextDraft | None = None
    content_available: bool = False


class InvalidSource(Exception):
    pass


def _snapshot(source, destination, size_bytes, sha256):
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != size_bytes:
            raise InvalidSource from None
        checksum, size = hashlib.sha256(), 0
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as target:
            while chunk := stream.read(64 * 1024):
                size += len(chunk)
                if size > size_bytes:
                    raise InvalidSource from None
                checksum.update(chunk)
                target.write(chunk)
        if size != size_bytes or checksum.hexdigest() != sha256:
            raise InvalidSource from None
    os.chmod(destination, 0o400)


def _read_draft(result):
    if result.is_symlink() or not result.is_file() or result.stat().st_size > MAX_RESULT_BYTES:
        raise ValueError
    value = json.loads(result.read_bytes())
    keys = {'page_count', 'pages', 'character_count', 'pages_without_text', 'method',
            'requires_review', 'ocr_performed', 'content_available'}
    if (not isinstance(value, dict) or set(value) != keys or value['method'] != 'embedded_text'
            or value['requires_review'] is not True or value['ocr_performed'] is not False
            or value['content_available'] is not False or type(value['page_count']) is not int
            or not 1 <= value['page_count'] <= MAX_PAGES
            or not isinstance(value['pages'], list) or len(value['pages']) != value['page_count']):
        raise ValueError
    pages, empty = [], []
    for number, page in enumerate(value['pages'], 1):
        if (not isinstance(page, dict) or set(page) != {'number', 'text'}
                or type(page['number']) is not int or page['number'] != number
                or not isinstance(page['text'], str) or len(page['text']) > MAX_PAGE_CHARACTERS
                or '\x00' in page['text'] or any(0xD800 <= ord(c) <= 0xDFFF for c in page['text'])):
            raise ValueError
        pages.append(ExtractedPage(number, page['text']))
        if not page['text'].strip():
            empty.append(number)
    count = sum(len(page.text) for page in pages)
    if (count > MAX_CHARACTERS or type(value['character_count']) is not int
            or value['character_count'] != count or not isinstance(value['pages_without_text'], list)
            or any(type(number) is not int for number in value['pages_without_text'])
            or value['pages_without_text'] != empty):
        raise ValueError
    return TextDraft(tuple(pages), tuple(empty), count)


def _extract(snapshot, result, scan):
    try:
        # The child receives no application credentials or database environment.
        completed = subprocess.run(
            [sys.executable, '-I', '-m', 'wizzy.pdf_extract', str(snapshot), str(result)],
            cwd=snapshot.parent, env={'LANG': 'C.UTF-8'}, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=40, check=False,
        )
    except subprocess.TimeoutExpired:
        return ProcessingResult('unavailable', 'extraction_timeout', scan)
    except OSError:
        return ProcessingResult('unavailable', 'extraction_unavailable', scan)
    if completed.returncode != 0:
        code = completed.stdout.decode('ascii', errors='replace').strip()
        if code in REJECTION_CODES:
            return ProcessingResult('rejected' if code != 'extraction_failed' else 'unavailable',
                                    code, scan)
        return ProcessingResult('unavailable', 'extraction_failed', scan)
    if completed.stdout != b'ok\n':
        return ProcessingResult('unavailable', 'extraction_failed', scan)
    try:
        draft = _read_draft(result)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return ProcessingResult('unavailable', 'extraction_failed', scan)
    status = 'draft_ready' if any(page.text.strip() for page in draft.pages) else 'no_text'
    return ProcessingResult(status, scan=scan, draft=draft)


def process_pdf(source: Path, *, size_bytes: int, sha256: str, workspace: Path,
                scanner: ClamAVScanner) -> ProcessingResult:
    """Scan and extract a verified private copy; caller must separately authorise.

    No caller-supplied filename is used for a new file. All transient PDF/text
    files are removed on success and failure; the quarantined source is preserved.
    Results are only in memory. Durable jobs, authorisation rechecks, revision
    binding and draft persistence belong to the durable textbook worker; approval
    remains outside this component.
    """
    if (type(size_bytes) is not int or not 1 <= size_bytes <= MAX_PDF_BYTES
            or not isinstance(sha256, str) or re.fullmatch('[0-9a-f]{64}', sha256) is None):
        raise ValueError('Invalid source metadata')
    try:
        workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
        if workspace.is_symlink():
            return ProcessingResult('unavailable', 'storage_unavailable')
        os.chmod(workspace, 0o700)
        with tempfile.TemporaryDirectory(prefix='pdf-', dir=workspace) as directory:
            private = Path(directory)
            snapshot, result = private / 'source.pdf', private / 'draft.json'
            try:
                _snapshot(source, snapshot, size_bytes, sha256)
            except InvalidSource:
                return ProcessingResult('rejected', 'source_mismatch')
            except OSError:
                return ProcessingResult('unavailable', 'source_unavailable')
            try:
                evidence = scanner.scan(snapshot)
            except UnsafeFile:
                return ProcessingResult('rejected', 'scanner_rejected')
            except ScanUnavailable:
                return ProcessingResult('unavailable', 'scanner_unavailable')
            if not isinstance(evidence, ScanEvidence):
                return ProcessingResult('unavailable', 'scanner_unavailable')
            return _extract(snapshot, result, evidence)
    except OSError:
        return ProcessingResult('unavailable', 'storage_unavailable')
