"""Clamd protocol tests use a local test server, not a virus scanning engine."""
import contextlib
import hashlib
import socket
import struct
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from wizzy.pdf_scan import ClamAVScanner, ScanUnavailable, UnsafeFile

VERSION = b'ClamAV 1.4.3/27700/Mon Oct 26 05:00:00 2026\0'
PUBLISHED = datetime(2026, 10, 26, 5, tzinfo=UTC)


@pytest.fixture(autouse=True)
def scanner_clock(monkeypatch):
    from wizzy import pdf_scan
    current = [PUBLISHED + timedelta(hours=1)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0].astimezone(tz)

    monkeypatch.setattr(pdf_scan, 'datetime', Clock)
    return lambda value: current.__setitem__(0, value)


def read_exact(connection, size):
    value = bytearray()
    while len(value) < size:
        chunk = connection.recv(size - len(value))
        if not chunk:
            raise ValueError('Unexpected end of protocol test stream')
        value.extend(chunk)
    return bytes(value)


def read_command(connection):
    value = bytearray()
    while not value.endswith(b'\0'):
        value.extend(read_exact(connection, 1))
    return bytes(value)


@contextlib.contextmanager
def daemon(*, verdict=b'stream: OK\0', version=VERSION, after=VERSION,
           version_only=False, fragment=False, database_timezone='UTC'):
    received = []
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(3)
    listener.settimeout(3)

    def serve():
        with listener:
            for command, reply in [(b'zVERSION\0', version)] + ([] if version_only else [
                    (b'zINSTREAM\0', verdict), (b'zVERSION\0', after)]):
                # Non-clean verdicts never trigger the final VERSION request.
                if command == b'zVERSION\0' and received and verdict != b'stream: OK\0':
                    break
                with listener.accept()[0] as connection:
                    connection.settimeout(3)
                    assert read_command(connection) == command
                    if command == b'zINSTREAM\0':
                        stream = bytearray()
                        while size := struct.unpack('!I', read_exact(connection, 4))[0]:
                            assert size <= 64 * 1024
                            stream.extend(read_exact(connection, size))
                        received.append(bytes(stream))
                    if fragment:
                        for character in reply:
                            connection.sendall(bytes([character]))
                    else:
                        connection.sendall(reply)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(serve)
        try:
            yield ClamAVScanner('127.0.0.1', listener.getsockname()[1], timeout=2,
                                database_timezone=database_timezone), received
        finally:
            future.result(timeout=4)


def test_stream_protocol_and_evidence(tmp_path):
    source = tmp_path / 'private.pdf'
    content = b'%PDF-' + b'x' * (160 * 1024)
    source.write_bytes(content)
    with daemon(fragment=True) as (scanner, received):
        result = scanner.scan(source)
    assert received == [content]
    assert result.engine == 'ClamAV 1.4.3'
    assert result.database_revision == 27700
    assert result.database_date == '2026-10-26T05:00:00+00:00'
    assert result.checked_at.endswith('+00:00')


@pytest.mark.parametrize('verdict', [
    b'stream: Eicar-Signature FOUND\0',
    b'stream: Heuristics.Limits.Exceeded.MaxFileSize FOUND\0',
])
def test_detected_or_limit_rejection(tmp_path, verdict):
    source = tmp_path / 'source.pdf'
    source.write_bytes(b'%PDF-test')
    with daemon(verdict=verdict) as (scanner, _), pytest.raises(UnsafeFile):
        scanner.scan(source)


@pytest.mark.parametrize('verdict', [
    b'stream: OK', b'OK\0', b'stream: OK\n', b'stream: OK\0extra',
    b'INSTREAM size limit exceeded. ERROR\0', b'stream: read error ERROR\0',
    b'x' * 4097, b'x' * 4096 + b'\0', b'stream: OK\xff\0', b'stream: OK\0\0', b'',
])
def test_only_exact_clean_reply_is_accepted(tmp_path, verdict):
    source = tmp_path / 'source.pdf'
    source.write_bytes(b'%PDF-test')
    with daemon(verdict=verdict) as (scanner, _), pytest.raises(ScanUnavailable):
        scanner.scan(source)


@pytest.mark.parametrize('version', [
    b'ClamAV 1.4.3\0', b'COMMAND UNAVAILABLE\0', b'ClamAV 1.4.3/0/no db\0',
    b'ClamAV x/27700/date\0', b'ClamAV 1.4.3/27700/date\nunsafe\0',
])
def test_missing_version_stops_before_scan(tmp_path, version):
    with daemon(version=version, version_only=True) as (scanner, received):
        with pytest.raises(ScanUnavailable):
            scanner.scan(tmp_path / 'absent.pdf')
    assert received == []


def test_database_reload_invalidates_clean_receipt(tmp_path):
    source = tmp_path / 'source.pdf'
    source.write_bytes(b'%PDF-test')
    with daemon(after=VERSION.replace(b'27700', b'27701')) as (scanner, _):
        with pytest.raises(ScanUnavailable):
            scanner.scan(source)


def test_connection_unavailable_is_sanitized(tmp_path):
    with socket.socket() as connection:
        connection.bind(('127.0.0.1', 0))
        port = connection.getsockname()[1]
    with pytest.raises(ScanUnavailable) as error:
        ClamAVScanner('127.0.0.1', port, timeout=.1).scan(tmp_path / 'source.pdf')
    assert str(error.value) == ''


