"""Canonical dashboard wrapper.

The Cloudflare adapter keeps the existing dashboard backend and authentication,
while adding the static-frontend CORS/session bridge and dashboard routes.
"""
from __future__ import annotations

from web import dashboard_cloud as base


def create_app(bot):
    return base.create_app(bot)
