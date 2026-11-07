from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.integrations.google_drive.errors import PhotoTooLargeError
from app.settings import Settings

PREFIX = "/integrations/google-drive"


class DriveUploadLimit(StarletteHTTPException):
    def __init__(self):
        super().__init__(status_code=413, detail="photo_too_large")


async def handle_upload_limit(_request, _error):
    error = PhotoTooLargeError(detail={"reason": "photo_too_large"})
    return JSONResponse(error.dump_python(), status_code=413)


class GoogleDriveMiddleware:
    def __init__(self, app: ASGIApp, config: Settings):
        self.app = app
        self.limit = config.google_drive_max_photo_bytes + 65_536

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        path = scope.get("path", "").removeprefix(scope.get("root_path", ""))
        if scope["type"] != "http" or not (
            path == PREFIX or path.startswith(PREFIX + "/")
        ):
            return await self.app(scope, receive, send)

        async def private_send(message: Message):
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() not in (b"cache-control", b"referrer-policy")
                ] + [
                    (b"cache-control", b"no-store"),
                    (b"referrer-policy", b"no-referrer"),
                ]
            await send(message)

        if path != PREFIX + "/photo-exports" or scope["method"] != "POST":
            return await self.app(scope, receive, private_send)
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    raise DriveUploadLimit()
            return message

        try:
            for key, value in scope.get("headers", []):
                if key.lower() == b"content-length":
                    try:
                        if int(value) > self.limit:
                            raise DriveUploadLimit()
                    except ValueError:
                        pass
            await self.app(scope, bounded_receive, private_send)
        except DriveUploadLimit:
            error = PhotoTooLargeError(detail={"reason": "photo_too_large"})
            await JSONResponse(error.dump_python(), status_code=413)(
                scope, receive, private_send
            )
