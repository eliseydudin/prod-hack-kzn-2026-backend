from typing import Annotated

import jwt
from fastapi import Depends, Query
from httpx_oauth.clients.google import GoogleOAuth2
from httpx_oauth.oauth2 import GetAccessTokenError
from pydantic import ValidationError

from app.api.google.models import GoogleProfile
from app.responses.exceptions import GoogleInteractionFailed as HttpGoogleError
from app.settings import settings

google_api = GoogleOAuth2(
    settings.google_client_id,
    settings.google_client_secret,
)
jwks_client = jwt.PyJWKClient("https://www.googleapis.com/oauth2/v3/certs")


class GoogleInteractionFailed(Exception):
    pass


async def extract_google_user_profile(code: str, callback_url: str) -> GoogleProfile:
    try:
        access_token = await google_api.get_access_token(code, callback_url)
        id_token: str | None = access_token.get("id_token")

        if id_token is None:
            raise GoogleInteractionFailed("No id_token found")

        signing_key = jwks_client.get_signing_key_from_jwt(id_token)
        decoded = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=[signing_key.algorithm_name],
            audience=settings.google_client_id,
        )
        return GoogleProfile.model_validate(decoded)

    except GetAccessTokenError:
        raise GoogleInteractionFailed
    except jwt.InvalidTokenError:
        raise GoogleInteractionFailed("Bad JWT token")
    except ValidationError as validation_error:
        errors = validation_error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
        errors_str = "; ".join(f"in {err['loc']}: {err['msg']}" for err in errors)
        raise GoogleInteractionFailed(f"Bad decoded JSON: {errors_str}")


async def extract_google_user_profile_as_dependency(code: str = Query()):
    try:
        return await extract_google_user_profile(code, settings.google_callback_url)
    except GoogleInteractionFailed as e:
        detail = {} if not e.args else {"inner": e.args[0]}
        # TODO make this the proper HTTP error
        raise HttpGoogleError(detail=detail)


GoogleProfileDep = Annotated[
    GoogleProfile,
    Depends(extract_google_user_profile_as_dependency),
]
