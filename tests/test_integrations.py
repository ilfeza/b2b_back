from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from marketplace.b2b.integrations.contracts import Order, SubscriptionState
from marketplace.b2b.models.entities import Store, Subscription
from marketplace.shared.db import utcnow
from marketplace.shared.errors import DomainError
from marketplace.shared.schemas import OrderStatus
from tests.conftest import onboard, product


async def failure(*args, **kwargs):
    raise DomainError(503, "DEPENDENCY_UNAVAILABLE", "Недоступно")


async def test_onboarding_recovers_partial_failure(cluster):
    state = cluster.apps["b2b"].state
    original = state.users.ensure_seller
    state.users.ensure_seller = failure
    response = await cluster.client.post(
        "/api/users/me/become-seller", json={"username": "pending_shop", "displayName": "Магазин"}
    )
    assert response.status_code == 503
    async with state.sessions() as db:
        store = await db.scalar(select(Store))
        assert store.onboarding_status == "pending"
        reserved = store.id
    assert (await cluster.client.get("/api/seller/profile")).status_code == 403
    state.users.ensure_seller = original
    store = await onboard(cluster.client, "pending_shop")
    assert store["id"] == str(reserved)


async def test_subscription_confirmation_only_and_repeat(cluster, seller):
    client, state = cluster.client, cluster.apps["b2b"].state
    response = await client.post(
        "/api/plans/subscribe", json={"tier": "premium"}, headers={"Idempotency-Key": "upgrade-1"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "pending"
    repeat = await client.post(
        "/api/plans/subscribe", json={"tier": "premium"}, headers={"Idempotency-Key": "upgrade-1"}
    )
    assert repeat.json() == response.json()
    assert (await client.get("/api/seller/subscription")).json()["data"]["tier"] == "free"
    assert (await client.post("/api/seller/subscription/sync")).json()["data"][
        "status"
    ] == "pending"
    state.payments.confirmed = True
    first = await client.post("/api/seller/subscription/sync")
    assert first.status_code == 200, first.text
    assert first.json()["data"]["tier"] == "premium"
    assert (await client.post("/api/seller/subscription/sync")).status_code == 200
    assert (await client.get("/api/seller/subscription")).json()["data"]["onlinePayments"] is True
    _, media = seller
    online = await product(client, media["url"], saleMode="online", availableDeliveries=["cdek"])
    assert online.status_code == 201, online.text
    state.payments.rejected = True
    assert (await client.post("/api/seller/subscription/sync")).json()["data"]["tier"] == "free"
    pid = online.json()["data"]["id"]
    assert (await client.get(f"/api/seller/products/{pid}")).json()["data"]["purchasable"] is False


async def test_payment_refusal_outage_and_free_online_forbidden(cluster, seller):
    client, state = cluster.client, cluster.apps["b2b"].state
    _, media = seller
    assert (
        await product(client, media["url"], saleMode="online", availableDeliveries=["cdek"])
    ).status_code == 403
    assert (
        await client.post(
            "/api/plans/subscribe", json={"tier": "free"}, headers={"Idempotency-Key": "x"}
        )
    ).status_code == 400
    assert (await client.post("/api/seller/subscription/sync")).status_code == 400
    assert (await client.post("/api/plans/subscribe", json={"tier": "premium"})).status_code == 400
    await client.post(
        "/api/plans/subscribe", json={"tier": "premium"}, headers={"Idempotency-Key": "x"}
    )
    state.payments.rejected = True
    response = await client.post("/api/seller/subscription/sync")
    assert response.json()["data"]["status"] == "rejected"
    state.payments.initiate = failure
    assert (
        await client.post(
            "/api/plans/subscribe", json={"tier": "premium"}, headers={"Idempotency-Key": "y"}
        )
    ).status_code == 503
    assert (await client.get("/api/seller/subscription")).json()["data"]["tier"] == "free"


async def test_subscription_wrong_owner_confirmation_rejected(cluster, seller):
    client, state = cluster.client, cluster.apps["b2b"].state
    response = await client.post(
        "/api/plans/subscribe", json={"tier": "premium"}, headers={"Idempotency-Key": "x"}
    )
    ref = response.json()["data"]["reference"]

    async def wrong(seller_id, reference):
        return SubscriptionState(
            seller_id=uuid4(),
            reference=ref,
            tier="premium",
            status="confirmed",
            valid_until=utcnow() + timedelta(days=30),
        )

    state.payments.state = wrong
    assert (await client.post("/api/seller/subscription/sync")).status_code == 502
    assert (await client.get("/api/seller/subscription")).json()["data"]["tier"] == "free"


async def test_expired_plan_is_effectively_free(cluster, seller):
    state = cluster.apps["b2b"].state
    async with state.sessions() as db:
        store = await db.scalar(select(Store))
        sub = await db.scalar(select(Subscription))
        store.plan_tier, sub.status, sub.valid_until, sub.online_payments = (
            "premium",
            "confirmed",
            utcnow() - timedelta(seconds=1),
            True,
        )
        await db.commit()
    result = (await cluster.client.get("/api/seller/subscription")).json()["data"]
    assert (
        result["tier"] == "free"
        and result["productLimit"] == 10
        and result["onlinePayments"] is False
    )


async def test_partial_dashboard_and_unavailable_orders(cluster, seller):
    state, client = cluster.apps["b2b"].state, cluster.client
    state.orders.summary, state.orders.list = failure, failure
    state.users.followers, state.analytics.views = failure, failure
    response = await client.get("/api/seller/dashboard")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["totalProductsCount"] == 0
    assert data["followersCount"] is None and data["newOrdersCount"] is None
    assert set(data["unavailableSections"]) == {"orders", "followers", "views"}
    assert (await client.get("/api/seller/orders")).status_code == 503
    assert (await client.get("/api/seller/followers")).status_code == 503
    assert (
        await client.get(
            "/api/seller/dashboard",
            params={"from": "2026-10-20T00:00:00Z", "to": "2026-10-19T00:00:00Z"},
        )
    ).status_code == 400


async def test_order_facade_owner_transitions_and_authoritative_confirmation(cluster, seller):
    state, client = cluster.apps["b2b"].state, cluster.client
    store, _ = seller
    order = Order(
        id="order-1",
        seller_id=store["id"],
        status="paid",
        created_at=utcnow(),
        items=[{"quantity": 1}],
        total=1800,
    )

    async def get(seller_id, order_id):
        return order

    async def change(seller_id, order_id, payload, key):
        assert key == "status-1"
        return order.model_copy(update={"status": OrderStatus(payload["status"])})

    state.orders.get, state.orders.change = get, change
    assert (await client.get("/api/seller/orders/order-1")).json()["data"]["total"] == 1800
    result = await client.patch(
        "/api/seller/orders/order-1/status",
        json={"status": "shipped", "trackingNumber": "CDEK123"},
        headers={"Idempotency-Key": "status-1"},
    )
    assert result.status_code == 200, result.text
    assert result.json()["data"]["status"] == "shipped"
    assert (
        await client.patch(
            "/api/seller/orders/order-1/status",
            json={"status": "delivered"},
            headers={"Idempotency-Key": "status-1"},
        )
    ).status_code == 400

    async def unconfirmed(*args):
        return order

    state.orders.change = unconfirmed
    assert (
        await client.patch(
            "/api/seller/orders/order-1/status",
            json={"status": "shipped"},
            headers={"Idempotency-Key": "status-1"},
        )
    ).status_code == 502
    order.seller_id = uuid4()
    assert (await client.get("/api/seller/orders/order-1")).status_code == 403
    assert (
        await client.patch(
            "/api/seller/orders/order-1/status",
            json={"status": "shipped"},
            headers={"Idempotency-Key": "status-1"},
        )
    ).status_code == 403


@pytest.mark.parametrize("code", [500, 503, 400, 401, 403, 404, 409])
async def test_http_transport_errors_are_never_success(code):
    from marketplace.shared.http import HTTPClient

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            code, json={"data": None, "error": {"message": "secret-connection-string"}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        upstream = HTTPClient(client, "http://external/api", "key", "external")
        with pytest.raises(DomainError) as error:
            await upstream.request("PATCH", "/resource", body={"x": 1})
        assert error.value.status == (503 if code >= 500 else code)
        assert "secret-connection-string" not in error.value.message
        assert len(calls) == 1


async def test_http_timeout_and_malformed_envelope():
    from marketplace.shared.http import HTTPClient

    def timeout(request):
        raise httpx.ReadTimeout("secret", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(DomainError) as error:
            await HTTPClient(client, "http://external", "key", "external").request("GET", "/")
        assert error.value.status == 503
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"wrong": "envelope"}))
    ) as client:
        with pytest.raises(DomainError) as error:
            await HTTPClient(client, "http://external", "key", "external").request("GET", "/")
        assert error.value.status == 502
