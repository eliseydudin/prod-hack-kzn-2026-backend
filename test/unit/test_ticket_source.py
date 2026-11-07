import asyncio
from http.client import HTTPResponse
from io import BytesIO
from time import monotonic
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import UploadFile
from pypdf import PdfWriter

from app.tickets import source


def test_source_blocks_non_public_ipv6():
    for address in ("fec0::1", "::127.0.0.1", "::169.254.169.254", "::ffff:8.8.8.8"):
        assert not source._is_public(address)
    assert source._is_public("2001:4860:4860::8888")


def test_source_rejects_truncated_http_response(monkeypatch):
    wire = BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 50\r\n\r\npartial")
    response = HTTPResponse(Mock(makefile=Mock(return_value=wire)))
    response.begin()
    connection = Mock(getresponse=Mock(return_value=response))
    monkeypatch.setattr(source, "_PinnedHTTPSConnection", Mock(return_value=connection))

    with pytest.raises(source.TicketSourceError, match="ticket_url_unavailable"):
        source._fetch("example.com", "/", ("93.184.216.34",), monotonic() + 5)


def test_html_source_ignores_hidden_subtrees_and_rejects_bad_entities():
    document = source._Document(
        b"<div hidden><div>Fake departure</div>Fake arrival</div><p>Real departure</p>",
        "text/html",
        None,
    )
    assert source._html_text(document) == "Real departure"
    invalid = source._Document(b"<p>&#" + b"9" * 5000 + b";</p>", "text/html", None)
    with pytest.raises(source.TicketSourceError, match="ticket_unsupported_document"):
        source._html_text(invalid)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/ticket",
        "https://user:secret@example.com/ticket",
        "https://example.com:8443/ticket",
        "https://example.com/\r\nHost: localhost",
        "https://example.com\\@localhost/ticket",
        "https://[fe80::1%eth0]/ticket",
        "https://example.com/" + "a" * 4096,
        "https://example.com/\ud800",
    ],
)
def test_invalid_url_never_reaches_network(url):
    with pytest.raises(source.TicketSourceError, match="ticket_url_invalid"):
        source._https_target(url)


async def test_dns_checks_every_address_before_fetch(monkeypatch):
    records = [
        (2, 1, 6, "", ("93.184.216.34", 443)),
        (2, 1, 6, "", ("169.254.169.254", 443)),
    ]
    monkeypatch.setattr(
        asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=records)
    )
    fetch = Mock()
    monkeypatch.setattr(source, "_fetch", fetch)
    with pytest.raises(source.TicketSourceError, match="ticket_url_not_public"):
        await source._download("https://example.com/ticket")
    fetch.assert_not_called()


async def test_redirect_is_resolved_and_checked_again(monkeypatch):
    lookup = AsyncMock(
        side_effect=[
            [(2, 1, 6, "", ("93.184.216.34", 443))],
            [(2, 1, 6, "", ("127.0.0.1", 443))],
        ]
    )
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)
    fetch = Mock(return_value="https://internal.example/ticket.pdf")
    monkeypatch.setattr(source, "_fetch", fetch)
    with pytest.raises(source.TicketSourceError, match="ticket_url_not_public"):
        await source._download("https://public.example/ticket")
    assert lookup.await_count == 2
    fetch.assert_called_once()
    assert fetch.call_args.args[:3] == ("public.example", "/ticket", ("93.184.216.34",))


def test_https_uses_pinned_address_and_hostname_for_tls(monkeypatch):
    raw = Mock()
    connect = Mock(return_value=raw)
    wrap_socket = Mock(return_value=Mock())
    monkeypatch.setattr(source.socket, "create_connection", connect)
    monkeypatch.setattr(source, "_TLS_CONTEXT", Mock(wrap_socket=wrap_socket))
    connection = source._PinnedHTTPSConnection(
        "public.example", ("93.184.216.34",), monotonic() + 5
    )
    connection.connect()
    assert connect.call_args.args == (("93.184.216.34", 443),)
    wrap_socket.assert_called_once_with(raw, server_hostname="public.example")
    connection.close()


@pytest.mark.parametrize(
    ("pages", "password", "reason"),
    [
        (1, "secret", "ticket_pdf_encrypted"),
        (1, None, "ticket_document_has_no_text"),
        (9, None, "ticket_pdf_limit"),
    ],
)
def test_real_pdf_worker_rejects_encrypted_empty_and_overlong_documents(
    pages, password, reason
):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    if password:
        writer.encrypt(password)
    data = BytesIO()
    writer.write(data)
    with pytest.raises(source.TicketSourceError, match=reason):
        source._pdf_text(data.getvalue())


async def test_pdf_upload_rejects_bad_content_and_size():
    for data, reason in [
        (b"not a PDF", "ticket_unsupported_document"),
        (b"%PDF-" + b"x" * source._MAX_DOCUMENT_BYTES, "ticket_document_too_large"),
        (b"%PDF-1.7 broken", "ticket_pdf_invalid"),
    ]:
        with pytest.raises(source.TicketSourceError, match=reason):
            await source.read_ticket_text(UploadFile(BytesIO(data)), None)


async def test_source_requires_exactly_one_document():
    for file, url in [
        (None, None),
        (UploadFile(BytesIO(b"pdf")), "https://example.com"),
    ]:
        with pytest.raises(source.TicketSourceError, match="ticket_source_required"):
            await source.read_ticket_text(file, url)
