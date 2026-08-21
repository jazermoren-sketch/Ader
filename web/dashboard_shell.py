"""Dashboard application compatibility wrapper.

The canonical dashboard UI and OAuth2 flow live in :mod:`web.api_v2`.
This wrapper intentionally does not replace the root route, so the original
Nova Aro login page and OAuth flow remain the single source of truth.
"""
from __future__ import annotations

from web.api_v2 import create_app as create_base_app


def create_app(bot):
    return create_base_app(bot)
