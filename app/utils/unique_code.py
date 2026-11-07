from secrets import token_urlsafe


def generate_unique_code() -> str:
    return token_urlsafe(32)
