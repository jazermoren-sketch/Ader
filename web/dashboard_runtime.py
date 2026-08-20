"""Live dashboard routes that keep OAuth guild sessions in sync with the bot."""
from __future__ import annotations

import asyncio

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from web.dashboard_shell import create_app as create_shell_app


async def _wait_for_bot(bot) -> None:
    """Avoid reporting a guild as missing while Discord.py is still filling its cache."""
    if getattr(bot, "is_ready", lambda: False)():
        return
    try:
        await asyncio.wait_for(bot.wait_until_ready(), timeout=15)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=503,
            detail="البوت مازال كيتصل بـDiscord. عاود المحاولة بعد ثوانٍ.",
        )


def _live_guild(bot, guild_id: int):
    guild = bot.get_guild(guild_id)
    if guild is not None:
        return guild
    # Fallback for cache edge cases: iterate the current guild cache once.
    return next((g for g in getattr(bot, "guilds", ()) if g.id == guild_id), None)


def create_app(bot):
    app = create_shell_app(bot)

    async def require_user(request: Request):
        user = request.session.get("discord_user")
        if not user:
            raise HTTPException(status_code=401, detail="تسجيل الدخول مطلوب")
        return user

    async def live_managed_guilds(request: Request):
        await require_user(request)
        await _wait_for_bot(bot)
        saved = request.session.get("managed_guilds", {}) or {}
        live = []
        refreshed = {}
        for key, info in saved.items():
            try:
                guild_id = int(info.get("id", key))
            except (TypeError, ValueError):
                continue
            guild = _live_guild(bot, guild_id)
            if guild is None:
                continue
            item = {
                "id": guild.id,
                "name": guild.name,
                "icon": info.get("icon"),
                "permissions": info.get("permissions", 0),
            }
            refreshed[str(guild.id)] = item
            live.append(item)
        request.session["managed_guilds"] = refreshed
        return live, refreshed

    async def guilds(request: Request):
        live, _ = await live_managed_guilds(request)
        return JSONResponse({"guilds": live})

    async def overview(request: Request):
        guild_id = int(request.path_params["guild_id"])
        _, managed = await live_managed_guilds(request)
        if str(guild_id) not in managed:
            raise HTTPException(status_code=403, detail="لا تملك صلاحية إدارة هذا الخادم أو البوت غير متصل به حالياً")
        guild = _live_guild(bot, guild_id)
        if guild is None:
            raise HTTPException(status_code=503, detail="البوت غير جاهز بعد أو لم يتم تحميل الخادم في الذاكرة")
        try:
            open_tickets = await bot.db.fetchone(
                "SELECT COUNT(*) FROM tickets WHERE guild_id=? AND status='open'", (guild_id,)
            )
        except Exception:
            open_tickets = (0,)
        try:
            teams = await bot.db.fetchone(
                "SELECT COUNT(*) FROM verified_teams WHERE guild_id=? AND active=1", (guild_id,)
            )
        except Exception:
            teams = (0,)
        return JSONResponse({
            "id": guild.id,
            "name": guild.name,
            "members": guild.member_count or len(getattr(guild, "members", ())),
            "channels": len(guild.channels),
            "open_tickets": int(open_tickets[0]) if open_tickets else 0,
            "verified_teams": int(teams[0]) if teams else 0,
        })

    # Insert before the legacy routes. This makes the selected guild list live
    # and prevents stale OAuth session data from producing a false 404.
    app.router.routes.insert(0, Route("/api/guilds/{guild_id:int}/overview", overview, methods=["GET"]))
    app.router.routes.insert(0, Route("/api/guilds", guilds, methods=["GET"]))
    return app
