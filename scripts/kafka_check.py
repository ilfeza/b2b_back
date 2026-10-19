"""Repeatable local acceptance check, optionally stopping the REAL Kafka broker.

Uses HTTP APIs, real PostgreSQL outboxes, Kafka and S3; no direct database writes.
--outage also restarts Goods while Kafka is down to verify pending-event durability.
"""

import argparse
import asyncio
import io
import json
import shutil
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from PIL import Image

from marketplace.shared.config import Settings

ROOT = Path(__file__).resolve().parents[1]


async def docker(*args):
    executable = shutil.which("docker") or "/Applications/Docker.app/Contents/Resources/bin/docker"
    process = await asyncio.create_subprocess_exec(
        executable,
        "compose",
        *args,
        cwd=ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(60):
            await process.communicate()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode:
        raise RuntimeError("Docker lifecycle command failed")


async def run(args):
    settings = Settings()
    if (
        settings.environment != "development"
        or not settings.auth_dev_mode
        or not settings.external_fake_mode
    ):
        raise RuntimeError("Kafka check requires the explicit local development adapters")
    for url in (args.base_url, args.goods_url, args.publications_url):
        if urlparse(url).hostname not in {"localhost", "127.0.0.1"}:
            raise RuntimeError("This mutating acceptance check is limited to localhost")
    service_headers = {"X-Service-Key": settings.service_key}
    stopped = False
    pending_before_recovery = []
    async with httpx.AsyncClient(timeout=20) as client:

        async def api(method, path, *, base=args.base_url, headers=None, **kwargs):
            response = await client.request(
                method,
                base + settings.api_prefix + path,
                headers=headers or {"X-User-Id": "22222222-2222-4222-8222-222222222222"},
                **kwargs,
            )
            response.raise_for_status()
            return response.json()["data"]

        async def status(base):
            return await api(
                "GET", "/internal/events?limit=100", base=base, headers=service_headers
            )

        try:
            for url in (args.base_url, args.goods_url, args.publications_url):
                if not (await status(url))["enabled"]:
                    raise RuntimeError("Kafka must be enabled in all three services")
            store = await api(
                "POST",
                "/users/me/become-seller",
                json={
                    "username": "kafka_check_shop",
                    "displayName": "Проверка Kafka",
                    "bio": "Local event verification",
                },
            )
            image = io.BytesIO()
            Image.new("RGB", (8, 8), (190, 80, 40)).save(image, "PNG")
            media = await api(
                "POST", "/uploads", files={"file": ("check.png", image.getvalue(), "image/png")}
            )
            if args.outage:
                await docker("stop", "kafka")
                stopped = True
            item = await api(
                "POST",
                "/products",
                json={
                    "title": f"Kafka check {uuid4().hex[:8]}",
                    "image": media["url"],
                    "price": 100,
                    "saleMode": "deal_only",
                    "dealContacts": "Contact the workshop",
                    "inStock": 1,
                },
            )
            if args.outage:
                pending = [
                    r
                    for r in (await status(args.goods_url))["outbox"]
                    if r["aggregate_id"] == item["id"]
                ]
                assert pending and all(r["published_at"] is None for r in pending)
                pending_before_recovery.extend(pending)
                # Restart the owning process as well: the queue must survive in PostgreSQL.
                await docker("restart", "goods")
                async with asyncio.timeout(40):
                    while True:
                        try:
                            if (await client.get(args.goods_url + "/readyz")).status_code == 200:
                                break
                        except httpx.RequestError:
                            pass
                        await asyncio.sleep(0.25)
            post = await api(
                "POST",
                "/posts",
                json={
                    "title": "Kafka publication",
                    "body": "<p>Real event</p>",
                    "status": "published",
                    "productIds": [item["id"]],
                },
            )
            if args.outage:
                pending = [
                    r
                    for r in (await status(args.publications_url))["outbox"]
                    if r["aggregate_id"] == post["id"]
                ]
                assert pending and all(r["published_at"] is None for r in pending)
                pending_before_recovery.extend(pending)
                await docker("start", "kafka")
                stopped = False
            await api("PATCH", f"/products/{item['id']}", json={"price": 200})
            await api("PATCH", f"/posts/{post['id']}", json={"title": "Updated via real API"})
            await api("DELETE", f"/posts/{post['id']}")
            await api("DELETE", f"/products/{item['id']}")
            async with asyncio.timeout(120):
                while True:
                    records = []
                    for base, identity in [
                        (args.goods_url, item["id"]),
                        (args.publications_url, post["id"]),
                    ]:
                        records += [
                            r
                            for r in (await status(base))["outbox"]
                            if r["aggregate_id"] == identity
                        ]
                    b2b = await status(args.base_url)
                    receipts = {r["event_id"]: r for r in b2b["receipts"]}
                    if len(records) == 6 and all(
                        r["published_at"] and r["event_id"] in receipts for r in records
                    ):
                        break
                    await asyncio.sleep(0.25)
            result = {
                "status": "passed",
                "realBrokerStoppedAndRestarted": args.outage,
                "goodsRestartedDuringOutage": args.outage,
                "pendingBeforeRecovery": pending_before_recovery,
                "sellerId": store["seller"]["id"],
                "productId": item["id"],
                "publicationId": post["id"],
                "transport": "HTTP -> PostgreSQL transactional outbox -> Apache Kafka KRaft -> B2B inbox/activity",
                "events": [
                    dict(r, consumerGroup=receipts[r["event_id"]]["consumer_group"])
                    for r in records
                ],
            }
            seller_events = [
                r
                for r in b2b["outbox"]
                if r["event_type"] == "seller.created"
                and r["aggregate_id"] == store["seller"]["id"]
            ]
            if seller_events:
                seller_event_id = seller_events[0]["event_id"]
                async with asyncio.timeout(60):
                    while True:
                        goods_receipts = (await status(args.goods_url))["receipts"]
                        pub_receipts = (await status(args.publications_url))["receipts"]
                        if all(
                            any(r["event_id"] == seller_event_id for r in rows)
                            for rows in [goods_receipts, pub_receipts]
                        ):
                            break
                        await asyncio.sleep(0.25)
                result["sellerCreatedReceipt"] = {
                    "eventId": seller_event_id,
                    "receivedBy": ["goods", "publications"],
                }
            if args.output:
                await asyncio.to_thread(
                    Path(args.output).write_text,
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            if stopped:
                await docker("start", "kafka")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--goods-url", default="http://localhost:8081")
    parser.add_argument("--publications-url", default="http://localhost:8082")
    parser.add_argument("--outage", action="store_true")
    parser.add_argument("--output")
    asyncio.run(run(parser.parse_args()))
