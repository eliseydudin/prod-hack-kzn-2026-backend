from logging import getLogger

from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse

from app import users
from app.auth.auth import send_refresh_token
from app.auth.google.profile import GoogleProfileDep, google_api
from app.database import SessionDep
from app.settings import settings

router = APIRouter(
    prefix="/google",
    tags=["google"],
    include_in_schema=False,
)
logger = getLogger(__name__)


@router.get("/", status_code=307)
async def google_auth(redirect: str | None = Query(default=None)):
    redirect_url = await google_api.get_authorization_url(
        settings.google_callback_url,
        state=redirect,
    )
    return RedirectResponse(redirect_url)


@router.get("/callback", status_code=200)
async def google_auth_callback(
    session: SessionDep,
    profile: GoogleProfileDep,
    state: str | None = Query(default=None),
):
    if user := await users.get_user_from_google_profile(session, profile):
        logger.info(
            "Created a new account @%s (%s) using Google's social login (%s)",
            user.username,
            user.id,
            profile.email,
        )
    else:
        user = await users.create_user_from_google_profile(session, profile)

    if state:
        response = RedirectResponse(state)
        send_refresh_token(user, response)
        return response
