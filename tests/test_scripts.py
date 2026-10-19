import json
from types import SimpleNamespace

import httpx

from marketplace.b2b.integrations.fakes import DEMO_USER
from scripts import seed, smoke


async def test_demo_seed_is_idempotent_through_three_service_http(cluster, monkeypatch):
    original = httpx.AsyncClient

    def client(**kwargs):
        return original(transport=cluster.transport, **kwargs)

    monkeypatch.setattr(seed.httpx, "AsyncClient", client)
    settings = cluster.apps["b2b"].state.settings
    settings.environment = "development"
    monkeypatch.setattr(seed, "Settings", lambda: settings)
    await seed.seed("http://b2b")
    await seed.seed("http://b2b")
    # Restore the shared module attribute before making assertions via the normal client.
    monkeypatch.setattr(seed.httpx, "AsyncClient", original)
    assert (await cluster.client.get("/api/seller/products")).json()["data"]["total"] == 3
    posts = (await cluster.client.get("/api/seller/posts")).json()["data"]
    assert posts["total"] == 2 and all(p["status"] == "published" for p in posts["items"])
    assert (await cluster.client.get("/api/seller/subscription")).json()["data"]["tier"] == "free"
    assert len(cluster.apps["b2b"].state.storage.objects) == 1


async def test_smoke_reads_actual_app_and_reports_timings(cluster, seller, monkeypatch, capsys):
    original = httpx.AsyncClient

    def client(**kwargs):
        return original(transport=cluster.transport, **kwargs)

    monkeypatch.setattr(smoke.httpx, "AsyncClient", client)
    args = SimpleNamespace(
        requests=10, concurrency=2, base_url="http://b2b", prefix="/api", user=str(DEMO_USER)
    )
    await smoke.run(args)
    report = json.loads(capsys.readouterr().out)
    assert report["requests"] == 10 and report["concurrency"] == 2
    assert report["durationSeconds"] > 0 and report["p95ms"] > 0
