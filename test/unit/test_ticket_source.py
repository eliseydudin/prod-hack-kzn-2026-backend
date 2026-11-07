from http.client import HTTPResponse
from io import BytesIO
from time import monotonic
from unittest.mock import Mock

import pytest

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
