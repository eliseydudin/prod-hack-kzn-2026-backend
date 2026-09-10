from typing import Any, Optional, Sequence


def Extra[_T](
    *,
    example: Optional[_T] = None,
    examples: Optional[Sequence[_T]] = None,
    pattern: Optional[str] = None,
    format: Optional[str] = None,
):
    assert not (example is not None and examples is not None), (
        "choose one between `example` and `examples`"
    )
    result: dict[str, Any] = {}

    if example:
        result["examples"] = [example]
    elif examples:
        result["examples"] = list(examples)

    if pattern:
        result["pattern"] = pattern

    if format:
        result["format"] = format

    return result
