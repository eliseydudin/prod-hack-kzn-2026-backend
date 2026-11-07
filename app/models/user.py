from enum import auto
from typing import TYPE_CHECKING, Optional

from pydantic import field_validator, model_validator
from sqlmodel import Field, Relationship

from app.models.base import (
    Base,
    PrimaryKeyBase,
    SnowflakeIdBase,
)
from app.models.collaborator import CollaboratorTable
from app.models.types.extra import Extra
from app.models.types.pascal_case_enum import PascalCaseEnum
from app.models.types.time import CreatedAt, Timestamp, UpdatedAt

if TYPE_CHECKING:
    from app.models.trip import TripTable


USERNAME_REGEX = r"^[a-zA-Z0-9._]*$"
USERNAME_EXAMPLES = [
    "johndoe",
    "John_Doe1312",
    "liftyr.skinnyfists",
    "aerbh1231bfaber",
]

PASSWORD_REGEX = r"^[a-zA-Z0-9._?/+-]*$"


class UserRole(PascalCaseEnum):
    BOOTSTRAP = auto()
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

    @field_validator("username")
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


class UserExtraBase(UserBase, SnowflakeIdBase):
    role: UserRole = Field(default=UserRole.USER)
    created_at: Timestamp = CreatedAt()
    updated_at: Timestamp = UpdatedAt()


class UserIdentitySource(PascalCaseEnum):
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

    collaborator_in: list[TripTable] = Relationship(
        link_model=CollaboratorTable,
        sa_relationship_kwargs={"lazy": "selectin"},
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


class AdminCreateUserRequest(CreateUserRequest):
    @model_validator(mode="after")
    def validate_user_role(self):
        if self.role == UserRole.BOOTSTRAP:
            raise ValueError("Can't instantiate a bootstrap user this way")
        return self


# class EditUserRequest(Base):
#     username: MaybeMissing[str] = Field(
#         min_length=1,
#         max_length=32,
#         regex=USERNAME_REGEX,
#         schema_extra=Extra(
#             examples=USERNAME_EXAMPLES,
#             pattern=USERNAME_REGEX,
#         ),
#     )


class UserPaginationResult(Base):
    items: list[User]
    size: int
    page: int
    amount: int
    is_at_end: bool
