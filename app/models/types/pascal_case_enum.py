from enum import Enum

from pydantic.alias_generators import to_pascal


class PascalCaseEnum(Enum):
    @staticmethod
    def _generate_next_value_(name: str, start, count, last_values):
        return to_pascal(name)