def test_absolute_deadline_stops_read(monkeypatch):
    from wizzy import pdf_scan
    ticks = iter([1, 1, 1, 1.5, 2.1])
    monkeypatch.setattr(pdf_scan.time, 'monotonic', lambda: next(ticks))

    class Stalled:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def settimeout(self, value):
            assert value <= 1

        def sendall(self, value):
            pass

        def recv(self, size):
            return b'C'

    monkeypatch.setattr(pdf_scan.socket, 'create_connection', lambda *args, **kwargs: Stalled())
    with pytest.raises(ScanUnavailable):
        ClamAVScanner('127.0.0.1', timeout=1).scan(None)


@pytest.mark.parametrize(('host', 'port', 'timeout'), [
    ('localhost', 3310, 30), ('https://bad.test', 3310, 30), ('127.0.0.1', 0, 30),
    ('127.0.0.1', True, 30), ('127.0.0.1', 3310, 0), ('127.0.0.1', 3310, 121),
    ('127.0.0.1', 3310, True), ('127.0.0.1', 3310, float('nan')),
])
def test_scanner_configuration_is_bounded(host, port, timeout):
    with pytest.raises(ValueError):
        ClamAVScanner(host, port, timeout=timeout)


@pytest.mark.parametrize(('age_seconds', 'accepted'), [
    (72 * 3600, True), (72 * 3600 + 1, False), (-300, True), (-301, False),
])
def test_database_age_and_future_boundaries(tmp_path, scanner_clock, age_seconds, accepted):
    scanner_clock(PUBLISHED + timedelta(seconds=age_seconds))
    source = tmp_path / 'source.pdf'
    source.write_bytes(b'%PDF-test')
    with daemon(version_only=not accepted) as (scanner, received):
        if accepted:
            assert scanner.scan(source).database_revision == 27700
        else:
            with pytest.raises(ScanUnavailable):
                scanner.scan(source)
            assert received == []


@pytest.mark.parametrize('date', [
    'Sun Oct 26 05:00:00 2026', 'Mon Xxx 26 05:00:00 2026',
    'Sun Feb 29 05:00:00 2026', 'Mon Oct 26 25:00:00 2026',
    'Mon Oct 26 05:00:00 2026 UTC', '2026-10-26T05:00:00',
    'Mon Oct 26 05:00:00 0000',
])
def test_invalid_database_dates_stop_before_stream(tmp_path, date):
    version = f'ClamAV 1.4.3/27700/{date}\0'.encode('ascii')
    with daemon(version=version, version_only=True) as (scanner, received):
        with pytest.raises(ScanUnavailable):
            scanner.scan(tmp_path / 'absent.pdf')
        assert received == []


def test_daemon_timezone_is_explicit_and_evidence_uses_utc(tmp_path):
    version = VERSION.replace(b'05:00:00', b'13:00:00')
    source = tmp_path / 'source.pdf'
    source.write_bytes(b'%PDF-test')
    with daemon(version=version, after=version, database_timezone='Asia/Shanghai') as (scanner, _):
        assert scanner.scan(source).database_date == PUBLISHED.isoformat()


@pytest.mark.parametrize(('date', 'now'), [
    ('Sun Nov  1 01:30:00 2026', datetime(2026, 11, 1, 8, tzinfo=UTC)),
    ('Sun Mar  8 02:30:00 2026', datetime(2026, 3, 8, 8, tzinfo=UTC)),
])
def test_ambiguous_or_nonexistent_daemon_times_fail_closed(tmp_path, scanner_clock, date, now):
    scanner_clock(now)
    version = f'ClamAV 1.4.3/27700/{date}\0'.encode('ascii')
    with daemon(version=version, version_only=True, database_timezone='America/New_York') as (scanner, received):
        with pytest.raises(ScanUnavailable):
            scanner.scan(tmp_path / 'absent.pdf')
        assert received == []


@pytest.mark.parametrize('timezone', ['No/Such_Zone', '', '../UTC', None, 1])
def test_unknown_daemon_timezone_is_rejected(timezone):
    with pytest.raises(ValueError, match='timezone'):
        ClamAVScanner('127.0.0.1', database_timezone=timezone)


def test_database_aging_during_scan_invalidates_clean_receipt(tmp_path, monkeypatch):
    from wizzy import pdf_scan
    times = iter([PUBLISHED + timedelta(hours=72), PUBLISHED + timedelta(hours=72, seconds=1)])

    class AdvancingClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return next(times).astimezone(tz)

    monkeypatch.setattr(pdf_scan, 'datetime', AdvancingClock)
    source = tmp_path / 'source.pdf'
    source.write_bytes(b'%PDF-test')
    with daemon() as (scanner, received), pytest.raises(ScanUnavailable):
        scanner.scan(source)
    assert received == [b'%PDF-test']


def test_stale_database_never_starts_extraction(tmp_path, scanner_clock, monkeypatch):
    from wizzy import pdf_processing
    scanner_clock(PUBLISHED + timedelta(hours=73))
    source = tmp_path / 'source.pdf'
    content = b'%PDF-test'
    source.write_bytes(content)
    monkeypatch.setattr(pdf_processing, '_extract', lambda *args: pytest.fail('Extraction must not run'))
    workspace = tmp_path / 'processing'
    with daemon(version_only=True) as (scanner, received):
        result = pdf_processing.process_pdf(source, size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(), workspace=workspace, scanner=scanner)
    assert result.status == 'unavailable' and result.code == 'scanner_unavailable'
    assert result.scan is None and result.draft is None and received == []
    assert list(workspace.iterdir()) == [] and source.read_bytes() == content
