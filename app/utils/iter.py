from typing import Iterable, TypeVar

_T = TypeVar("_T")


def extend_with(iter: Iterable[_T], *values: _T) -> Iterable[_T]:
    """
    Yield all values from `iter` and append `values` at the end

    ```python
    def myiter():
        yield 10
        yield 20

    for e in extend_with(myiter(), 30, 40):
        print(e)
    # 10
    # 20
    # 30
    # 40
    ```
    """
    assert len(values) > 0

    yield from iter
    yield from values
