import json
from math import isfinite

from starlette.responses import JSONResponse


def _json_value(value):
    if isinstance(value, float) and not isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


class ErrorResponse(JSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(
            _json_value(content),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
