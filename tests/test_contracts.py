from datetime import timedelta
from uuid import uuid4

import httpx
import pytest

from marketplace.b2b.integrations.external import (
    AnalyticsClient,
    OrderServiceClient,
    SubscriptionServiceClient,
    UserServiceClient,
)
from marketplace.shared.clients import GoodsClient, PublicationsClient
from marketplace.shared.db import utcnow
from marketplace.shared.errors import DomainError
from tests.conftest import KEY, product


async def test_external_http_contracts():
    seller_id, user_id = uuid4(), uuid4()
    order = {
        "id": "o1",
        "seller": {"id": str(seller_id)},
        "status": "paid",
        "createdAt": utcnow().isoformat(),
        "items": [{"quantity": 1}],
        "total": 100,
    }
    user = {
        "id": str(user_id),
        "roles": ["buyer", "seller"],
        "displayName": "Мастер",
        "username": "master",
        "email": "user@example.test",
    }
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["X-Service-Key"] == KEY
        path = request.url.path
        if path == "/api/auth/me" or path.endswith("seller-role"):
            data = {"user": user}
        elif path.endswith("followers"):
            data = {
                "count": 1,
                "items": [{"id": str(user_id), "displayName": "Подписчик"}],
                "page": 1,
                "pageSize": 20,
            }
        elif path.endswith("orders-summary"):
            data = {
                "newOrdersCount": 1,
                "awaitingShipmentCount": 1,
                "shippedOrdersCount": 0,
                "totalOrdersCount": 1,
                "recentOrders": [order],
            }
        elif path == "/api/orders":
            data = {"items": [order], "total": 1, "page": 1, "pageSize": 20}
        elif "/orders/" in path:
            data = dict(order, status="shipped") if request.method == "PATCH" else order
        elif path.endswith("checkout"):
            data = {
                "reference": "ref-1",
                "paymentUrl": "https://payments.example/1",
                "status": "pending",
            }
        elif path.endswith("ref-1"):
            data = {
                "sellerId": str(seller_id),
                "reference": "ref-1",
                "status": "confirmed",
                "tier": "premium",
                "validUntil": (utcnow() + timedelta(days=30)).isoformat(),
                "onlinePayments": True,
            }
        else:
            data = {"count": 42}
        return httpx.Response(200, json={"data": data, "error": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        users = UserServiceClient(client, "http://users/api", KEY, "users")
        assert (await users.current("opaque-token")).email == "user@example.test"
        assert (await users.ensure_seller(user_id, seller_id)).roles == {"buyer", "seller"}
        assert (await users.followers(seller_id, 1, 20)).count == 1
        orders = OrderServiceClient(client, "http://orders/api", KEY, "orders")
        assert (await orders.list(seller_id, 1, 20, "paid", "o1")).total == 1
        assert (await orders.get(seller_id, "o1")).seller_id == seller_id
        assert (
            await orders.change(seller_id, "o1", {"status": "shipped"}, "status-key")
        ).status == "shipped"
        assert (await orders.summary(seller_id)).awaiting_shipment_count == 1
        payments = SubscriptionServiceClient(client, "http://payments/api", KEY, "payments")
        assert (await payments.initiate(seller_id, 990, "upgrade-key")).status == "pending"
        assert (await payments.state(seller_id, "ref-1")).online_payments is True
        assert (
            await AnalyticsClient(client, "http://analytics/api", KEY, "analytics").views(
                seller_id, utcnow() - timedelta(days=30), utcnow()
            )
            == 42
        )
    assert calls[0].headers["cookie"] == "auth-token=opaque-token"
    assert calls[1].headers["Idempotency-Key"] == str(seller_id)
    assert any(
        r.url.params.get("role") == "seller" and r.url.params.get("sellerId") == str(seller_id)
        for r in calls
    )
    assert any(r.headers.get("Idempotency-Key") == "upgrade-key" for r in calls)


@pytest.mark.parametrize("kind", ["goods", "publications", "users", "payments", "analytics"])
async def test_typed_clients_reject_broken_upstream_data(kind):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"data": {"wrong": "data"}, "error": None})
        )
    ) as client:
        args = (client, "http://external/api", KEY, kind)
        with pytest.raises(DomainError) as error:
            if kind == "goods":
                await GoodsClient(*args).get(uuid4(), uuid4())
            elif kind == "publications":
                await PublicationsClient(*args).get(uuid4(), uuid4())
            elif kind == "users":
                await UserServiceClient(*args).current("token")
            elif kind == "payments":
                await SubscriptionServiceClient(*args).state(uuid4(), "ref")
            else:
                await AnalyticsClient(*args).views(uuid4(), utcnow(), utcnow())
        assert error.value.status == 502


