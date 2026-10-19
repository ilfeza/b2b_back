import asyncio
import os

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine

from marketplace.b2b.models.entities import Base

load_dotenv(".env", override=False)
config = context.config
url = os.environ.get("DATABASE_URL", config.get_main_option("sqlalchemy.url"))
schema = "b2b"


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name == schema
    return True


def run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        include_schemas=True,
        include_name=include_name,
        version_table_schema=schema,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def online():
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        exists = await connection.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :schema)"),
            {"schema": schema},
        )
        if not exists:
            await connection.execute(text('CREATE SCHEMA "b2b"'))
        await connection.commit()
        await connection.run_sync(run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=url,
        target_metadata=Base.metadata,
        literal_binds=True,
        include_schemas=True,
        include_name=include_name,
        version_table_schema=schema,
    )
    with context.begin_transaction():
        context.execute('CREATE SCHEMA IF NOT EXISTS "b2b"')
        context.run_migrations()
else:
    asyncio.run(online())
