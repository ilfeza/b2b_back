from datetime import timedelta
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from marketplace.b2b.integrations.fakes import BUYER_USER, DEMO_USER
from marketplace.shared.config import Settings
from marketplace.shared.db import utcnow
from tests.conftest import USER_B, onboard, product


async def test_two_sellers_are_isolated(cluster, seller):
    client = cluster.client
    a_store, a_media = seller
    pid = (await product(client, a_media["url"])).json()["data"]["id"]
    post_id = (await client.post("/api/posts", json={"body": "Текст"})).json()["data"]["id"]
    client.headers["X-User-Id"] = str(USER_B)
    b_store = await onboard(client, "shop_b")
    assert a_store["id"] != b_store["id"]
    for method, path, body in [
        ("GET", f"/seller/products/{pid}", None),
        ("PATCH", f"/products/{pid}", {"title": "Чужой"}),
        ("DELETE", f"/products/{pid}", None),
        ("GET", f"/seller/posts/{post_id}", None),
        ("PATCH", f"/posts/{post_id}", {"title": "Чужой"}),
        ("DELETE", f"/posts/{post_id}", None),
    ]:
        response = await client.request(method, "/api" + path, json=body)
        assert response.status_code == 404, response.text
    assert (await product(client, a_media["url"])).status_code == 400
    assert (
        await client.patch("/api/seller/profile", json={"avatar": a_media["url"]})
    ).status_code == 400
    assert (await client.post("/api/posts", json={"productIds": [pid]})).status_code == 400
    assert (await client.post("/api/posts", json={"coverImage": a_media["url"]})).status_code == 400
    for endpoint in (
        "/seller/products",
        "/seller/posts",
        "/seller/delivery",
        "/seller/followers",
        "/seller/orders",
        "/seller/dashboard",
    ):
        response = await client.get("/api" + endpoint, params={"sellerId": a_store["id"]})
        assert response.status_code == 200, response.text
        if endpoint.endswith(("products", "posts")):
            assert response.json()["data"]["total"] == 0
    client.headers["X-User-Id"] = str(DEMO_USER)
    assert (await client.get(f"/api/seller/products/{pid}")).status_code == 200
    assert (await client.get(f"/api/seller/posts/{post_id}")).status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/seller/profile",
        "/seller/products",
        "/seller/posts",
        "/seller/delivery",
        "/seller/dashboard",
        "/seller/orders",
        "/seller/followers",
        "/seller/subscription",
        "/seller/onboarding",
    ],
)
async def test_no_auth_and_buyer_denied(cluster, path):
    cluster.client.headers.pop("X-User-Id")
    response = await cluster.client.get("/api" + path)
    assert response.status_code == 401
    assert response.json()["data"] is None
    cluster.client.headers["X-User-Id"] = str(BUYER_USER)
    assert (await cluster.client.get("/api" + path)).status_code == 403


async def test_buyer_can_onboard_and_invalid_dev_user_denied(cluster):
    cluster.client.headers["X-User-Id"] = str(BUYER_USER)
    await onboard(cluster.client, "buyer_shop")
    assert (await cluster.client.get("/api/seller/profile")).status_code == 200
    for uid in ("bad", str(uuid4())):
        cluster.client.headers["X-User-Id"] = uid
        assert (await cluster.client.get("/api/seller/profile")).status_code == 401


@pytest.mark.parametrize(
    "name,path",
    [
        ("b2b", "/internal/sellers/{id}/entitlements"),
        ("goods", "/internal/sellers/{id}/products"),
        ("publications", "/internal/sellers/{id}/posts"),
    ],
)
async def test_internal_apis_require_key(cluster, name, path):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=cluster.apps[name]), base_url=f"http://{name}"
    ) as client:
        response = await client.get(
            "/api" + path.format(id=uuid4()),
            headers={"X-Service-Key": "wrong", "X-User-Id": str(DEMO_USER)},
        )
        assert response.status_code == 401


@pytest.mark.parametrize("flag", ["auth_dev_mode", "external_fake_mode"])
def test_dev_adapters_forbidden_in_production(flag):
    with pytest.raises(ValueError, match="forbidden"):
        Settings(_env_file=None, environment="production", **{flag: True})


@pytest.mark.parametrize(
    "bad",
    [
        "expired",
        "invalid_signature",
        "wrong_audience",
        "wrong_issuer",
        "no_exp",
        "none",
        "malformed",
    ],
)
async def test_invalid_jwt_rejected(cluster, seller, bad):
    settings = cluster.apps["b2b"].state.settings
    settings.auth_dev_mode = False
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings.jwt_public_key = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    settings.jwt_issuer, settings.jwt_audience = "users", "handmade"
    claims = {
        "sub": str(DEMO_USER),
        "iss": "users",
        "aud": "handmade",
        "exp": utcnow() + timedelta(hours=1),
    }
    if bad == "expired":
        claims["exp"] = utcnow() - timedelta(seconds=5)
    if bad == "wrong_audience":
        claims["aud"] = "other"
    if bad == "wrong_issuer":
        claims["iss"] = "other"
    if bad == "no_exp":
        del claims["exp"]
    key = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if bad == "invalid_signature"
        else private
    )
    token = jwt.encode(claims, key, algorithm="RS256")
    if bad == "none":
        token = jwt.encode(claims, "", algorithm="none")
    if bad == "malformed":
        token = "bad.token"
    cluster.client.cookies.set("auth-token", token)
    assert (await cluster.client.get("/api/seller/profile")).status_code == 401


async def test_csrf_and_verified_identity(cluster, seller):
    settings = cluster.apps["b2b"].state.settings
    settings.auth_dev_mode = False
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings.jwt_public_key = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    settings.jwt_issuer = "users"
    token = jwt.encode(
        {
            "sub": str(DEMO_USER),
            "iss": "users",
            "aud": "handmade",
            "exp": utcnow() + timedelta(hours=1),
        },
        private,
        algorithm="RS256",
    )
    cluster.client.cookies.set("auth-token", token)
    assert (await cluster.client.get("/api/seller/profile")).status_code == 200
    for headers in (
        {},
        {"Origin": "https://evil.example", "X-CSRF-Protection": "1"},
        {"Origin": "http://localhost:3000"},
    ):
        assert (
            await cluster.client.patch(
                "/api/seller/profile", headers=headers, json={"displayName": "Изменено"}
            )
        ).status_code == 403
    response = await cluster.client.patch(
        "/api/seller/profile",
        headers={"Origin": "http://localhost:3000", "X-CSRF-Protection": "1"},
        json={"displayName": "Проверено"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["displayName"] == "Проверено"


async def test_cors_request_id_errors_and_docs(cluster):
    client = cluster.client
    response = await client.options(
        "/api/seller/profile",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "X-CSRF-Protection",
        },
    )
    assert response.headers["Access-Control-Allow-Credentials"] == "true"
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
    for app in cluster.apps.values():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            assert (await c.get("/healthz")).status_code == 200
            assert (await c.get("/readyz")).status_code == 200
            assert (await c.get("/docs")).status_code == 200
            schema = (await c.get("/openapi.json")).json()
            assert schema["openapi"].startswith("3.")
            assert (await c.get("/missing")).json()["error"]["code"] == "NOT_FOUND"
    response = await client.get("/api/seller/profile", headers={"X-Request-Id": "trace-123"})
    assert response.headers["X-Request-Id"] == "trace-123"
    assert response.json()["error"]["requestId"] == "trace-123"
