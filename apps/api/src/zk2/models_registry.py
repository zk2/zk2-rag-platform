"""Import-side-effect module that registers every ORM model on Base.metadata.

Import this from `main.py` (and from Alembic env.py) to guarantee that all
tables are known to SQLAlchemy before any query or FK is resolved.
"""

from __future__ import annotations

# ruff: noqa: F401
import zk2.auth.models
import zk2.bots.models
import zk2.llm.models
import zk2.orgs.models
import zk2.pipelines.models
import zk2.sources.models
