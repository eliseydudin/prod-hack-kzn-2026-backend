from dataclasses import dataclass


@dataclass
class Content:
    """
    Represents `Content-Type` of a FastAPI route.
    ```python
    cont = Content(200, "image/png")
    # this means that when the route returns the HTTP code 200 it responds with a PNG image.
    ```
    """

    status_code: int
    content_type: str
