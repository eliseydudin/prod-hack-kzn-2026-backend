from fastapi.routing import APIRouter

from app.api.impl import user
from app.database import SessionDep
from app.models.user import CreateUserRequest, RegisterUserRequest, User
from app.responses import ConflictError, responses

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", responses=responses(ConflictError), status_code=201)
async def register(session: SessionDep, req: RegisterUserRequest) -> User:
    user_table = await user.create_user(session, CreateUserRequest.model_validate(req))
    return User.model_validate(user_table)
