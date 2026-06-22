from fastapi import FastAPI, Query
from starlette.responses import JSONResponse

from app.responses import responses
from app.responses.exceptions import BadRequestError, HTTPException

app = FastAPI()


@app.exception_handler(HTTPException)
async def handle_http_exceptions(_app, error: HTTPException):
    return JSONResponse(
        error.dump_json(),
        status_code=error.status_code,
    )


@app.get("/ping", responses=responses(BadRequestError))
async def ping(test_error: bool = Query(False, alias="testError")) -> str:
    if test_error:
        raise BadRequestError()
    return "pong"
