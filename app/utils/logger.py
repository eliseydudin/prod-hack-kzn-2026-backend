import logging
from typing import Any

from rich_toolkit import RichToolkit, RichToolkitTheme
from rich_toolkit.styles import TaggedStyle
from uvicorn.logging import DefaultFormatter


class CustomFormatter(DefaultFormatter):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.toolkit = get_rich_toolkit()

    def formatMessage(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        return self.toolkit.print_as_string(message, tag=record.levelname)


def get_rich_toolkit() -> RichToolkit:
    theme = RichToolkitTheme(
        style=TaggedStyle(tag_width=11),
        theme={
            "tag.title": "white on #a48cf2",
            "tag": "white on #a48cf2",
            "placeholder": "grey85",
            "text": "white",
            "selected": "#007166",
            "result": "grey85",
            "progress": "on #a48cf2",
            "error": "red",
            "log.info": "black on blue",
        },
    )

    return RichToolkit(theme=theme)


def setup_logger(logger: logging.Logger):
    handler = logging.StreamHandler()
    handler.setFormatter(CustomFormatter())
    logger.addHandler(handler)
    logger.level = logging.DEBUG
