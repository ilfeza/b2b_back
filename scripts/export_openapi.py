import asyncio
import json
from pathlib import Path

from marketplace.shared.app import create_app
from marketplace.shared.config import Settings
from marketplace.shared.messaging.schema import EVENTS


async def main():
    target = Path("docs/contracts")
    await asyncio.to_thread(target.mkdir, exist_ok=True)
    await asyncio.to_thread(
        (target / "events.v1.schema.json").write_text,
        json.dumps(EVENTS.json_schema(by_alias=True), ensure_ascii=False, indent=2) + "\n",
    )
    for service in ("b2b", "goods", "publications"):
        settings = Settings(
            _env_file=None,
            environment="test",
            kafka_enabled=False,
            service=service,
            service_key="schema-export-key",
            s3_access_key="schema",
            s3_secret_key="schema",
        )
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            schema = app.openapi()
            await asyncio.to_thread(
                (target / f"{service}.openapi.json").write_text,
                json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
            )
            print(service, len(schema["paths"]), "paths")


if __name__ == "__main__":
    asyncio.run(main())
