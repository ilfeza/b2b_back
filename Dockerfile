FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY marketplace marketplace
COPY migrations migrations
COPY alembic-*.ini ./
COPY scripts scripts
COPY docs/contracts docs/contracts
RUN useradd --uid 10001 --create-home app && chown -R app:app /app
USER app
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "marketplace.b2b.main:factory", "--factory", "--host", "0.0.0.0", "--port", "8000"]
