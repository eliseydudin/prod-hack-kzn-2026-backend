import asyncio
import socket
import ssl
import subprocess
import sys
from contextlib import closing, suppress
from dataclasses import dataclass
from html.parser import HTMLParser
from http.client import HTTPException, HTTPSConnection
from ipaddress import IPv6Address, ip_address, ip_network
from pathlib import Path
from re import sub
from threading import BoundedSemaphore, Timer
from time import monotonic
from urllib.parse import quote, urljoin, urlsplit

from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

_MAX_DOCUMENT_BYTES = 900 * 1024
_MAX_TEXT_LENGTH = 64 * 1024
_MAX_URL_LENGTH = 4096
_URL_TIMEOUT = 8
_CONNECT_TIMEOUT = 3
_MAX_REDIRECTS = 3
_PDF_TIMEOUT = 5
_PDF_WORKER = Path(__file__).with_name("pdf_text.py")
_PDF_FAILURES = {
    2: "ticket_pdf_encrypted",
    3: "ticket_document_has_no_text",
    4: "ticket_pdf_limit",
}
_PDF_SLOTS = BoundedSemaphore(2)
_TLS_CONTEXT = ssl.create_default_context()
_NAT64_PREFIXES = (ip_network("64:ff9b::/96"), ip_network("64:ff9b:1::/48"))
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_HTML_TYPES = {"text/html", "application/xhtml+xml"}
_HIDDEN_TAGS = {"head", "script", "style", "template", "noscript", "svg"}
_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_BLOCK_TAGS = {
    "address",
    "article",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "header",
    "hr",
    "li",
    "main",
    "p",
    "section",
    "tr",
}


class TicketSourceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _Document:
    data: bytes
    media_type: str
    encoding: str | None


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.hidden_tag: str | None = None
        self.hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        if self.hidden_tag is not None:
            if self.hidden_tag == tag:
                self.hidden_depth += 1
        elif tag in _HIDDEN_TAGS or any(name == "hidden" for name, _ in attrs):
            if tag not in _VOID_TAGS:
                self.hidden_tag = tag
                self.hidden_depth = 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.hidden_tag is not None:
            if self.hidden_tag == tag:
                self.hidden_depth -= 1
                if not self.hidden_depth:
                    self.hidden_tag = None
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append("  ")

    def handle_data(self, data):
        if self.hidden_tag is None:
            self.parts.append(sub(r"\s+", " ", data))


def _remaining(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError
    return remaining


def _https_target(url: str) -> tuple[str, str]:
    if (
        len(url) > _MAX_URL_LENGTH
        or "\\" in url
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url)
    ):
        raise TicketSourceError("ticket_url_invalid")
    try:
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or "%" in parts.hostname
            or parts.username is not None
            or parts.port not in (None, 443)
        ):
            raise ValueError
        host = parts.hostname.encode("idna").decode("ascii")
        target = quote(parts.path or "/", safe="/!$&'()*+,;=:@%")
        if parts.query:
            target += "?" + quote(parts.query, safe="/?!$&'()*+,;=:@%")
        return host, target
    except ValueError:
        raise TicketSourceError("ticket_url_invalid") from None


def _is_public(address: str) -> bool:
    ip = ip_address(address)
    if not ip.is_global or ip.is_multicast:
        return False
    if isinstance(ip, IPv6Address):
        return (
            ip.scope_id is None
            and not ip.is_site_local
            and not ip.is_reserved
            and ip.ipv4_mapped is None
            and ip.sixtofour is None
            and ip.teredo is None
            and not any(ip in prefix for prefix in _NAT64_PREFIXES)
        )
    return True


async def _public_addresses(host: str) -> tuple[str, ...]:
    records = await asyncio.get_running_loop().getaddrinfo(
        host, 443, type=socket.SOCK_STREAM
    )
    addresses = tuple(dict.fromkeys(record[4][0] for record in records))
    if not addresses or not all(_is_public(address) for address in addresses):
        raise TicketSourceError("ticket_url_not_public")
    return addresses


class _PinnedHTTPSConnection(HTTPSConnection):
    def __init__(self, host: str, addresses: tuple[str, ...], deadline: float):
        super().__init__(host, port=443, context=_TLS_CONTEXT)
        self._addresses = addresses
        self._deadline = deadline

    def connect(self):
        for address in self._addresses:
            try:
                raw = socket.create_connection(
                    (address, 443),
                    timeout=min(_CONNECT_TIMEOUT, _remaining(self._deadline)),
                )
            except OSError:
                continue
            try:
                raw.settimeout(min(_CONNECT_TIMEOUT, _remaining(self._deadline)))
                self.sock = _TLS_CONTEXT.wrap_socket(raw, server_hostname=self.host)
                return
            except OSError:
                raw.close()
        raise OSError("Ticket host unavailable")


