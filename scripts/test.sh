#!/bin/sh
set -eu
python scripts/prepare_test_db.py
ruff check marketplace tests scripts migrations
ruff format --check marketplace tests scripts migrations
pytest --cov --cov-report=term-missing --cov-report=html --cov-fail-under=85 "$@"
