from enum import StrEnum, auto
from typing import Optional
from uuid import UUID

from pydantic import field_validator
from sqlmodel import Field, Relationship

from app.models.base import (
    Base,
    PrimaryKeyBase,
    UUIDBase,
)
from app.models.types.extra import Extra
from app.models.types.time import CreatedAt, Timestamp, UpdatedAt

USERNAME_REGEX = r"^[a-zA-Z0-9._]*$"
USERNAME_EXAMPLES = [
    "johndoe",
    "John_Doe1312",
    "liftyr.skinnyfists",
    "aerbh1231bfaber",
]

PASSWORD_REGEX = r"^[a-zA-Z0-9._?/+-]*$"


class UserRole(StrEnum):
    USER = auto()
    ADMIN = auto()


class UserBase(Base):
    username: str = Field(
        min_length=1,
        max_length=32,
        unique=True,
        regex=USERNAME_REGEX,
        schema_extra=Extra(
            examples=USERNAME_EXAMPLES,
            pattern=USERNAME_REGEX,
        ),
    )

    @field_validator("username", mode="before")
    @classmethod
    def validate_username(cls, username: str):
        if username[0].isdigit():
            raise ValueError("Usernames can't start with a digit")

        for special_symbol in [".", "_"]:
            if username[0] == special_symbol:
                raise ValueError(
                    f"Username can't start with the special symbol '{special_symbol}'"
                )

            if username[-1] == special_symbol:
                raise ValueError(
                    f"Username can't end with the special symbol '{special_symbol}'"
                )

            if (special_symbol * 2) in username:
                raise ValueError(
                    f"Can't have 2 special symbols '{special_symbol}' next to eachother"
                )

        return username


class UserExtraBase(UserBase, UUIDBase):
    role: UserRole = Field(
        default=UserRole.USER,
        exclude=True,
    )
    created_at: Timestamp = CreatedAt()
    updated_at: Timestamp = UpdatedAt()


class UserIdentitySource(StrEnum):
    GOOGLE = "google"


class UserIdentity(Base):
    source: UserIdentitySource
    identifier: str


class UserIdentityTable(UserIdentity, PrimaryKeyBase, table=True):
    pass


class UserTable(UserExtraBase, PrimaryKeyBase, table=True):
    password: Optional[str]
    enabled: bool = True

    identity_id: Optional[int] = Field(
        default=None,
        foreign_key="useridentity.primary_key",
    )
    identity: Optional[UserIdentityTable] = Relationship(
        sa_relationship_kwargs={"lazy": "selectin"}
    )


class User(UserExtraBase):
    pass


class RegisterUserRequest(UserBase):
    password: str = Field(
        min_length=8,
        max_length=64,
        regex=PASSWORD_REGEX,
        schema_extra=Extra(
            pattern=PASSWORD_REGEX,
            format="password",
        ),
    )


class CreateUserRequest(RegisterUserRequest):
    role: UserRole = UserRole.USER


class UserPaginationResult(Base):
    items: list[User]
    size: int
    page: int
    amount: int


class UserMeta(Base):
    refresh_token_user_id: UUID
