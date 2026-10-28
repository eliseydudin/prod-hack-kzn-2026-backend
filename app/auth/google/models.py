from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class GoogleProfile(BaseModel):
    """
    Do NOT expose this type to the end user
    """

    sub: str = Field(description="User's Google ID")

    email: EmailStr
    email_verified: bool
    at_hash: str

    name: str
    picture: str

    given_name: Optional[str] = None
    family_name: Optional[str] = None
