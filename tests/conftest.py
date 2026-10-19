import asyncio
import io
import os
import subprocess
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from uuid import UUID

import httpx
import pytest
from PIL import Image
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from marketplace.b2b.integrations.fakes import DEMO_USER, FakeStorage
from marketplace.b2b.models.entities import Base as B2BBase
from marketplace.goods.models.entities import Base as GoodsBase
from marketplace.publications.models.entities import Base as PostsBase
from marketplace.shared.app import create_app
from marketplace.shared.config import Settings

BASES = {"b2b": B2BBase, "goods": GoodsBase, "publications": PostsBase}
KEY = "test-internal-service-key-32-characters"
USER_B = UUID("33333333-3333-4333-8333-333333333333")


class ClusterTransport(httpx.AsyncBaseTransport):
    """Route actual HTTP clients to the three real ASGI apps, never mock domain clients."""

    def __init__(self):
        self.apps = {}
        self.calls = []

    async def handle_async_request(self, request):
        self.calls.append((request.method, request.url.host, request.url.path))
        app = self.apps[request.url.host]
        return await httpx.ASGITransport(app=app, raise_app_exceptions=True).handle_async_request(
            request
        )


@dataclass
class Cluster:
    apps: dict
    client: httpx.AsyncClient
    transport: ClusterTransport


async def make_cluster(tmp_path, *, database_url=None):
    transport = ClusterTransport()
    apps = {}
    for name in BASES:
        settings = Settings(
            _env_file=None,
            environment="test",
            kafka_enabled=False,
            service=name,
            database_url=database_url or f"sqlite+aiosqlite:///{tmp_path / (name + '.db')}",
            service_key=KEY,
            auth_dev_mode=name == "b2b",
            external_fake_mode=name == "b2b",
            s3_access_key="test",
            s3_secret_key="test",
            b2b_service_url="http://b2b/api",
            goods_service_url="http://goods/api",
            publications_service_url="http://publications/api",
        )
        app = create_app(settings, transport=transport)
        apps[name] = app
        if not database_url:

            @event.listens_for(app.state.engine.sync_engine, "connect")
            def fk_on(connection, record):
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

            async with app.state.engine.begin() as conn:
                await conn.run_sync(BASES[name].metadata.create_all)
    transport.apps = apps
    apps["b2b"].state.storage = FakeStorage()
    apps["b2b"].state.users.roles[USER_B] = {"buyer", "seller"}
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=apps["b2b"]),
        base_url="http://b2b",
        headers={"X-User-Id": str(DEMO_USER)},
    )
    return Cluster(apps, client, transport)


@pytest.fixture(scope="session")
def postgres_url():
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        return None
    parsed = make_url(url)
    if not parsed.database or not parsed.database.endswith("_test"):
        raise RuntimeError("Only disposable *_test databases are allowed")
    subprocess.run([sys.executable, "scripts/prepare_test_db.py"], check=True)

    async def reset_schemas():
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            for schema in BASES:
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()

    asyncio.run(reset_schemas())
    env = dict(os.environ, DATABASE_URL=url)
    for schema in BASES:
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", f"alembic-{schema}.ini", "upgrade", "head"],
            env=env,
            check=True,
        )
    return url


@pytest.fixture
async def cluster(tmp_path, postgres_url):
    value = await make_cluster(tmp_path, database_url=postgres_url)
    if postgres_url:
        tables = [
            f'"{schema}"."{table.name}"'
            for schema, base in BASES.items()
            for table in base.metadata.sorted_tables
        ]
        async with value.apps["b2b"].state.engine.begin() as conn:
            await conn.execute(text("TRUNCATE " + ", ".join(tables) + " CASCADE"))
    async with AsyncExitStack() as stack:
        for app in value.apps.values():
            await stack.enter_async_context(app.router.lifespan_context(app))
        await stack.enter_async_context(value.client)
        yield value


def png():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "orange").save(buf, format="PNG")
    return buf.getvalue()


async def onboard(client, username="shop_a"):
    response = await client.post(
        "/api/users/me/become-seller",
        json={"username": username, "displayName": "Мастерская", "bio": "Ручная работа"},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["seller"]


async def upload(client):
    response = await client.post("/api/uploads", files={"file": ("photo.png", png(), "image/png")})
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def product(client, image_url, **overrides):
    payload = {
        "title": "Керамическая чашка",
        "image": image_url,
        "price": 1800,
        "saleMode": "deal_only",
        "dealContacts": "Телефон +7 999 000 00 00",
        "inStock": 3,
    }
    payload.update(overrides)
    return await client.post("/api/products", json=payload)


@pytest.fixture
async def seller(cluster):
    store = await onboard(cluster.client)
    media = await upload(cluster.client)
    return store, media
