"""Disposable PDF parsing process; no DB, network client, OCR or publishing.

Invoked only after antivirus succeeds. It writes a bounded private JSON draft and
prints a fixed status code; it must never print PDF text or parser exceptions.
Process resource limits complement, but do not replace, a production OS sandbox.
"""
import json
import logging
import os
import resource
import sys
from pathlib import Path

MAX_PAGES = 500
MAX_CHARACTERS = 1_000_000
MAX_PAGE_CHARACTERS = 100_000
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_GRAPH_NODES = 100_000
REJECTION_CODES = frozenset({
    'invalid_pdf', 'encrypted_pdf', 'unsupported_pdf_features', 'page_limit_exceeded',
    'extraction_limit_exceeded', 'extraction_failed',
})


class PdfRejected(Exception):
    def __init__(self, code):
        self.code = code


def _limits():
    # Apply before importing a parser. Darwin rejects RLIMIT_AS below Python's
    # reserved virtual address space. Local macOS runs have no memory ceiling;
    # production workers require Linux and enforce the address-space limit.
    limits = [(resource.RLIMIT_CPU, 20), (resource.RLIMIT_FSIZE, MAX_RESULT_BYTES),
              (resource.RLIMIT_NOFILE, 64), (resource.RLIMIT_CORE, 0)]
    if sys.platform != 'darwin':
        limits.append((resource.RLIMIT_AS, 1024 ** 3))
    for kind, maximum in limits:
        _, hard = resource.getrlimit(kind)
        value = maximum if hard == resource.RLIM_INFINITY else min(maximum, hard)
        resource.setrlimit(kind, (value, value))


def _check_features(root):
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

    forbidden_keys = {'/JavaScript', '/JS', '/OpenAction', '/AA', '/EmbeddedFiles',
                      '/AF', '/EF', '/XFA', '/RichMediaContent', '/Collection'}
    forbidden_actions = {'/JavaScript', '/Launch', '/GoToR', '/GoToE', '/SubmitForm', '/ImportData',
                         '/Rendition', '/Movie', '/Sound'}
    seen, pending, visited = set(), [root], 0
    while pending:
        value = pending.pop()
        if isinstance(value, IndirectObject):
            identity = ('indirect', value.idnum, value.generation)
            if identity in seen:
                continue
            seen.add(identity)
            value = value.get_object()
        if isinstance(value, (DictionaryObject, ArrayObject)):
            identity = ('direct', id(value))
            if identity in seen:
                continue
            seen.add(identity)
        visited += 1
        if visited > MAX_GRAPH_NODES:
            raise PdfRejected('extraction_limit_exceeded')
        if isinstance(value, DictionaryObject):
            action = value.get('/S', '')
            if isinstance(action, IndirectObject):
                action = action.get_object()
            if forbidden_keys.intersection(value) or str(action) in forbidden_actions:
                raise PdfRejected('unsupported_pdf_features')
            pending.extend(value.values())
        elif isinstance(value, ArrayObject):
            pending.extend(value)


def extract(source: Path):
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        with source.open('rb') as stream:
            reader = PdfReader(stream, strict=True)
            if reader.is_encrypted:
                raise PdfRejected('encrypted_pdf')
            page_count = len(reader.pages)
            if not 1 <= page_count <= MAX_PAGES:
                raise PdfRejected('page_limit_exceeded')
            _check_features(reader.root_object)
            pages, total, empty = [], 0, []
            for number, page in enumerate(reader.pages, 1):
                text = (page.extract_text() or '').replace('\r\n', '\n').replace('\r', '\n')
                # Keep source page boundaries. Normalise C0 controls for safe text display.
                text = ''.join(char for char in text if char in '\n\t' or ord(char) >= 32)
                if len(text) > MAX_PAGE_CHARACTERS:
                    raise PdfRejected('extraction_limit_exceeded')
                total += len(text)
                if total > MAX_CHARACTERS:
                    raise PdfRejected('extraction_limit_exceeded')
                if not text.strip():
                    empty.append(number)
                pages.append({'number': number, 'text': text})
            return {'page_count': page_count, 'pages': pages, 'character_count': total,
                    'pages_without_text': empty, 'method': 'embedded_text',
                    'requires_review': True, 'ocr_performed': False, 'content_available': False}
    except PdfRejected:
        raise
    except (PdfReadError, ValueError, TypeError, KeyError, IndexError, RecursionError):
        raise PdfRejected('invalid_pdf') from None


def main():
    logging.disable(logging.CRITICAL)
    try:
        _limits()
        if len(sys.argv) != 3:
            return 2
        result = json.dumps(extract(Path(sys.argv[1])), ensure_ascii=True).encode('ascii')
        if len(result) > MAX_RESULT_BYTES:
            raise PdfRejected('extraction_limit_exceeded')
        descriptor = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as output:
            output.write(result)
            output.flush()
            os.fsync(output.fileno())
        print('ok')
        return 0
    except PdfRejected as exc:
        print(exc.code)
        return 1
    except MemoryError:
        print('extraction_limit_exceeded')
        return 1
    except Exception:
        # All diagnostics are fixed codes. No paths, credentials or PDF contents.
        print('extraction_failed')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
