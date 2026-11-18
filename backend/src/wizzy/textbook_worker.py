"""Consume one durable textbook job with a trusted, explicitly configured scanner."""
import json
from dataclasses import asdict
from uuid import UUID

from sqlalchemy import create_engine, text

from wizzy.config import Settings
from wizzy.execution import check_worker_role
from wizzy.pdf_processing import process_pdf
from wizzy.pdf_scan import ClamAVScanner

QUERY = text('SELECT public.textbook_processing_worker(:op,:job,:token,CAST(:result AS jsonb))')


class TextbookWorker:
    def __init__(self, engine, settings: Settings):
        if settings.clamav_host is None:
            raise ValueError('Configure a trusted ClamAV IP before consuming textbook jobs')
        self.scanner = ClamAVScanner(settings.clamav_host, settings.clamav_port,
                                     timeout=settings.clamav_timeout,
                                     database_timezone=settings.clamav_database_timezone)
        check_worker_role(engine)
        self.engine, self.storage = engine, settings.storage_root

    def _call(self, operation, *, job=None, token=None, result=None):
        with self.engine.begin() as db:
            return db.execute(QUERY, {'op': operation, 'job': job, 'token': token,
                                     'result': json.dumps(result) if result is not None else None}).scalar_one()

    def consume_one(self):
        claimed = self._call('claim')
        if claimed['status'] != 'claimed':
            return claimed['status']
        # No database connection or authorization lock is held during untrusted processing.
        document = UUID(claimed['document_id'])
        result = process_pdf(self.storage / 'textbook-quarantine' / f'{document.hex}.pdf',
                             size_bytes=claimed['size_bytes'], sha256=claimed['sha256'],
                             workspace=self.storage / 'textbook-processing', scanner=self.scanner)
        payload = asdict(result)
        if result.draft is not None:
            payload['draft']['page_count'] = len(result.draft.pages)
        return self._call('finish', job=UUID(claimed['id']), token=UUID(claimed['lease_token']),
                          result=payload)['status']


def main():
    engine = None
    try:
        settings = Settings()
        engine = create_engine(settings.worker_db_url, hide_parameters=True, pool_pre_ping=True,
                               connect_args={'connect_timeout': 3})
        status = TextbookWorker(engine, settings).consume_one()
    except Exception:
        # Exceptions can embed connection strings, PDF content or SQL parameters.
        raise SystemExit('Textbook worker unavailable; verify configuration and service health') from None
    finally:
        if engine is not None:
            engine.dispose()
    print(status)


if __name__ == '__main__':
    main()
