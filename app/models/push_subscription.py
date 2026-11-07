from base64 import b64decode

from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import HttpUrl, field_validator
from sqlmodel import Field, Text, UniqueConstraint

from app.models.base import Base, PrimaryKeyBase, SnowflakeIdBase
from app.models.types.snowflake_id import SnowflakeId


def _decode_key(value: str) -> bytes:
    return b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


class PushEndpoint(Base):
    endpoint: str = Field(sa_type=Text, unique=True, max_length=2048, repr=False)

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        url = HttpUrl(value)
        host = url.host or ""
        if (
            url.scheme != "https"
            or url.port != 443
            or url.username is not None
            or url.password is not None
            or url.fragment is not None
            or url.path in (None, "/")
        ):
            raise ValueError("invalid push endpoint")
        if host != "fcm.googleapis.com" and not host.endswith(
            (".push.services.mozilla.com", ".push.apple.com", ".notify.windows.com")
        ):
            raise ValueError("unsupported push service")
        endpoint = str(url)
        if len(endpoint) > 2048:
            raise ValueError("push endpoint is too long")
        return endpoint


class PushSubscriptionKeys(Base):
    p256dh: str = Field(min_length=87, max_length=88, repr=False)
    auth: str = Field(min_length=22, max_length=24, repr=False)

    @field_validator("p256dh")
    @classmethod
    def validate_public_key(cls, value: str) -> str:
        key = _decode_key(value)
        if len(key) != 65 or key[0] != 4:
            raise ValueError("p256dh must be an uncompressed P-256 public key")
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), key)
        return value

    @field_validator("auth")
    @classmethod
    def validate_auth_secret(cls, value: str) -> str:
        if len(_decode_key(value)) != 16:
            raise ValueError("auth must be a 16-byte secret")
        return value


class PushSubscriptionTable(
    PushEndpoint, PushSubscriptionKeys, SnowflakeIdBase, PrimaryKeyBase, table=True
):
    __table_args__ = (UniqueConstraint("id"),)

    owner_id: int = Field(foreign_key="user.primary_key", ondelete="CASCADE")


class CreatePushSubscriptionRequest(PushEndpoint):
    keys: PushSubscriptionKeys


class PushSubscription(SnowflakeIdBase):
    pass


class PushPublicKey(Base):
    public_key: str


class TestPushRequest(Base):
    subscription_id: SnowflakeId
