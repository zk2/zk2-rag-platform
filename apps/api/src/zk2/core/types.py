"""Custom SQLAlchemy column types.

SQLAlchemy ships TSVECTOR but no LTREE — define it ourselves.
"""

from __future__ import annotations

from sqlalchemy.types import UserDefinedType


class Ltree(UserDefinedType[str]):
    """Maps Postgres `ltree` column to a Python str."""

    cache_ok = True

    def get_col_spec(self, **_: object) -> str:
        return "ltree"

    def bind_processor(self, dialect):  # type: ignore[no-untyped-def]
        def process(value):  # type: ignore[no-untyped-def]
            return value

        return process

    def result_processor(self, dialect, coltype):  # type: ignore[no-untyped-def]
        def process(value):  # type: ignore[no-untyped-def]
            return value

        return process
