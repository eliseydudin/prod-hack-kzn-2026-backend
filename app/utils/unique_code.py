from base64 import b64encode
from random import randbytes

from app.models.types.time import utc_now


def generate_unique_code():
    now = utc_now()
    return (
        b64encode(randbytes(4) + str(now.timestamp()).encode()[-8:])
        .decode()
        .removesuffix("=")
    )
