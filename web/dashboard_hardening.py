from __future__ import annotations

import asyncio
import os
import time

import aiohttp
import discord
from fastapi import Request
from fastapi.responses import JSONResponse


"""Dashboard hardening helpers.

This module intentionally wraps the existing dashboard application instead of
creating a second dashboard server.  The previous version of this file had a
truncated triple-quoted string/code fragment after ``install_dashboard_hardening``
which made the whole dashboard import fail with SyntaxError.
"""


def _managed_guild(item: dict) -> dict | None:
    try:
        guild_id = int(item["id"])
        permissions = int(item.get("permissions", 0) or 0)
    except (KeyError, TypeError, ValueError):
        return None

    perms = discord.Permissions(permissions)
    owner = bool(item.get("owner"))
    if not (owner or perms.administrator or perms.manage_guild):
        return None

    return {
        "id": guild_id,
        "name": str(item.get("name") or "Unknown Server"),
        "icon": item.get("icon"),
        "permissions": permissions,
        "administrator": bool(owner or perms.administrator),
        "manage_guild": bool(owner or perms.manage_guild),
        "owner": owner,
    }


async def _refresh_oauth_guilds(request: Request, bot) -> tuple[bool, str]:
    """Refresh the authenticated user's manageable guilds from Discord.

    Discord's OAuth ``/users/@me/guilds`` response is the authoritative source
    for the user's OAuth permissions.  Only guilds where the user is owner,
    Administrator, or has Manage Server are retained, and the bot must also be
    connected to the guild before it is exposed by the dashboard.
    """
    from web import dashboard_app as base

    sid = request.session.get("sid")
    session = base._SESSIONS.get(str(sid)) if sid else None
    if not session:
        return False, "جلسة الدخول منتهية."

    access_token = str(session.get("access_token") or "")
    if not access_token:
        return False, "جلسة Discord غير صالحة. سجل الدخول من جديد."

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.get(
                "https://discord.com/api/users/@me/guilds",
                headers={"Authorization": f"Bearer {access_token}"},
            ) as response:
                if response.status == 401:
                    return False, "جلسة Discord منتهية. سجل الدخول من جديد."
                if response.status >= 400:
                    return False, "تعذر التحقق من صلاحيات Discord."
                data = await response.json(content_type=None)

        if not isinstance(data, list):
            return False, "Discord رجع بيانات غير صالحة."

        managed: dict[str, dict] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            row = _managed_guild(item)
            if row is None:
                continue
            if bot.get_guild(row["id"]) is None:
                continue
            row["bot_connected"] = True
            managed[str(row["id"])] = row

        session["managed_guilds"] = managed
        session["guilds_refreshed_at"] = time.time()
        return True, ""

    except (aiohttp.ClientError, asyncio.TimeoutError, TypeError, ValueError):
        return False, "تعذر الاتصال بـDiscord للتحقق من الصلاحيات."


def install_dashboard_hardening(app, bot) -> None:
    """Install dashboard middleware without replacing the base dashboard UI."""

    @app.middleware("http")
    async def hardening(request: Request, call_next):
        path = request.url.path

        if path == "/api/guilds" or path.startswith("/api/guilds/"):
            ok, message = await _refresh_oauth_guilds(request, bot)
            if not ok and request.session.get("sid"):
                return JSONResponse(
                    {"detail": message or "تعذر التحقق من صلاحيات Discord."},
                    status_code=401,
                )

        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response
