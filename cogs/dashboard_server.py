"""Ader's canonical FastAPI dashboard server.

The dashboard shares the running Discord bot instance and the same SQLite
DatabaseManager, so dashboard changes apply immediately without a second bot
or a separate MongoDB service.
"""
from __future__ import annotations

import asyncio
import logging
import os

from discord.ext import commands


class DashboardServer(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.server = None
        self.task: asyncio.Task | None = None
        self.log = logging.getLogger("Ader.dashboard")

    async def _migrate_dashboard(self):
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

    @staticmethod
    def _resolve_port(cfg: dict) -> int:
        for value in (
            os.getenv("DASHBOARD_PORT"), os.getenv("SERVER_PORT"),
            os.getenv("PORT"), cfg.get("port"), 3000,
        ):
            try:
                port = int(str(value).strip())
            except (TypeError, ValueError):
                continue
            if 1 <= port <= 65535:
                return port
        return 3000

    @staticmethod
    def _resolve_host(cfg: dict) -> str:
        # Public/NAT panel IPs are often not local container interfaces.
        host = str(os.getenv("DASHBOARD_HOST") or cfg.get("host") or "0.0.0.0").strip()
        return host or "0.0.0.0"

    async def cog_load(self):
        cfg = self.bot.config.get("web", {}) or {}
        if not cfg.get("enabled", True):
            self.log.info("Ader dashboard is disabled in config")
            return

        await self._migrate_dashboard()
        try:
            import uvicorn
            from web.api_v2 import create_app
        except Exception as exc:
            self.log.error("Dashboard dependencies are unavailable: %s", exc, exc_info=True)
            return

        host, port = self._resolve_host(cfg), self._resolve_port(cfg)
        app = create_app(self.bot)
        config = uvicorn.Config(
            app, host=host, port=port,
            log_level=str(cfg.get("log_level", "info")).lower(),
            access_log=bool(cfg.get("access_log", False)),
            proxy_headers=True, forwarded_allow_ips="*",
        )
        self.server = uvicorn.Server(config)
        self.task = asyncio.create_task(self.server.serve(), name="ader-dashboard")
        self.log.info("Ader dashboard starting on %s:%s", host, port)
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
