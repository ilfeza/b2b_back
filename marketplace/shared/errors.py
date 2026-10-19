from __future__ import annotations

import logging
from contextvars import ContextVar
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

request_id: ContextVar[str] = ContextVar("request_id", default="")
logger = logging.getLogger("marketplace")


class DomainError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status, self.code, self.message, self.details = status, code, message, details


def invalid(message: str, field: str | None = None):
    raise DomainError(400, "VALIDATION_ERROR", message, {"field": field} if field else None)


def forbidden(message: str = "Нет доступа"):
    raise DomainError(403, "FORBIDDEN", message)


def missing():
    raise DomainError(404, "NOT_FOUND", "Ресурс не найден")


def unavailable(source: str):
    raise DomainError(
        503, "DEPENDENCY_UNAVAILABLE", "Зависимый сервис недоступен", {"source": source}
    )


def error_response(status, code, message, details=None):
    return JSONResponse(
        status_code=status,
        content=jsonable_encoder(
            {
                "data": None,
                "error": {
                    "code": code,
                    "message": message,
                    "details": details,
                    "requestId": request_id.get(),
                },
            }
        ),
    )


def install_errors(app: FastAPI):
    @app.middleware("http")
    async def correlate(request: Request, call_next):
        incoming = request.headers.get("X-Request-Id", "")
        rid = (
            incoming
            if incoming and len(incoming) <= 100 and all(c.isalnum() or c in "-_" for c in incoming)
            else str(uuid4())
        )
        context = request_id.set(rid)
        try:
            try:
                response = await call_next(request)
            except Exception as exc:
                logger.error(
                    "unhandled_error", extra={"request_id": rid, "type": type(exc).__name__}
                )
                response = error_response(500, "SERVER_ERROR", "Внутренняя ошибка сервера")
            response.headers["X-Request-Id"] = rid
            logger.info(
                "request",
                extra={
                    "request_id": rid,
                    "service": app.title,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                },
            )
            return response
        finally:
            request_id.reset(context)

    @app.exception_handler(DomainError)
    async def domain_handler(request, exc):
        return error_response(exc.status, exc.code, exc.message, exc.details)

    async def validation_handler(request, exc):
        errors = [
            {"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()
        ]
        return error_response(400, "VALIDATION_ERROR", "Некорректные данные", {"errors": errors})

    app.add_exception_handler(RequestValidationError, validation_handler)
    app.add_exception_handler(ValidationError, validation_handler)

    @app.exception_handler(IntegrityError)
    async def conflict_handler(request, exc):
        return error_response(409, "CONFLICT", "Нарушено ограничение уникальности или целостности")

    @app.exception_handler(HTTPException)
    async def http_handler(request, exc):
        return error_response(
            exc.status_code,
            {
                404: "NOT_FOUND",
                405: "METHOD_NOT_ALLOWED",
                401: "UNAUTHORIZED",
                403: "FORBIDDEN",
            }.get(exc.status_code, "REQUEST_ERROR"),
            "Запрос отклонён",
        )

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        logger.error(
            "unhandled_error", extra={"request_id": request_id.get(), "type": type(exc).__name__}
        )
        return error_response(500, "SERVER_ERROR", "Внутренняя ошибка сервера")
