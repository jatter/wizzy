"""Bounded private snapshots and single-page image rendering, without DB access."""
import io
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from threading import BoundedSemaphore

from PIL import Image

from wizzy.pdf_extract import MAX_RESULT_BYTES
from wizzy.pdf_processing import InvalidSource, _snapshot
from wizzy.pdf_render import MAX_EDGE
from wizzy.pdf_scan import MAX_PDF_BYTES

_slots = BoundedSemaphore(2)


class PreviewUnavailable(Exception):
    def __init__(self, code='preview_unavailable'):
        self.code = code


def render_page(source: Path, *, workspace: Path, size_bytes: int, sha256: str,
                page_number: int) -> bytes:
    if (type(size_bytes) is not int or not 1 <= size_bytes <= MAX_PDF_BYTES
            or not isinstance(sha256, str) or re.fullmatch('[0-9a-f]{64}', sha256) is None
            or type(page_number) is not int or not 1 <= page_number <= 500):
        raise ValueError('Invalid preview metadata')
    if not _slots.acquire(blocking=False):
        raise PreviewUnavailable
    try:
        workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
        if workspace.is_symlink() or source.parent.is_symlink():
            raise PreviewUnavailable
        os.chmod(workspace, 0o700)
        with tempfile.TemporaryDirectory(prefix='preview-', dir=workspace) as directory:
            snapshot, result = Path(directory) / 'source.pdf', Path(directory) / 'page.png'
            try:
                _snapshot(source, snapshot, size_bytes, sha256)
            except InvalidSource:
                raise PreviewUnavailable('preview_source_mismatch') from None
            completed = subprocess.run(
                [sys.executable, '-I', '-m', 'wizzy.pdf_render', str(snapshot), str(result), str(page_number)],
                cwd=directory, env={'LANG': 'C.UTF-8'}, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, check=False,
            )
            if completed.returncode != 0 or completed.stdout != b'ok\n':
                raise PreviewUnavailable
            if result.is_symlink() or not result.is_file() or not 1 <= result.stat().st_size <= MAX_RESULT_BYTES:
                raise PreviewUnavailable
            content = result.read_bytes()
            with Image.open(io.BytesIO(content)) as image:
                if image.format != 'PNG' or not all(1 <= v <= MAX_EDGE for v in image.size):
                    raise PreviewUnavailable
                image.verify()
            return content
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise PreviewUnavailable from None
    finally:
        _slots.release()