def _interrupt_socket(sock: socket.socket):
    with suppress(OSError):
        socket.socket.shutdown(sock, socket.SHUT_RDWR)


def _fetch(
    host: str, target: str, addresses: tuple[str, ...], deadline: float
) -> _Document | str:
    with closing(_PinnedHTTPSConnection(host, addresses, deadline)) as connection:
        connection.connect()
        assert connection.sock is not None
        # The deadline also applies to headers and chunked bodies sent one byte at a time.
        timer = Timer(_remaining(deadline), _interrupt_socket, args=(connection.sock,))
        timer.daemon = True
        timer.start()
        try:
            connection.request(
                "GET",
                target,
                headers={
                    "Accept": "application/pdf, text/html, application/xhtml+xml, application/octet-stream",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                    "User-Agent": "TravelHub/1.0",
                },
            )
            with connection.getresponse() as response:
                if response.status in _REDIRECT_STATUSES:
                    location = response.getheader("Location")
                    if not location:
                        raise TicketSourceError("ticket_url_unavailable")
                    return location
                if response.status != 200:
                    raise TicketSourceError("ticket_url_unavailable")
                if (
                    response.getheader("Content-Encoding", "identity").lower()
                    != "identity"
                ):
                    raise TicketSourceError("ticket_unsupported_document")
                if (
                    response.length is not None
                    and response.length > _MAX_DOCUMENT_BYTES
                ):
                    raise TicketSourceError("ticket_document_too_large")
                data = response.read(_MAX_DOCUMENT_BYTES + 1)
                _remaining(deadline)
                if len(data) > _MAX_DOCUMENT_BYTES:
                    raise TicketSourceError("ticket_document_too_large")
                if response.length:
                    raise TicketSourceError("ticket_url_unavailable")
                return _Document(
                    data,
                    response.headers.get_content_type(),
                    response.headers.get_content_charset(),
                )
        finally:
            timer.cancel()


async def _download(url: str) -> _Document:
    deadline = monotonic() + _URL_TIMEOUT
    try:
        async with asyncio.timeout(_URL_TIMEOUT):
            for _ in range(_MAX_REDIRECTS + 1):
                host, target = _https_target(url)
                addresses = await _public_addresses(host)
                result = await run_in_threadpool(
                    _fetch, host, target, addresses, deadline
                )
                if isinstance(result, _Document):
                    return result
                try:
                    url = urljoin(url, result)
                except ValueError:
                    raise TicketSourceError("ticket_url_invalid") from None
    except OSError, HTTPException:
        raise TicketSourceError("ticket_url_unavailable") from None
    raise TicketSourceError("ticket_url_redirect_limit")


def _pdf_text(data: bytes) -> str:
    if not _PDF_SLOTS.acquire(blocking=False):
        raise TicketSourceError("ticket_source_busy")
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(_PDF_WORKER)],
            input=data,
            env={},
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_PDF_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise TicketSourceError("ticket_pdf_limit") from None
    finally:
        _PDF_SLOTS.release()
    if result.returncode < 0:
        raise TicketSourceError("ticket_pdf_limit")
    if result.returncode:
        raise TicketSourceError(
            _PDF_FAILURES.get(result.returncode, "ticket_pdf_invalid")
        )
    return result.stdout.decode("utf-8")


def _html_text(document: _Document) -> str:
    try:
        html = document.data.decode(document.encoding or "utf-8", errors="replace")
    except LookupError, UnicodeError:
        html = document.data.decode("utf-8", errors="replace")
    parser = _HTMLText()
    try:
        parser.feed(html)
        parser.close()
    except ValueError, AssertionError:
        raise TicketSourceError("ticket_unsupported_document") from None
    text = "\n".join(
        line.strip() for line in "".join(parser.parts).splitlines() if line.strip()
    )
    if not text:
        raise TicketSourceError("ticket_document_has_no_text")
    if len(text) > _MAX_TEXT_LENGTH:
        raise TicketSourceError("ticket_document_too_large")
    return text


async def read_ticket_text(file: UploadFile | None, url: str | None) -> str:
    if (file is None) == (url is None):
        raise TicketSourceError("ticket_source_required")
    if file is not None:
        data = await file.read(_MAX_DOCUMENT_BYTES + 1)
        if len(data) > _MAX_DOCUMENT_BYTES:
            raise TicketSourceError("ticket_document_too_large")
        if not data.startswith(b"%PDF-"):
            raise TicketSourceError("ticket_unsupported_document")
        return await run_in_threadpool(_pdf_text, data)
    assert url is not None
    document = await _download(url)
    if document.data.startswith(b"%PDF-"):
        return await run_in_threadpool(_pdf_text, document.data)
    if document.media_type in _HTML_TYPES:
        return await run_in_threadpool(_html_text, document)
    raise TicketSourceError("ticket_unsupported_document")
