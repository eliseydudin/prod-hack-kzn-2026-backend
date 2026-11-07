from asyncio import run

from celery import Celery

from app.database import get_session_ctx
from app.database.connection import engine
from app.push.service import send_test_notification
from app.responses import GoneError, NotFoundError
from app.settings import settings

celery = Celery("push", broker=str(settings.redis_url or "redis://127.0.0.1:6379/0"))
celery.conf.update(
    task_default_queue="travelhub.push",
    broker_transport_options={"socket_connect_timeout": 1, "socket_timeout": 3},
)


async def _deliver_test_notification(owner_id: int, subscription_id: int) -> None:
    try:
        async with get_session_ctx() as session:
            await send_test_notification(session, owner_id, subscription_id)
    except NotFoundError, GoneError:
        pass
    finally:
        # asyncpg connections belong to the event loop created for this task.
        await engine.dispose()


@celery.task(ignore_result=True, acks_late=True)
def deliver_test_notification(owner_id: int, subscription_id: int) -> None:
    run(_deliver_test_notification(owner_id, subscription_id))
