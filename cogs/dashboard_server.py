"""Nova Aro dashboard web server.

Runs the authenticated FastAPI dashboard alongside the Discord bot. The actual
UI/API lives in ``web.api_v2``; this cog is intentionally only the lifecycle
adapter so the dashboard starts and stops with Ader.
"""
from __future__ import annotations

import asyncio
import logging

from discord.ext import commands


class DashboardServer(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.server = None
        self.task: asyncio.Task | None = None
        self.log = logging.getLogger("Ader.dashboard")

    async def cog_load(self):
        cfg = self.bot.config.get("web", {}) or {}
        if not cfg.get("enabled", True):
            self.log.info("Nova Aro dashboard is disabled in config")
            return

        try:
            import uvicorn
            from web.api_v2 import create_app
        except Exception as exc:
            self.log.error("Dashboard dependencies are unavailable: %s", exc, exc_info=True)
            return

        host = str(cfg.get("host", "0.0.0.0"))
        try:
            port = int(cfg.get("port", 8000))
        except (TypeError, ValueError):
            port = 8000

        app = create_app(self.bot)
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level=str(cfg.get("log_level", "info")).lower(),
            access_log=bool(cfg.get("access_log", False)),
            proxy_headers=True,
            forwarded_allow_ips="*",
        )
        self.server = uvicorn.Server(config)
        self.task = asyncio.create_task(self.server.serve(), name="nova-aro-dashboard")
        self.log.info("Nova Aro dashboard starting on %s:%s", host, port)

    async def cog_unload(self):
        if self.server is not None:
            self.server.should_exit = True
        if self.task is not None:
            try:
                await asyncio.wait_for(self.task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.task.cancel()
        self.server = None
        self.task = None


async def setup(bot):
    await bot.add_cog(DashboardServer(bot))
