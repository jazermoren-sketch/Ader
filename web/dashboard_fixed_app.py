"""Canonical dashboard wrapper.

The original dashboard backend is the single source of truth.  The wrapper
intentionally does not add another HTTP middleware layer here: the previous
hardening middleware accessed ``request.session`` before Starlette's
``SessionMiddleware`` was guaranteed to be in scope, which caused the
``/api/guilds`` request to return HTTP 500 on the dashboard server list.

Authentication and guild-management checks remain enforced by the canonical
``dashboard_app`` routes themselves.
"""
from __future__ import annotations

from web import dashboard_app as base


def create_app(bot):
    return base.create_app(bot)
