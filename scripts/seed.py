"""Idempotent development data through the B2B API; no writes to other service databases."""

import argparse
import asyncio
import io

import httpx
from PIL import Image

from marketplace.b2b.integrations.fakes import DEMO_USER
from marketplace.shared.config import Settings


def checked(response):
    response.raise_for_status()
    return response.json()["data"]


async def seed(base_url: str):
    settings = Settings()
    if (
        settings.environment != "development"
        or not settings.auth_dev_mode
        or not settings.external_fake_mode
    ):
        raise RuntimeError(
            "Seed requires ENVIRONMENT=development, AUTH_DEV_MODE=true, EXTERNAL_FAKE_MODE=true"
        )
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/") + settings.api_prefix,
        headers={"X-User-Id": str(DEMO_USER)},
        timeout=30,
    ) as client:
        # The seed's idempotency uses immutable titles in the dedicated demo store.
        data = checked(
            await client.post(
                "/users/me/become-seller",
                json={
                    "username": "demo_workshop",
                    "displayName": "Демо мастерская",
                    "bio": "Керамика ручной работы",
                },
            )
        )
        store = data["seller"]
        image = store["avatar"]
        if not image:
            buf = io.BytesIO()
            Image.new("RGB", (64, 64), (194, 65, 12)).save(buf, "PNG")
            image = checked(
                await client.post(
                    "/uploads", files={"file": ("demo.png", buf.getvalue(), "image/png")}
                )
            )["url"]
            checked(await client.patch("/seller/profile", json={"avatar": image}))
        ids = []
        for n, title in enumerate(("Демо чашка", "Демо ваза", "Демо блюдце"), 1):
            listing = checked(await client.get("/seller/products", params={"q": title}))
            item = next((p for p in listing["items"] if p["title"] == title), None)
            if item is None:
                item = checked(
                    await client.post(
                        "/products",
                        json={
                            "title": title,
                            "image": image,
                            "price": n * 600,
                            "inStock": n,
                            "saleMode": "deal_only",
                            "dealContacts": "Свяжитесь с мастерской для оформления заказа",
                            "availableDeliveries": ["pickup"],
                            "pickupPoints": [
                                {"city": "Москва", "address": "Демонстрационный адрес, 1"}
                            ],
                        },
                    )
                )
            ids.append(item["id"])
        for title, selected in (
            ("Демо: как создаётся керамика", ids[:2]),
            ("Демо: новая коллекция", ids[1:]),
        ):
            listing = checked(await client.get("/seller/posts", params={"q": title}))
            if not any(p["title"] == title for p in listing["items"]):
                checked(
                    await client.post(
                        "/posts",
                        json={
                            "title": title,
                            "body": "<h2>Ручная работа</h2><p>История создания новой коллекции.</p>",
                            "status": "published",
                            "coverImage": image,
                            "productIds": selected,
                        },
                    )
                )
        checked(
            await client.patch(
                "/seller/delivery",
                json={
                    "pickupEnabled": True,
                    "pickupPoints": [{"city": "Москва", "address": "Демонстрационный адрес, 1"}],
                    "cdekEnabled": True,
                },
            )
        )
        print(
            f"Демо готово. sellerId={store['id']}; dev user={DEMO_USER}; внешний User/Order/Payment/Analytics — fake adapters; S3 — реальный."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    asyncio.run(seed(parser.parse_args().base_url))
