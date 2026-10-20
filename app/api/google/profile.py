from logging import getLogger
from typing import Annotated

import jwt
from fastapi import Depends, Query
from httpx_oauth.clients.google import GoogleOAuth2
from httpx_oauth.oauth2 import GetAccessTokenError
from pydantic import ValidationError

from app.api.google.models import GoogleProfile
from app.responses import GoogleInteractionFailed as HTTPGoogleInteractionFailed
from app.responses.exceptions.google import FailureCause, GoogleInteractionFailed
from app.settings import settings

logger = getLogger(__name__)
google_api = GoogleOAuth2(
    settings.google_client_id,
    settings.google_client_secret,
)
jwks_client = jwt.PyJWKClient("https://www.googleapis.com/oauth2/v3/certs")


async def extract_google_user_profile(code: str, callback_url: str) -> GoogleProfile:
    try:
        access_token = await google_api.get_access_token(code, callback_url)
        id_token: str | None = access_token.get("id_token")

        if id_token is None:
            raise GoogleInteractionFailed(FailureCause.NO_ID_TOKEN)

        signing_key = jwks_client.get_signing_key_from_jwt(id_token)
        decoded = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=[signing_key.algorithm_name],
            audience=settings.google_client_id,
        )
        return GoogleProfile.model_validate(decoded)

    except GetAccessTokenError:
        raise GoogleInteractionFailed(FailureCause.NO_ACCESS_TOKEN)
    except jwt.InvalidTokenError:
        raise GoogleInteractionFailed(FailureCause.BAD_ID_TOKEN)
    except ValidationError as validation_error:
        logger.error(
            "Validation failed while interacting with Google's API",
            exc_info=validation_error,
        )

        raise GoogleInteractionFailed(FailureCause.VALIDATION_FAILED)


async def extract_google_user_profile_as_dependency(code: str = Query()):
    try:
        return await extract_google_user_profile(code, settings.google_callback_url)
    except GoogleInteractionFailed as error:
        raise HTTPGoogleInteractionFailed(cause=error.cause)


GoogleProfileDep = Annotated[
    GoogleProfile,
    Depends(extract_google_user_profile_as_dependency),
]
