"""Canonical dashboard wrapper.

The original dashboard backend stays the API source of truth; the hardened
middleware refreshes Discord OAuth permissions and serves the resilient tabbed
UI. This avoids a second dashboard server or duplicate API implementation.
"""
from __future__ import annotations

from web import dashboard_app as base
from web.dashboard_hardening import install_dashboard_hardening


def create_app(bot):
    app = base.create_app(bot)
    install_dashboard_hardening(app, bot)
    return app
