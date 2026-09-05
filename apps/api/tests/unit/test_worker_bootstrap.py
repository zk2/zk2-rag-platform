"""The worker has to stand up on its own, without main.py."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_importing_the_worker_registers_every_model() -> None:
    """The worker never goes through main.py, so it must register models itself.

    Without this the first mapper configuration inside a job fails with
    NoReferencedTableError on a table the worker happened not to import - and
    it fails at startup, where nothing is watching.
    """
    from sqlalchemy.orm import configure_mappers

    import zk2.jobs

    configure_mappers()
    assert zk2.jobs.WorkerSettings.functions


def test_the_worker_exposes_the_jobs_the_app_enqueues() -> None:
    import zk2.jobs

    names = {func.__name__ for func in zk2.jobs.WorkerSettings.functions}
    assert {"ingest_source", "run_eval", "check_mcp_servers"} <= names
