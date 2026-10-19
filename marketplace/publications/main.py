from __future__ import annotations

from marketplace.shared.app import create_app
from marketplace.shared.config import Settings


def factory():
    return create_app(Settings(service="publications"))
