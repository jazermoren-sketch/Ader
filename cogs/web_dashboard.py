"""Start the Nova Aro web dashboard alongside the Discord bot."""
from __future__ import annotations

import asyncio
import os
from urllib.parse import urlparse

from discord.ext import commands


class WebDashboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.server_task = None
        self.server = None

    @staticmethod
    def _valid_redirect_uri(value: str) -> bool:
        """Return True only for an absolute HTTP(S) OAuth callback URL."""
        try:
            parsed = urlparse(value.strip())
            return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
        except Exception:
            return False

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

        # Prefer the configured public URL and never pass a malformed value to
        # Discord OAuth2. This also fixes deployments where an old/invalid
        # DASHBOARD_REDIRECT_URI remains in the environment.
        public_url = str(
            web_cfg.get("public_url")
            or os.getenv("DASHBOARD_PUBLIC_URL")
            or os.getenv("PUBLIC_URL")
            or ""
        ).strip().rstrip("/")

        configured_redirect = str(os.getenv("DASHBOARD_REDIRECT_URI", "")).strip()
        if public_url and self._valid_redirect_uri(public_url):
            expected_redirect = public_url + "/callback"
            if configured_redirect != expected_redirect:
                os.environ["DASHBOARD_REDIRECT_URI"] = expected_redirect
                self.bot.logger.info(
                    f"Nova Aro OAuth redirect URI set to {expected_redirect}"
                )
        elif configured_redirect and not self._valid_redirect_uri(configured_redirect):
            # Keep OAuth usable even if the environment contains a malformed
            # URI. api_v2 will derive the callback from request.base_url.
            os.environ.pop("DASHBOARD_REDIRECT_URI", None)
            self.bot.logger.warning(
                "Invalid DASHBOARD_REDIRECT_URI detected; using the dashboard public URL/request URL instead."
            )

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
