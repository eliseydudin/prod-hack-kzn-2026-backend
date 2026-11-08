from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.auth import tokens
from app.files import MAX_FILE_BYTES
from app.main import app
from app.responses import NotFoundError
from app.storage import get_storage


def test_file_upload_and_read_are_private_and_validate_the_body(monkeypatch):
    objects = {}

    def put(path, data, content_type):
        objects[path] = (bytes(data), content_type)

    def get(path):
        if path not in objects:
            raise NotFoundError()
        return objects[path]

    async def storage():
        return SimpleNamespace(put=put, get=get)

    async def current_user(_session, token):
        return SimpleNamespace(id={"owner": 1, "other": 2}[token])

    monkeypatch.setitem(app.dependency_overrides, get_storage, storage)
    monkeypatch.setattr(tokens, "_get_current_user", current_user)
    client = TestClient(app)
    path = f"/api/v1/files/{uuid4()}"
    owner = {"Authorization": "Bearer owner"}
    pdf = {**owner, "Content-Type": "application/pdf"}
    assert client.put(path, content=b"%PDF-1.7").status_code == 401
    assert client.put(path, headers=pdf, content=b"<html>").status_code == 415

    def oversized_pdf():
        yield b"%PDF-1.7"
        yield bytes(MAX_FILE_BYTES)

    assert client.put(path, headers=pdf, content=oversized_pdf()).status_code == 413
    assert objects == {}

    png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    assert (
        client.put(
            path, headers={**owner, "Content-Type": "image/png"}, content=png
        ).status_code
        == 204
    )
    response = client.get(path, headers=owner)
    assert response.content == png and response.headers["content-type"] == "image/png"

    assert client.put(path, headers=pdf, content=b"%PDF-1.7").status_code == 204
    assert len(objects) == 1
    assert client.get(path).status_code == 401
    assert (
        client.get(path, headers={"Authorization": "Bearer other"}).status_code == 404
    )
    response = client.get(path, headers=owner)
    assert response.status_code == 200
    assert response.content == b"%PDF-1.7"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == "attachment"
    assert response.headers["cache-control"] == "private, no-store"
