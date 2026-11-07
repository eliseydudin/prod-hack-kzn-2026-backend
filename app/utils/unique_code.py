from base64 import b64encode

from app.models.types.time import utc_now


def generate_unique_code():
    now = utc_now()
    return b64encode(str(now).encode()).decode().removesuffix("=")
