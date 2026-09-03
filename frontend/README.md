# Ader Dashboard — Cloudflare Pages

This directory is a **static frontend**. It does not run Python, FastAPI, SQLite, or the Discord bot.

## Cloudflare Pages

Use these settings when importing the Ader repository:

- **Production branch:** `main`
- **Root directory:** `frontend`
- **Build command:** leave empty
- **Build output directory:** `.`

After deployment, Cloudflare gives the project a `*.pages.dev` address.

## Backend setup

Keep the Ader bot + FastAPI backend on the current Python host. Set:

```text
DASHBOARD_FRONTEND_URL=https://YOUR-PROJECT.pages.dev
DASHBOARD_REDIRECT_URI=https://YOUR-BACKEND-DOMAIN.example.com/callback
DASHBOARD_SESSION_SECRET=<long-random-secret>
```

Then edit `frontend/config.js` and set `API_BASE` to the public HTTPS URL of the backend.

## Discord OAuth2

In the Discord Developer Portal, add the backend callback URL from `DASHBOARD_REDIRECT_URI` as an OAuth2 Redirect URI. The browser starts OAuth on the backend, Discord returns to the backend callback, and the backend redirects the authenticated session to the Cloudflare Pages frontend.

## Features

- Discord OAuth2 login
- Server selector
- Responsive dark/light UI
- Overview statistics and Ader health
- Slash-command enable/disable controls
- Shortcut alias management
- Ticket panel creation/publishing
- Ticket list
- Verified Teams view
- Server roles/channels browser
- Mobile navigation
- Arabic/French UI direction toggle
