from pwdlib import PasswordHash

hasher = PasswordHash.recommended()
dummy_hash = "$argon2id$v=19$m=65536,t=3,p=4$gD2/ebpnsnqEE8pEQAyI3g$TXaLDLSY+4mh/joQdYC8kV1D5lXewdL6BOyAX0QnxWY"


def hash_password(password: str):
    return hasher.hash(password)


def check_password(hashed: str, plain: str):
    return hasher.verify(plain, hashed)


def check_dummy_hash():
    check_password(dummy_hash, "dummy_password")
