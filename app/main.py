from fastapi import FastAPI
from starlette.responses import JSONResponse

from app.models.status import Status
from app.responses import responses
from app.responses.exceptions import HTTPException

app = FastAPI()


@app.exception_handler(HTTPException)
async def handle_http_exceptions(_app, error: HTTPException):
    return JSONResponse(
        error.dump_json(),
        status_code=error.status_code,
    )


@app.get("/health", responses=responses())
async def health() -> Status:
    return Status(status="ok")
