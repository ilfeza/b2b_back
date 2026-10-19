from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from sqlalchemy import select

from marketplace.shared.clients import GoodsClient, PolicyClient, PublicationsClient
from marketplace.shared.config import Settings
from marketplace.shared.db import configure_database
from marketplace.shared.errors import install_errors, unavailable
from marketplace.shared.schemas import Envelope


class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {
                "event": record.getMessage(),
                **{
                    key: getattr(record, key, None)
                    for key in ("request_id", "service", "method", "path", "status", "type")
                },
            },
            ensure_ascii=False,
        )


def create_app(settings: Settings, *, transport=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.events.start()
        try:
            yield
        finally:
            await app.state.events.stop()
            await app.state.http.aclose()
            if hasattr(app.state, "storage") and hasattr(app.state.storage, "client"):
                app.state.storage.client.close()
            await app.state.engine.dispose()

    logger = logging.getLogger("marketplace")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    app = FastAPI(title=f"Handmade {settings.service}", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    configure_database(app, settings)
    from marketplace.shared.messaging.runtime import KafkaRuntime

    app.state.events = KafkaRuntime(settings, app.state.sessions)
    app.state.http = httpx.AsyncClient(
        timeout=settings.upstream_timeout_seconds, transport=transport, follow_redirects=False
    )
    app.state.policy = PolicyClient(
        app.state.http, settings.b2b_service_url, settings.service_key, "b2b"
    )
    app.state.goods = GoodsClient(
        app.state.http, settings.goods_service_url, settings.service_key, "goods"
    )
    app.state.publications = PublicationsClient(
        app.state.http, settings.publications_service_url, settings.service_key, "publications"
    )
    if settings.service == "b2b":
        from marketplace.b2b.api.routes import router
        from marketplace.b2b.integrations.external import (
            AnalyticsClient,
            OrderServiceClient,
            SubscriptionServiceClient,
            UserServiceClient,
        )
        from marketplace.b2b.integrations.fakes import (
            FakeAnalytics,
            FakeOrders,
            FakePayments,
            FakeUsers,
        )
        from marketplace.b2b.integrations.storage import S3StorageClient

        app.state.users = (
            FakeUsers()
            if settings.external_fake_mode
            else UserServiceClient(
                app.state.http, settings.user_service_url, settings.service_key, "users"
            )
        )
        app.state.orders = (
            FakeOrders()
            if settings.external_fake_mode
            else OrderServiceClient(
                app.state.http, settings.order_service_url, settings.service_key, "orders"
            )
        )
        app.state.payments = (
            FakePayments()
            if settings.external_fake_mode
            else SubscriptionServiceClient(
                app.state.http, settings.payment_service_url, settings.service_key, "payments"
            )
        )
        app.state.analytics = (
            FakeAnalytics()
            if settings.external_fake_mode
            else AnalyticsClient(
                app.state.http, settings.analytics_service_url, settings.service_key, "analytics"
            )
        )
        # S3 is real even when external-team adapters are fake.
        app.state.storage = S3StorageClient(settings)
    elif settings.service == "goods":
        from marketplace.goods.api.routes import router
    else:
        from marketplace.publications.api.routes import router
    app.include_router(
        router,
        prefix=settings.api_prefix,
        responses={
            code: {"model": Envelope[None], "description": description}
            for code, description in {
                400: "Validation error",
                401: "Unauthenticated",
                403: "Forbidden or quota exceeded",
                404: "Not found",
                409: "Conflict",
                500: "Internal error",
                502: "Invalid upstream response",
                503: "Dependency unavailable",
            }.items()
        },
    )

    def openapi():
        if app.openapi_schema is None:
            schema = get_openapi(
                title=app.title,
                version=app.version,
                routes=app.routes,
                openapi_version=app.openapi_version,
            )
            for path in schema["paths"].values():
                for operation in path.values():
                    if isinstance(operation, dict):
                        operation.get("responses", {}).pop("422", None)
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi
    install_errors(app)
    from marketplace.shared.messaging.api import router as event_router

    app.include_router(event_router, prefix=settings.api_prefix)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "X-CSRF-Protection",
            "Idempotency-Key",
            "X-Request-Id",
            *(["X-User-Id"] if settings.auth_dev_mode else []),
        ],
        expose_headers=["X-Request-Id"],
    )

    @app.get("/healthz", tags=["Health"])
    async def health():
        return {"status": "ok", "service": settings.service}

    if settings.service == "b2b":
        from marketplace.b2b.models.entities import Store as readiness_model
    elif settings.service == "goods":
        from marketplace.goods.models.entities import Product as readiness_model
    else:
        from marketplace.publications.models.entities import Post as readiness_model

    @app.get("/readyz", tags=["Health"])
    async def ready():
        try:
            async with app.state.engine.connect() as connection:
                # Verify the schema as well as connectivity; a missing migration is not ready.
                await connection.execute(select(readiness_model.id).limit(1))
        except Exception:
            unavailable("database")
        return {"status": "ready", "service": settings.service}

    return app
