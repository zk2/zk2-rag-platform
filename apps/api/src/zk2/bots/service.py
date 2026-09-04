"""Bot CRUD with versioning."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.models import User
from zk2.bots.models import Bot, BotSource, BotVersion
from zk2.bots.schemas import BotCreate, BotDto, BotPatch
from zk2.core.errors import NotFoundError


async def hydrate_bot(db: AsyncSession, bot: Bot) -> BotDto:
    version: BotVersion | None = None
    if bot.current_version_id is not None:
        version = await db.scalar(select(BotVersion).where(BotVersion.id == bot.current_version_id))
    source_ids = [
        s
        for (s,) in (
            await db.execute(select(BotSource.source_id).where(BotSource.bot_id == bot.id))
        ).all()
    ]
    return BotDto(
        id=bot.id,
        name=bot.name,
        system_prompt=version.system_prompt if version else None,
        llm_provider=version.llm_provider if version else "openai",
        llm_model=version.llm_model if version else "gpt-4o-mini",
        temperature=version.temperature if version else 0.0,
        num_k=version.num_k if version else 5,
        source_ids=source_ids,
        current_version_id=bot.current_version_id,
        created_at=bot.created_at,
        updated_at=bot.updated_at,
    )


async def create_bot(db: AsyncSession, *, org_id: int, user: User, payload: BotCreate) -> BotDto:
    bot = Bot(org_id=org_id, name=payload.name)
    db.add(bot)
    await db.flush()
    version = BotVersion(
        bot_id=bot.id,
        system_prompt=payload.system_prompt,
        llm_provider=payload.llm_provider,
        llm_model=payload.llm_model,
        temperature=payload.temperature,
        num_k=payload.num_k,
        created_by_user_id=user.id,
    )
    db.add(version)
    await db.flush()
    bot.current_version_id = version.id
    if payload.source_ids:
        db.add_all(BotSource(bot_id=bot.id, source_id=s) for s in payload.source_ids)
    await db.flush()
    return await hydrate_bot(db, bot)


async def list_bots(db: AsyncSession, *, org_id: int) -> list[BotDto]:
    bots = (
        (await db.execute(select(Bot).where(Bot.org_id == org_id).order_by(Bot.created_at.desc())))
        .scalars()
        .all()
    )
    return [await hydrate_bot(db, b) for b in bots]


async def get_bot(db: AsyncSession, *, org_id: int, bot_id: int) -> Bot:
    bot = await db.scalar(select(Bot).where(Bot.id == bot_id, Bot.org_id == org_id))
    if bot is None:
        raise NotFoundError("Bot not found")
    return bot


async def patch_bot(
    db: AsyncSession, *, org_id: int, user: User, bot_id: int, payload: BotPatch
) -> BotDto:
    bot = await get_bot(db, org_id=org_id, bot_id=bot_id)
    if payload.name is not None:
        bot.name = payload.name
    # If anything that lives on a version changes, snapshot a new version
    version_fields = {"system_prompt", "llm_provider", "llm_model", "temperature", "num_k"}
    diff = payload.model_dump(exclude_unset=True, exclude={"name", "source_ids"})
    if diff and bot.current_version_id is not None:
        current = await db.scalar(select(BotVersion).where(BotVersion.id == bot.current_version_id))
        new_version = BotVersion(
            bot_id=bot.id,
            system_prompt=diff.get("system_prompt", current.system_prompt if current else None),
            llm_provider=diff.get("llm_provider", current.llm_provider if current else "openai"),
            llm_model=diff.get("llm_model", current.llm_model if current else "gpt-4o-mini"),
            temperature=diff.get("temperature", current.temperature if current else 0.0),
            num_k=diff.get("num_k", current.num_k if current else 5),
            created_by_user_id=user.id,
        )
        db.add(new_version)
        await db.flush()
        bot.current_version_id = new_version.id
    if payload.source_ids is not None:
        await db.execute(delete(BotSource).where(BotSource.bot_id == bot.id))
        if payload.source_ids:
            db.add_all(BotSource(bot_id=bot.id, source_id=s) for s in payload.source_ids)
    bot.updated_at = datetime.now(UTC)
    await db.flush()
    _ = version_fields  # marker for static analyzers
    return await hydrate_bot(db, bot)


async def delete_bot(db: AsyncSession, *, org_id: int, bot_id: int) -> None:
    bot = await get_bot(db, org_id=org_id, bot_id=bot_id)
    await db.delete(bot)
    await db.flush()
