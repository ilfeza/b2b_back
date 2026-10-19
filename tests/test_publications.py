from uuid import uuid4

import pytest

from tests.conftest import product


async def test_ordered_links_reorder_unlink_and_preview(cluster, seller):
    _, media = seller
    client = cluster.client
    a = (await product(client, media["url"], title="А")).json()["data"]["id"]
    b = (await product(client, media["url"], title="Б")).json()["data"]["id"]
    response = await client.post(
        "/api/posts",
        json={
            "title": "История",
            "body": "<p>Текст</p>",
            "coverImage": media["url"],
            "productIds": [b, a],
        },
    )
    assert response.status_code == 201, response.text
    pid = response.json()["data"]["id"]
    assert response.json()["data"]["productIds"] == [b, a]
    reordered = await client.patch(f"/api/posts/{pid}", json={"productIds": [a, b]})
    assert reordered.status_code == 200, reordered.text
    assert [p["id"] for p in reordered.json()["data"]["products"]] == [a, b]
    assert (await client.get(f"/api/seller/posts/{pid}/preview")).json()["data"]["productIds"] == [
        a,
        b,
    ]
    removed = await client.patch(f"/api/posts/{pid}", json={"productIds": []})
    assert removed.json()["data"]["products"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"productIds": [str(uuid4())]},
        {"productIds": [str(uuid4())] * 2},
        {"status": "published", "body": "<p><br></p>"},
        {"status": "published", "title": "", "body": "Текст"},
        {"coverImage": "https:// чужое.example/a.png"},
        {"likesCount": 100},
    ],
)
async def test_invalid_posts_do_not_persist(cluster, seller, payload):
    response = await cluster.client.post("/api/posts", json=payload)
    assert response.status_code == 400, response.text
    assert (await cluster.client.get("/api/seller/posts")).json()["data"]["total"] == 0


async def test_drafts_quota_search_and_invalid_patch_rollback(cluster, seller):
    client = cluster.client
    for n in range(5):
        response = await client.post("/api/posts", json={"title": f"Публикация {n}"})
        assert response.status_code == 201, response.text
    assert (await client.post("/api/posts", json={})).status_code == 403
    data = (
        await client.get("/api/seller/posts", params={"q": "Публикация", "pageSize": 2, "page": 2})
    ).json()["data"]
    assert data["total"] == 5 and len(data["items"]) == 2
    pid = data["items"][0]["id"]
    assert (await client.post(f"/api/seller/posts/{pid}/publish")).status_code == 400
    assert (await client.patch(f"/api/posts/{pid}", json={"title": None})).status_code == 400
    assert (await client.get(f"/api/seller/posts/{pid}")).json()["data"]["status"] == "draft"
    assert (await client.delete(f"/api/posts/{pid}")).status_code == 200
    assert (await client.post("/api/posts", json={})).status_code == 201


@pytest.mark.parametrize(
    "html",
    [
        "<script>alert(1)</script><p>ok</p>",
        '<iframe src="javascript:alert(1)"></iframe>ok',
        '<p onmouseover="alert(1)" style="color:red">ok</p>',
        '<a href="javascript:alert(1)">ok</a>',
        "<img src=x onerror=alert(1)>ok",
        "<svg><script>alert(1)</script></svg>ok",
        '<!-- attack --><h2 class="x">ok</h2>',
    ],
)
async def test_stored_html_is_sanitized(cluster, seller, html):
    response = await cluster.client.post("/api/posts", json={"title": "Безопасно", "body": html})
    assert response.status_code == 201, response.text
    body = response.json()["data"]["body"]
    assert all(
        bad not in body
        for bad in ("<script", "<iframe", "onmouseover=", "style=", "href=", "<img", "<svg", "<!--")
    )
    pid = response.json()["data"]["id"]
    assert (await cluster.client.get(f"/api/seller/posts/{pid}")).json()["data"]["body"] == body
