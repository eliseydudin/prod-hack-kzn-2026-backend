from logging import Filter, LogRecord, getLogger
from re import sub


class DriveAccessLogFilter(Filter):
    def filter(self, record: LogRecord) -> bool:
        if (
            not isinstance(record.args, tuple)
            or len(record.args) != 5
            or not isinstance(record.args[2], str)
        ):
            return True
        target = sub(
            r"(/(?:api/v1/)?integrations/google-drive/callback/?)\?[^\s\"]*",
            r"\1",
            record.args[2],
        )
        record.args = (*record.args[:2], target, *record.args[3:])
        return True


def setup_drive_logging() -> None:
    logger = getLogger("uvicorn.access")
    if not any(isinstance(item, DriveAccessLogFilter) for item in logger.filters):
        logger.addFilter(DriveAccessLogFilter())
