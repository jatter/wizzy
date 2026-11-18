"""Disposable single-page renderer; invoked only for an authorised scanned source."""
import logging
import math
import sys
from contextlib import closing
from pathlib import Path

from wizzy.pdf_extract import MAX_PAGES, _limits

MAX_EDGE = 1600


def render(source, destination, number):
    import pypdfium2 as pdfium

    # No form environment or JavaScript platform is initialised. Each invocation
    # owns its PDFium instance in a separate, resource-limited process.
    with pdfium.PdfDocument(source) as document:
        if not 1 <= len(document) <= MAX_PAGES or not 1 <= number <= len(document):
            raise ValueError
        with closing(document[number - 1]) as page:
            width, height = page.get_size()
            if not all(math.isfinite(v) and v > 0 for v in (width, height)):
                raise ValueError
            scale = min(1.5, (MAX_EDGE - 1) / max(width, height))
            with closing(page.render(scale=scale, draw_annots=False)) as bitmap:
                image = bitmap.to_pil()
                try:
                    if not all(1 <= v <= MAX_EDGE for v in image.size):
                        raise ValueError
                    image.save(destination, format='PNG')
                finally:
                    image.close()


def main():
    logging.disable(logging.CRITICAL)
    try:
        _limits()
        if len(sys.argv) != 4:
            return 2
        render(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]))
        print('ok')
        return 0
    except Exception:
        # No source data, filesystem paths or renderer diagnostics reach callers.
        print('preview_failed')
        return 1


if __name__ == '__main__':
    sys.exit(main())
