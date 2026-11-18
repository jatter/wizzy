"""Bounded ClamAV INSTREAM client. Only an exact clean reply permits extraction.

Protocol reference: Cisco-Talos/clamav docs/man/clamd.8.in (VERSION, INSTREAM).
The endpoint must be a trusted private daemon; the protocol has no authentication.
"""
import ipaddress
import re
import socket
import struct
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_DATABASE_AGE = timedelta(hours=72)
MAX_CLOCK_SKEW = timedelta(minutes=5)
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
WEEKDAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')


class ScanUnavailable(Exception):
    """No reliable clean verdict; raw daemon errors must not escape to logs/UI."""


class UnsafeFile(Exception):
    """The daemon rejected the stream (includes detection and configured limits)."""


@dataclass(frozen=True)
class ScanEvidence:
    engine: str
    database_revision: int
    database_date: str
    checked_at: str


class ClamAVScanner:
    def __init__(self, host: str, port: int = 3310, *, timeout: float = 30,
                 database_timezone: str = 'UTC'):
        try:
            ipaddress.ip_address(host)
        except (ValueError, TypeError):
            raise ValueError('Scanner requires a trusted IP address') from None
        if type(port) is not int or not 1 <= port <= 65535 or type(timeout) not in (int, float) or not 0 < timeout <= 120:
            raise ValueError('Invalid scanner configuration')
        try:
            self.database_timezone = ZoneInfo(database_timezone)
        except (ValueError, TypeError, ZoneInfoNotFoundError):
            raise ValueError('Scanner requires a valid database timezone') from None
        self.host, self.port, self.timeout = host, port, timeout

    @staticmethod
    def _remaining(connection, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ScanUnavailable from None
        connection.settimeout(remaining)

    def _reply(self, connection, deadline):
        result = bytearray()
        while len(result) <= 4096:
            self._remaining(connection, deadline)
            chunk = connection.recv(min(4097 - len(result), 1024))
            if not chunk:
                raise ScanUnavailable from None
            result.extend(chunk)
            if len(result) > 4096:
                raise ScanUnavailable from None
            if b'\0' in chunk:
                if not result.endswith(b'\0') or result.count(b'\0') != 1:
                    raise ScanUnavailable from None
                try:
                    return bytes(result[:-1]).decode('ascii')
                except UnicodeDecodeError:
                    raise ScanUnavailable from None
        raise ScanUnavailable from None

    def _connect(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ScanUnavailable from None
        return socket.create_connection((self.host, self.port), timeout=remaining)

    def _database_time(self, value):
        # Clamd's ctime value has no offset. Interpret it in the configured daemon
        # timezone without relying on this process's locale or local timezone.
        match = re.fullmatch(r'([A-Za-z]{3}) ([A-Za-z]{3}) ([ 0-9][0-9]) ([0-9]{2}):([0-9]{2}):([0-9]{2}) ([0-9]{4})', value)
        if match is None:
            raise ScanUnavailable from None
        weekday, month, day, hour, minute, second, year = match.groups()
        try:
            naive = datetime(int(year), MONTHS.index(month) + 1, int(day),
                             int(hour), int(minute), int(second))
        except ValueError:
            raise ScanUnavailable from None
        local = naive.replace(tzinfo=self.database_timezone)
        if (WEEKDAYS[naive.weekday()] != weekday
                or local.utcoffset() != local.replace(fold=1).utcoffset()):
            # Reject ambiguous and nonexistent DST times rather than guess.
            raise ScanUnavailable from None
        timestamp = local.astimezone(UTC)
        age = datetime.now(UTC) - timestamp
        if not -MAX_CLOCK_SKEW <= age <= MAX_DATABASE_AGE:
            raise ScanUnavailable from None
        return timestamp

    def _version(self, deadline):
        with self._connect(deadline) as connection:
            self._remaining(connection, deadline)
            connection.sendall(b'zVERSION\0')
            version = self._reply(connection, deadline)
        match = re.fullmatch(r'ClamAV ([0-9][0-9A-Za-z.+_-]{0,63})/([1-9][0-9]{0,11})/([^\x00-\x1f\x7f]{1,100})', version)
        if match is None:
            raise ScanUnavailable from None
        version = match.groups()
        self._database_time(version[2])
        return version

    def scan(self, source: Path) -> ScanEvidence:
        deadline = time.monotonic() + self.timeout
        try:
            before = self._version(deadline)
            with self._connect(deadline) as connection, source.open('rb') as stream:
                self._remaining(connection, deadline)
                connection.sendall(b'zINSTREAM\0')
                size = 0
                while chunk := stream.read(64 * 1024):
                    size += len(chunk)
                    if size > MAX_PDF_BYTES:
                        raise ScanUnavailable from None
                    self._remaining(connection, deadline)
                    connection.sendall(struct.pack('!I', len(chunk)) + chunk)
                self._remaining(connection, deadline)
                connection.sendall(struct.pack('!I', 0))
                verdict = self._reply(connection, deadline)
            if re.fullmatch(r'stream: .+ FOUND', verdict):
                raise UnsafeFile from None
            if verdict != 'stream: OK':
                raise ScanUnavailable from None
            # A concurrent database reload makes this receipt ambiguous; retry safely.
            if self._version(deadline) != before:
                raise ScanUnavailable from None
            return ScanEvidence(f'ClamAV {before[0]}', int(before[1]),
                                self._database_time(before[2]).isoformat(),
                                datetime.now(UTC).isoformat())
        except (OSError, ValueError, OverflowError):
            raise ScanUnavailable from None
