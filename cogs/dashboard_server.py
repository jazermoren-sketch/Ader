"""Nova Aro dashboard web server.

Starts the authenticated FastAPI dashboard alongside Ader and creates the
small dashboard-only persistence tables that are independent from bot modules.
"""
from __future__ import annotations

import asyncio
import logging
import time

from discord.ext import commands


class DashboardServer(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.server = None
        self.task: asyncio.Task | None = None
        self.log = logging.getLogger("Ader.dashboard")

    async def _migrate_dashboard(self):
        # These tables deliberately use simple JSON ID arrays so the dashboard
        # can configure permissions without coupling itself to Discord objects.
        await self.bot.db.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_command_settings (
                guild_id INTEGER NOT NULL,
                command_name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                allowed_roles TEXT NOT NULL DEFAULT '[]',
                denied_roles TEXT NOT NULL DEFAULT '[]',
                allowed_channels TEXT NOT NULL DEFAULT '[]',
                denied_channels TEXT NOT NULL DEFAULT '[]',
                updated_at REAL NOT NULL,
                PRIMARY KEY (guild_id, command_name)
            )
        """)
        await self.bot.db.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_shortcut_settings (
                guild_id INTEGER NOT NULL,
                shortcut_name TEXT NOT NULL,
                alias TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                allowed_roles TEXT NOT NULL DEFAULT '[]',
                denied_roles TEXT NOT NULL DEFAULT '[]',
                allowed_channels TEXT NOT NULL DEFAULT '[]',
                denied_channels TEXT NOT NULL DEFAULT '[]',
                updated_at REAL NOT NULL,
                PRIMARY KEY (guild_id, shortcut_name)
            )
        """)

    async def cog_load(self):
        cfg = self.bot.config.get("web", {}) or {}
        if not cfg.get("enabled", True):
            self.log.info("Nova Aro dashboard is disabled in config")
            return

        await self._migrate_dashboard()

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

        # Give uvicorn a short chance to report immediate bind/import failures.
        await asyncio.sleep(0)
        if self.task.done() and self.task.exception():
            raise self.task.exception()

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
