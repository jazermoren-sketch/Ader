"""Nova Aro dashboard web server."""
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
        """Resolve the port allocated by the hosting panel."""
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

    @staticmethod
    def _resolve_host(cfg: dict) -> str:
        """Choose a bind address that actually exists inside the container.

        SERVER_IP is the panel's public/NAT address and commonly does NOT
        exist as a local interface inside the container. Binding to it causes
        uvicorn to fail with: ``could not bind on any address``. Therefore it
        is intentionally never used as a bind host. Use DASHBOARD_HOST only
        when a host explicitly needs a non-default local interface.
        """
        explicit = os.getenv("DASHBOARD_HOST") or cfg.get("host")
        if explicit:
            host = str(explicit).strip()
            if host:
                return host
        return "0.0.0.0"

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

        host = self._resolve_host(cfg)
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
