"""Nova Aro dashboard web server.

Starts the authenticated FastAPI dashboard alongside Ader and creates the
small dashboard-only persistence tables that are independent from bot modules.
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

    @staticmethod
    def _resolve_port(cfg: dict) -> int:
        """Resolve the externally allocated hosting port.

        Quaxly/FeatherPanel normally exposes the allocated port through
        SERVER_PORT. Using it takes precedence over the old hard-coded 8000
        value, so the dashboard listens on the same public allocation as the
        bot container. DASHBOARD_PORT is also supported for hosts that expose
        a dashboard-specific variable.
        """
        candidates = (
            os.getenv("DASHBOARD_PORT"),
            os.getenv("SERVER_PORT"),
            os.getenv("PORT"),
            cfg.get("port"),
            8000,
        )
        for value in candidates:
            try:
                port = int(str(value).strip())
            except (TypeError, ValueError):
                continue
            if 1 <= port <= 65535:
                return port
        return 8000

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

        host = str(
            os.getenv("DASHBOARD_HOST")
            or os.getenv("SERVER_IP")
            or cfg.get("host", "0.0.0.0")
        ).strip() or "0.0.0.0"
        port = self._resolve_port(cfg)
        public_url = str(cfg.get("public_url", "https://nova.aro/")).strip().rstrip("/") + "/"

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
        self.log.info(
            "Nova Aro dashboard starting on %s:%s | public URL: %s",
            host,
            port,
            public_url,
        )

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
