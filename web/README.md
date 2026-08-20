# Ader Dashboard

Ader's dashboard is integrated into the running Python bot and does not run a second bot process or require MongoDB.

## Architecture

- `web/api_v2.py` — FastAPI routes, Discord OAuth2, guild authorization and dashboard API.
- `cogs/dashboard_server.py` — starts the canonical Uvicorn server inside Ader.
- `database/` — the existing SQLite data layer used by both the bot and dashboard.
- Discord OAuth2 scopes: `identify guilds`.

The design direction was adapted from the Krypto dashboard structure, but Krypto's MongoDB layer and unrelated bot systems are intentionally not imported.

## Environment

Set these variables in the hosting panel:

- `DISCORD_CLIENT_ID`
- `DISCORD_CLIENT_SECRET`
- `DASHBOARD_SESSION_SECRET`
- `DASHBOARD_REDIRECT_URI`

`DASHBOARD_REDIRECT_URI` must exactly match the callback configured in the Discord Developer Portal, for example `https://your-domain.example/callback`.

The server automatically prefers the hosting panel's `PORT`, with a default of `3000`.
