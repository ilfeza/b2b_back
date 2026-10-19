from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from marketplace.b2b.integrations.contracts import SubscriptionState
from marketplace.b2b.models.entities import Store
from marketplace.shared.db import utcnow
from marketplace.shared.errors import DomainError
from tests.conftest import KEY, product


async def test_configurable_plans_and_success_dashboard(cluster, seller):
    state = cluster.apps["b2b"].state
    state.settings.premium_price = 1234
    response = await cluster.client.get("/api/plans")
    assert response.status_code == 200
    assert response.json()["data"][1]["price"] == 1234
    _, media = seller
    await product(cluster.client, media["url"], inStock=1)
    dashboard = (await cluster.client.get("/api/seller/dashboard")).json()["data"]
    assert dashboard["totalProductsCount"] == 1
    assert len(dashboard["lowStockProducts"]) == 1
    assert dashboard["newOrdersCount"] == 0 and dashboard["followersCount"] == 0
    assert dashboard["unavailableSections"] == [] and dashboard["demoData"] is True


async def test_policy_down_prevents_catalog_write(cluster, seller):
    original = cluster.apps["goods"].state.policy.entitlements

    async def fail(*args):
        raise DomainError(503, "DEPENDENCY_UNAVAILABLE", "Недоступно")

    cluster.apps["goods"].state.policy.entitlements = fail
    _, media = seller
    assert (await product(cluster.client, media["url"])).status_code == 503
    cluster.apps["goods"].state.policy.entitlements = original
    assert (await cluster.client.get("/api/seller/products")).json()["data"]["total"] == 0


async def test_invalid_role_confirmation_keeps_pending(cluster):
    state = cluster.apps["b2b"].state

    async def wrong(user_id, seller_id):
        return SimpleNamespace(id=user_id, roles={"buyer"})

    state.users.ensure_seller = wrong
    response = await cluster.client.post(
        "/api/users/me/become-seller", json={"username": "pending", "displayName": "Тест"}
    )
    assert response.status_code == 502
    async with state.sessions() as db:
        assert (await db.scalar(select(Store))).onboarding_status == "pending"


@pytest.mark.parametrize(
    "status,tier,validity",
    [("confirmed", "premium", -1), ("confirmed", "free", 30), ("unknown", "premium", 30)],
)
async def test_invalid_subscription_proof_rejected(cluster, seller, status, tier, validity):
    state, client = cluster.apps["b2b"].state, cluster.client
    await client.post(
        "/api/plans/subscribe", json={"tier": "premium"}, headers={"Idempotency-Key": "x"}
    )

    async def proof(seller_id, reference):
        return SubscriptionState(
            seller_id=seller_id,
            reference=reference,
            tier=tier,
            status=status,
            valid_until=utcnow() + timedelta(days=validity),
        )

    state.payments.state = proof
    assert (await client.post("/api/seller/subscription/sync")).status_code == 502
    assert (await client.get("/api/seller/subscription")).json()["data"]["tier"] == "free"


async def test_duplicate_checkout_prevents_extra_payment_calls(cluster, seller):
    state, client = cluster.apps["b2b"].state, cluster.client
    original, calls = state.payments.initiate, []

    async def initiate(*args):
        calls.append(args)
        return await original(*args)

    state.payments.initiate = initiate
    for _ in range(2):
        assert (
            await client.post(
                "/api/plans/subscribe",
                json={"tier": "premium"},
                headers={"Idempotency-Key": "same"},
            )
        ).status_code == 200
    assert (
        await client.post(
            "/api/plans/subscribe",
            json={"tier": "premium"},
            headers={"Idempotency-Key": "different"},
        )
    ).status_code == 409
    assert len(calls) == 1


async def test_premium_relaxes_both_quotas(cluster, seller):
    state, client = cluster.apps["b2b"].state, cluster.client
    await client.post(
        "/api/plans/subscribe", json={"tier": "premium"}, headers={"Idempotency-Key": "upgrade"}
    )
    state.payments.confirmed = True
    assert (await client.post("/api/seller/subscription/sync")).status_code == 200
    _, media = seller
    for n in range(11):
        assert (await product(client, media["url"], title=f"Товар {n}")).status_code == 201
    for n in range(6):
        assert (await client.post("/api/posts", json={"title": f"Черновик {n}"})).status_code == 201
    assert (await client.get("/api/seller/products")).json()["data"]["total"] == 11
    assert (await client.get("/api/seller/posts")).json()["data"]["total"] == 6


async def test_deleted_product_cannot_be_newly_linked_and_media_refs_retained(cluster, seller):
    store, media = seller
    client = cluster.client
    pid = (await product(client, media["url"])).json()["data"]["id"]
    post = (await client.post("/api/posts", json={"coverImage": media["url"]})).json()["data"]["id"]
    await client.delete(f"/api/products/{pid}")
    assert (await client.post("/api/posts", json={"productIds": [pid]})).status_code == 400
    async with httpx.AsyncClient(
        transport=cluster.transport, headers={"X-Service-Key": KEY}
    ) as internal:
        for host in ("goods", "publications"):
            result = await internal.get(
                f"http://{host}/api/internal/sellers/{store['id']}/media/references"
            )
            assert result.json()["data"] == [media["url"]]
        batch = await internal.post(
            "http://goods/api/internal/products/batch", json={"ids": [pid], "includeDeleted": True}
        )
        assert batch.json()["data"][0]["purchasable"] is False
        batch = await internal.post("http://goods/api/internal/products/batch", json={"ids": [pid]})
        assert batch.json()["data"] == []
    await client.delete(f"/api/posts/{post}")


async def test_post_product_section_degrades_after_confirmed_publication(cluster, seller):
    _, media = seller
    client = cluster.client
    pid = (await product(client, media["url"])).json()["data"]["id"]
    post_id = (
        await client.post(
            "/api/posts", json={"title": "Текст", "body": "Текст", "productIds": [pid]}
        )
    ).json()["data"]["id"]

    async def fail(*args, **kwargs):
        raise DomainError(503, "DEPENDENCY_UNAVAILABLE", "Недоступно")

    cluster.apps["b2b"].state.goods.batch = fail
    response = await client.post(f"/api/seller/posts/{post_id}/publish")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "published"
    assert response.json()["data"]["products"] == []
    assert response.json()["data"]["unavailableSections"] == ["products"]


async def test_commit_failure_never_returns_success_and_preserves_state(
    cluster, seller, monkeypatch
):
    from sqlalchemy.ext.asyncio import AsyncSession

    client = cluster.client
    before = (await client.get("/api/seller/profile")).json()["data"]["displayName"]

    async def fail_commit(self):
        raise RuntimeError("SECRET DATABASE CONNECTION")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "commit", fail_commit)
        response = await client.patch(
            "/api/seller/profile",
            json={"displayName": "Не должно сохраниться"},
            headers={"X-Request-Id": "commit-failure"},
        )
    assert response.status_code == 500, response.text
    assert response.headers["X-Request-Id"] == "commit-failure"
    assert response.json()["error"]["requestId"] == "commit-failure"
    assert "SECRET" not in response.text
    assert (await client.get("/api/seller/profile")).json()["data"]["displayName"] == before
