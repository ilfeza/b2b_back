"""Create only an explicitly named disposable *_test database, never touch application data."""

import asyncio
import os

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


async def main():
    value = os.getenv("TEST_POSTGRES_URL")
    if not value:
        return
    url = make_url(value)
    if not url.database or not url.database.endswith("_test"):
        raise RuntimeError("TEST_POSTGRES_URL must use a disposable database ending in _test")
    engine = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        if not await conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": url.database}
        ):
            quoted = conn.dialect.identifier_preparer.quote(url.database)
            await conn.execute(text(f"CREATE DATABASE {quoted}"))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