async def test_owner_service_read_contract_and_draft_privacy(cluster, seller):
    store, media = seller
    pid = (await product(cluster.client, media["url"])).json()["data"]["id"]
    draft = (
        await cluster.client.post("/api/posts", json={"title": "Текст", "body": "<p>История</p>"})
    ).json()["data"]["id"]
    async with httpx.AsyncClient(
        transport=cluster.transport, headers={"X-Service-Key": KEY}
    ) as internal:
        products = await internal.get("http://goods/api/internal/products")
        assert products.status_code == 200, products.text
        assert products.json()["data"]["total"] == 1
        assert (await internal.get(f"http://goods/api/internal/products/{pid}")).json()["data"][
            "sellerId"
        ] == store["id"]
        assert (
            await internal.get(f"http://publications/api/internal/posts/{draft}")
        ).status_code == 404
        assert (await internal.get("http://publications/api/internal/posts")).json()["data"][
            "total"
        ] == 0
        await cluster.client.post(f"/api/seller/posts/{draft}/publish")
        listed = await internal.get(
            "http://publications/api/internal/posts", params={"q": "Текст", "sellerId": store["id"]}
        )
        assert listed.json()["data"]["total"] == 1
        assert (
            await internal.get(f"http://publications/api/internal/posts/{draft}")
        ).status_code == 200
        await cluster.client.delete(f"/api/products/{pid}")
        assert (await internal.get(f"http://goods/api/internal/products/{pid}")).status_code == 404


async def test_store_batch_exposes_only_public_metadata(cluster, seller):
    store, media = seller
    async with httpx.AsyncClient(
        transport=cluster.transport, headers={"X-Service-Key": KEY}
    ) as internal:
        response = await internal.post(
            "http://b2b/api/internal/stores/batch", json={"ids": [store["id"], str(uuid4())]}
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert len(data) == 1 and data[0]["id"] == store["id"]
        assert set(data[0]) == {"id", "username", "displayName", "avatar", "bio", "joinedAt"}


async def test_catalog_search_filters_batch_validation_and_policy_outage(cluster, seller):
    store, media = seller
    pid = (
        await product(
            cluster.client, media["url"], title="100% ручная чашка", availableDeliveries=["cdek"]
        )
    ).json()["data"]["id"]
    async with httpx.AsyncClient(
        transport=cluster.transport, headers={"X-Service-Key": KEY}
    ) as internal:
        for path in ("/internal/products", f"/internal/sellers/{store['id']}/products"):
            response = await internal.get(
                "http://goods/api" + path,
                params={
                    "q": "%",
                    "sellerId": store["id"],
                    "delivery": "cdek",
                    "saleMode": "deal_only",
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["data"]["total"] == 1
            response = await internal.get(
                "http://goods/api" + path, params={"saleMode": "online", "sellerId": store["id"]}
            )
            assert response.json()["data"]["total"] == 0
        response = await internal.get(
            "http://goods/api/internal/products",
            params={"q": "чашка", "delivery": "cdek", "sellerId": store["id"]},
        )
        assert response.json()["data"]["total"] == 1
        response = await internal.get(
            "http://goods/api/internal/products", params={"saleMode": "deal_only"}
        )
        assert response.status_code == 400
        for path in ("/internal/products/batch", f"/internal/sellers/{store['id']}/products/batch"):
            assert (
                await internal.post("http://goods/api" + path, json={"ids": [pid, pid]})
            ).status_code == 400
        assert (
            await internal.get(f"http://goods/api/internal/products/{uuid4()}")
        ).status_code == 404

        async def empty(ids):
            return []

        cluster.apps["goods"].state.policy.batch = empty
        assert (await internal.get("http://goods/api/internal/products")).status_code == 503
        assert (
            await internal.post("http://goods/api/internal/products/batch", json={"ids": [pid]})
        ).status_code == 503
