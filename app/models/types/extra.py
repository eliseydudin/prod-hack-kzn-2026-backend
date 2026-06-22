from typing import Any, Optional, Sequence, TypeVar

_T = TypeVar("_T")


def Extra(
    *,
    example: Optional[_T] = None,
    examples: Optional[Sequence[_T]] = None,
):
    assert (example is None) != (examples is None), (
        "choose one between `example` and `examples`"
    )
    result: dict[str, Any] = {}

    if example is not None:
        result["examples"] = [example]
    elif examples is not None:
        result["examples"] = list(examples)

    return result
