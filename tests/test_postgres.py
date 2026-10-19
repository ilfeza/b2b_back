import asyncio
import os
import subprocess
import sys

import pytest
from sqlalchemy import text

from tests.conftest import BASES, onboard, product

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def requires_postgres(postgres_url):
    if not postgres_url:
        pytest.skip("Set TEST_POSTGRES_URL to a disposable PostgreSQL 16 *_test database")


async def test_product_quota_concurrent_requests(cluster, seller):
    _, media = seller
    for n in range(9):
        assert (await product(cluster.client, media["url"], title=f"Товар {n}")).status_code == 201
    results = await asyncio.gather(
        *(product(cluster.client, media["url"], title=f"Гонка {n}") for n in range(4))
    )
    assert sorted(r.status_code for r in results) == [201, 403, 403, 403]
    assert (await cluster.client.get("/api/seller/products")).json()["data"]["total"] == 10
    assert all(
        r.json()["error"]["code"] == "QUOTA_EXCEEDED" for r in results if r.status_code == 403
    )


async def test_post_quota_concurrent_requests(cluster, seller):
    for n in range(4):
        assert (
            await cluster.client.post("/api/posts", json={"title": f"Черновик {n}"})
        ).status_code == 201
    results = await asyncio.gather(
        *(cluster.client.post("/api/posts", json={"title": f"Гонка {n}"}) for n in range(4))
    )
    assert sorted(r.status_code for r in results) == [201, 403, 403, 403]
    assert (await cluster.client.get("/api/seller/posts")).json()["data"]["total"] == 5


async def test_concurrent_onboarding_has_one_store(cluster):
    stores = await asyncio.gather(*(onboard(cluster.client, "same_shop") for _ in range(3)))
    assert len({s["id"] for s in stores}) == 1


async def test_schema_ownership_and_db_constraints(cluster, seller):
    engine = cluster.apps["b2b"].state.engine
    async with engine.connect() as conn:
        version = await conn.scalar(text("SHOW server_version"))
        assert version.startswith("16.")
        names = (
            await conn.execute(
                text(
                    "SELECT schemaname, tablename FROM pg_tables WHERE schemaname IN ('b2b','goods','publications')"
                )
            )
        ).all()
        assert ("goods", "products") in names and ("publications", "posts") in names
        assert ("b2b", "products") not in names and ("b2b", "posts") not in names
        assert not any(table in {"users", "orders", "payments"} for schema, table in names)
        constraints = (
            (
                await conn.execute(
                    text(
                        "SELECT conname FROM pg_constraint WHERE connamespace = 'goods'::regnamespace"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "ck_products_nonnegative_price" in constraints
        assert "ck_products_nonnegative_stock" in constraints


def test_alembic_roundtrip_and_model_consistency(postgres_url):
    env = dict(os.environ, DATABASE_URL=postgres_url)
    for name in BASES:
        for command in (("downgrade", "base"), ("upgrade", "head"), ("check",)):
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "-c", f"alembic-{name}.ini", *command],
                env=env,
                text=True,
                capture_output=True,
            )
            assert result.returncode == 0, result.stdout + result.stderr


async def test_database_roles_cannot_read_other_service_schema(cluster):
    from uuid import uuid4

    from sqlalchemy.exc import DBAPIError

    async with cluster.apps["b2b"].state.engine.connect() as conn:
        transaction = await conn.begin()
        try:
            role = "isolation_" + uuid4().hex
            await conn.execute(text(f'CREATE ROLE "{role}"'))
            await conn.execute(text(f'GRANT USAGE ON SCHEMA goods TO "{role}"'))
            await conn.execute(text(f'GRANT SELECT ON ALL TABLES IN SCHEMA goods TO "{role}"'))
            await conn.execute(text(f'SET LOCAL ROLE "{role}"'))
            await conn.execute(text("SELECT id FROM goods.products LIMIT 1"))
            with pytest.raises(DBAPIError):
                async with conn.begin_nested():
                    await conn.execute(text("SELECT id FROM b2b.seller_stores LIMIT 1"))
            with pytest.raises(DBAPIError):
                async with conn.begin_nested():
                    await conn.execute(text("SELECT id FROM publications.posts LIMIT 1"))
        finally:
            await transaction.rollback()
