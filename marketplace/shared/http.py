from __future__ import annotations

from typing import Any

import httpx
from pydantic import TypeAdapter, ValidationError

from marketplace.shared.errors import DomainError, request_id, unavailable


class HTTPClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str, key: str, source: str):
        self.client, self.base_url, self.key, self.source = (
            client,
            base_url.rstrip("/"),
            key,
            source,
        )

    def parse(self, schema, data):
        try:
            return TypeAdapter(schema).validate_python(data)
        except ValidationError as exc:
            raise DomainError(
                502,
                "UPSTREAM_ERROR",
                "Ответ зависимого сервиса нарушает контракт",
                {"source": self.source},
            ) from exc

    async def request(self, method: str, path: str, *, params=None, body=None, headers=None) -> Any:
        outgoing = {"X-Service-Key": self.key, "X-Request-Id": request_id.get()}
        outgoing.update(headers or {})
        try:
            response = await self.client.request(
                method, f"{self.base_url}{path}", params=params, json=body, headers=outgoing
            )
        except httpx.RequestError:
            unavailable(self.source)
        if response.status_code >= 500:
            unavailable(self.source)
        if response.status_code >= 400:
            # Do not reflect untrusted upstream messages, configuration, or response bodies.
            status = (
                response.status_code
                if response.status_code in {400, 401, 403, 404, 409, 422}
                else 502
            )
            code = {
                400: "VALIDATION_ERROR",
                401: "UNAUTHORIZED",
                403: "FORBIDDEN",
                404: "NOT_FOUND",
                409: "CONFLICT",
                422: "VALIDATION_ERROR",
            }.get(status, "UPSTREAM_ERROR")
            details = {"source": self.source}
            try:
                upstream_error = response.json().get("error", {})
                if status == 403 and upstream_error.get("code") == "QUOTA_EXCEEDED":
                    code = "QUOTA_EXCEEDED"
                    limit = (upstream_error.get("details") or {}).get("limit")
                    if type(limit) is int and limit >= 0:
                        details["limit"] = limit
            except (ValueError, TypeError, AttributeError):
                pass
            raise DomainError(
                status,
                code,
                "Достигнут лимит тарифа"
                if code == "QUOTA_EXCEEDED"
                else "Зависимый сервис отклонил запрос",
                details,
            )
        try:
            envelope = response.json()
            if envelope.get("error") is not None or "data" not in envelope:
                raise ValueError("Invalid envelope")
            return envelope["data"]
        except (ValueError, TypeError, AttributeError, ValidationError) as exc:
            raise DomainError(
                502,
                "UPSTREAM_ERROR",
                "Некорректный ответ зависимого сервиса",
                {"source": self.source},
            ) from exc
