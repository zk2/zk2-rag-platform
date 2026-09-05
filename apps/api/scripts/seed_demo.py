"""Fill a workspace with something to look at.

Creates a folder of documents, a bot pointed at them, a pipeline, and a golden
set of questions whose answers are in those documents - so the product can be
demonstrated without anyone typing sample data first.

Indexing costs money (one embedding call per chunk), so it is opt-in:

    uv run python -m scripts.seed_demo            # rows only, sources stay pending
    uv run python -m scripts.seed_demo --ingest   # also index them, spending tokens
"""

from __future__ import annotations

import argparse
import asyncio

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.models import Membership, Organization, User
from zk2.bots.schemas import BotCreate
from zk2.bots.service import create_bot
from zk2.config import get_settings
from zk2.core.db import db_session
from zk2.core.logging import configure_logging
from zk2.evals.models import EvalDataset, EvalItem
from zk2.pipelines.service import create_pipeline
from zk2.sources.models import Source
from zk2.sources.service import create_directory, create_file_source

logger = structlog.get_logger()

FOLDER = "Company handbook"

DOCUMENTS: dict[str, str] = {
    "vacation-policy.md": """# Vacation policy

## Accrual

Employees accrue 20 working days of paid leave per calendar year, accruing
monthly from the start date. Unused days roll over once, into the following
year only.

## Requesting leave

Requests go to your manager at least two weeks ahead for anything longer than
three days. Shorter absences need one working day's notice.

## Leaving the company

Accrued and unused days are paid out with the final salary.
""",
    "expenses.md": """# Expense policy

## What is reimbursed

Travel, accommodation, meals during business travel, and tools needed for the
job. Receipts must be submitted within 30 days of the expense.

## Limits

Meals are reimbursed up to 40 EUR per day without prior approval. Anything
above that needs written approval from a manager before the spend, not after.

## How to submit

Upload the receipt to the finance portal with the project code. Reimbursement
runs with the next payroll cycle.
""",
    "incident-policy.md": """# Incident policy

## Severity

- SEV1: customers cannot use the product. Page immediately, any hour.
- SEV2: a major feature is broken, with a workaround. Page during work hours.
- SEV3: degraded but usable. A ticket, not a page.

## During an incident

One person is the incident lead and is not also debugging. Post updates every
30 minutes even when there is nothing new, because silence reads as absence.

## Afterwards

A written review within five working days. It names causes, never people, and
its output is changes with owners and dates.
""",
    "security.md": """# Security basics

## Access

Access is granted per role and reviewed quarterly. Shared accounts are not
permitted; every action must be attributable to a person.

## Secrets

Credentials live in the secret manager. A credential that has appeared in a
chat message, a ticket or a repository is considered leaked and is rotated the
same day.

## Reporting

Anything suspicious goes to security@example.com. Reporting something that
turns out to be harmless is always the right call.
""",
}

QUESTIONS: list[tuple[str, str, list[str]]] = [
    (
        "How many vacation days do employees get?",
        "20 working days per year.",
        ["vacation-policy.md"],
    ),
    (
        "Can unused vacation days be carried over?",
        "Yes, once - into the following year only.",
        ["vacation-policy.md"],
    ),
    (
        "What is the meal limit while travelling?",
        "40 EUR per day without prior approval.",
        ["expenses.md"],
    ),
    ("How long do I have to submit a receipt?", "Within 30 days of the expense.", ["expenses.md"]),
    (
        "When is an incident a SEV1?",
        "When customers cannot use the product; page immediately.",
        ["incident-policy.md"],
    ),
    (
        "What happens if a credential leaks into a ticket?",
        "It is treated as leaked and rotated the same day.",
        ["security.md"],
    ),
]


async def _target_org(db: AsyncSession) -> Organization:
    """The organization the super-admin belongs to."""
    email = get_settings().super_admin.email
    user: User | None = await db.scalar(select(User).where(User.email == (email or "").lower()))
    if user is None:
        msg = "Run `make seed` first: there is no super-admin to attach the demo to"
        raise SystemExit(msg)
    membership = await db.scalar(select(Membership).where(Membership.user_id == user.id))
    if membership is None:
        msg = "The super-admin has no organization. Run `make seed`."
        raise SystemExit(msg)
    org: Organization | None = await db.scalar(
        select(Organization).where(Organization.id == membership.org_id)
    )
    if org is None:
        msg = "The membership points at an organization that no longer exists."
        raise SystemExit(msg)
    return org


async def main(ingest: bool) -> None:
    configure_logging()

    async with db_session() as db:
        org = await _target_org(db)
        user: User | None = await db.scalar(
            select(User).where(User.email == (get_settings().super_admin.email or "").lower())
        )
        if user is None:
            raise SystemExit("Run `make seed` first")

        existing = await db.scalar(
            select(Source).where(Source.org_id == org.id, Source.name == FOLDER)
        )
        if existing is not None:
            logger.info("seed_demo.already_present", org=org.slug)
            return

        folder = await create_directory(db, org_id=org.id, name=FOLDER, parent_id=None)
        source_ids: list[int] = []
        for filename, body in DOCUMENTS.items():
            # arq=None runs ingest inline; passing it only when asked keeps the
            # default free
            source = await create_file_source(
                db,
                None if ingest else _NoQueue(),  # type: ignore[arg-type]
                org_id=org.id,
                parent_id=folder.id,
                filename=filename,
                content=body.encode(),
            )
            source_ids.append(source.id)
        logger.info("seed_demo.documents", count=len(source_ids), ingested=ingest)

        bot = await create_bot(
            db,
            org_id=org.id,
            user=user,
            payload=BotCreate(
                name="Handbook assistant",
                system_prompt=(
                    "You answer questions about the company handbook. Be brief and "
                    "concrete, and say when the handbook does not cover something."
                ),
                llm_provider="openai",
                llm_model="gpt-4.1-mini",
                num_k=5,
                source_ids=source_ids,
            ),
        )
        logger.info("seed_demo.bot", bot_id=bot.id)

        pipeline = await create_pipeline(
            db,
            org_id=org.id,
            user=user,
            name="Handbook RAG",
            description="A copy of the default graph, ready to edit",
            dag=None,
        )
        logger.info("seed_demo.pipeline", pipeline_id=pipeline.id)

        dataset = EvalDataset(
            org_id=org.id,
            name="Handbook golden set",
            description="Questions whose answers are in the seeded documents",
        )
        db.add(dataset)
        await db.flush()
        for question, answer, sources in QUESTIONS:
            db.add(
                EvalItem(
                    dataset_id=dataset.id,
                    question=question,
                    expected_answer=answer,
                    expected_sources=sources,
                    tags=["handbook"],
                )
            )
        logger.info("seed_demo.dataset", items=len(QUESTIONS))
        logger.info(
            "seed_demo.done",
            hint="Open /sources, /bots, /pipelines and /evals"
            + ("" if ingest else "; documents are pending until a worker indexes them"),
        )


class _NoQueue:
    """Stands in for the job queue: records the source without indexing it."""

    async def enqueue_job(self, *_args: object, **_kwargs: object) -> None:
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ingest",
        action="store_true",
        help="Index the documents now (spends embedding tokens)",
    )
    asyncio.run(main(parser.parse_args().ingest))
