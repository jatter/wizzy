"""Real isolated PDFium rendering and private-source integrity boundaries."""
import hashlib
import io
import stat
import subprocess
from types import SimpleNamespace

import pytest
from PIL import Image
from test_pdf_processing import pdf_bytes

from wizzy.pdf_preview import PreviewUnavailable, render_page


@pytest.fixture
def preview(tmp_path):
    content = pdf_bytes(('Source page', ''))
    source = tmp_path / 'source.pdf'
    source.write_bytes(content)
    workspace = tmp_path / 'preview'

    def run(**changes):
        args = dict(workspace=workspace, size_bytes=len(content),
                    sha256=hashlib.sha256(content).hexdigest(), page_number=1)
        try:
            return render_page(source, **(args | changes))
        finally:
            if workspace.exists():
                assert list(workspace.iterdir()) == []
                assert stat.S_IMODE(workspace.stat().st_mode) == 0o700
    return run, source


def test_real_render_page_is_bounded_png_with_visible_source(preview):
    run, _ = preview
    for number in (1, 2):
        content = run(page_number=number)
        with Image.open(io.BytesIO(content)) as image:
            assert image.format == 'PNG' and max(image.size) <= 1600
            grayscale = image.convert('L')
            assert (grayscale.getextrema()[0] < 200) == (number == 1)


def test_source_tampering_rejected_before_native_parser(preview, monkeypatch):
    run, source = preview
    source.write_bytes(source.read_bytes().replace(b'Source page', b'Hidden text'))
    monkeypatch.setattr('wizzy.pdf_preview.subprocess.run', lambda *a, **k: pytest.fail('must not render'))
    with pytest.raises(PreviewUnavailable) as exc:
        run()
    assert exc.value.code == 'preview_source_mismatch'


def test_symlink_source_rejected(preview):
    run, source = preview
    target = source.with_name('target.pdf')
    source.rename(target)
    source.symlink_to(target)
    with pytest.raises(PreviewUnavailable):
        run()


@pytest.mark.parametrize('failure', ['timeout', 'exit', 'missing', 'invalid', 'oversized'])
def test_subprocess_failure_is_private_and_cleans_workspace(preview, monkeypatch, failure):
    run, _ = preview

    def child(args, **kwargs):
        assert kwargs['env'] == {'LANG': 'C.UTF-8'}
        assert args[1:4] == ['-I', '-m', 'wizzy.pdf_render']
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(args, 30)
        if failure == 'invalid':
            from pathlib import Path
            Path(args[5]).write_bytes(b'private not png')
        if failure == 'oversized':
            Image.new('RGB', (1601, 2)).save(args[5], format='PNG')
        return SimpleNamespace(returncode=1 if failure == 'exit' else 0, stdout=b'ok\n')

    monkeypatch.setattr('wizzy.pdf_preview.subprocess.run', child)
    with pytest.raises(PreviewUnavailable):
        run()
    # The concurrency slot must also be released after failures.
    with pytest.raises(PreviewUnavailable):
        run()


def test_concurrent_renderer_limit(preview):
    from wizzy.pdf_preview import _slots
    run, _ = preview
    assert _slots.acquire(False) and _slots.acquire(False)
    try:
        with pytest.raises(PreviewUnavailable):
            run()
    finally:
        _slots.release()
        _slots.release()
