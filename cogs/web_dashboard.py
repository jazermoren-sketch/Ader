"""Start the Nova Aro web dashboard alongside the Discord bot."""
from __future__ import annotations

import asyncio
import os

from discord.ext import commands


class WebDashboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.server_task = None
        self.server = None

    async def cog_load(self):
        web_cfg = self.bot.config.get("web", {}) or {}
        if not web_cfg.get("enabled", True):
            return
        try:
            import uvicorn
            from web.api_v2 import create_app
        except Exception as exc:
            self.bot.logger.error(f"Dashboard dependencies are unavailable: {exc}")
            return

        public_url = str(web_cfg.get("public_url", "")).rstrip("/")
        if public_url and not os.getenv("DASHBOARD_REDIRECT_URI"):
            os.environ["DASHBOARD_REDIRECT_URI"] = public_url + "/callback"

        app = create_app(self.bot)
        config = uvicorn.Config(
            app,
            host=str(web_cfg.get("host", "0.0.0.0")),
            port=int(web_cfg.get("port", 8000)),
            log_level="warning",
            access_log=False,
        )
        self.server = uvicorn.Server(config)
        self.server_task = asyncio.create_task(self.server.serve(), name="nova-aro-dashboard")
        self.bot.logger.info(
            f"Nova Aro dashboard started on {web_cfg.get('host', '0.0.0.0')}:{web_cfg.get('port', 8000)}"
        )

    async def cog_unload(self):
        if self.server:
            self.server.should_exit = True
        if self.server_task:
            try:
                await asyncio.wait_for(self.server_task, timeout=5)
            except Exception:
                self.server_task.cancel()


async def setup(bot):
    await bot.add_cog(WebDashboard(bot))
