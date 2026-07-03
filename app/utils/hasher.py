from pwdlib import PasswordHash

hasher = PasswordHash.recommended()


def hash_password(password: str):
    return hasher.hash(password)


def check_password(hashed: str, plain: str):
    return hasher.verify(plain, hashed)
