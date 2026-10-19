"""Conservative object cleanup during an explicit maintenance window with all writes paused."""

import argparse
import asyncio
from datetime import timedelta

from sqlalchemy import select

from marketplace.b2b.models.entities import Media, Store
from marketplace.shared.app import create_app
from marketplace.shared.config import Settings
from marketplace.shared.db import utcnow
from marketplace.shared.http import HTTPClient


async def run(args):
    if args.apply and not args.writes_paused:
        raise RuntimeError(
            "--apply requires --writes-paused; pause ALL three services' writes first"
        )
    if args.older_than_hours < 24:
        raise RuntimeError("Minimum retention is 24 hours")
    settings = Settings(service="b2b")
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.sessions() as db:
            media = (
                await db.scalars(
                    select(Media).where(
                        Media.created_at < utcnow() - timedelta(hours=args.older_than_hours)
                    )
                )
            ).all()
            avatars = set((await db.scalars(select(Store.avatar))).all())
            for seller_id in {m.owner_seller_id for m in media}:
                # Any timeout/rejection aborts; never treat missing upstream data as unused files.
                refs = set(avatars)
                for source, base in (
                    ("goods", settings.goods_service_url),
                    ("publications", settings.publications_service_url),
                ):
                    upstream = HTTPClient(app.state.http, base, settings.service_key, source)
                    refs.update(
                        upstream.parse(
                            list[str],
                            await upstream.request(
                                "GET", f"/internal/sellers/{seller_id}/media/references"
                            ),
                        )
                    )
                for item in [
                    m for m in media if m.owner_seller_id == seller_id and m.url not in refs
                ]:
                    print(
                        f"{'delete' if args.apply else 'candidate'}: {item.id} {item.storage_key}"
                    )
                    if args.apply:
                        await app.state.storage.delete(item.storage_key)
                        await db.delete(item)
            if args.apply:
                await db.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--older-than-hours", type=int, default=168)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--writes-paused", action="store_true")
    asyncio.run(run(parser.parse_args()))
