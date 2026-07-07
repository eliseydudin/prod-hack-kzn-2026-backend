from app.models.base import Base


class PaginationResult[T: Base](Base):
    items: list[T]
    size: int
    page: int
    amount: int
