from app.api.routers.admin import router as admin_router
from app.api.routers.auth import router as auth_router

__all__ = [
    "auth_router",
    "admin_router",
]
