"""Production dashboard for Ader.

The UI is deliberately self-contained: no React/Vite/CDN/build step is required,
so a restart cannot leave the dashboard on a blank page because of a missing
frontend bundle. Authentication remains Discord OAuth2 and all mutations are
protected by the user's Discord guild-management permissions.
"""
from __future__ import annotations

import html
import json
import os
import time
from typing import Any
from urllib.parse import quote

import aiohttp
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware


MANAGE_GUILD = 0x20
ADMINISTRATOR = 0x8


def _json_ids(value: Any) -> str:
    if value is None:
        return "[]"
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = []
    if not isinstance(value, (list, tuple, set)):
        value = []
    result = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return json.dumps(sorted(set(result)), separators=(",", ":"))


def _loads_ids(value: Any) -> list[int]:
    try:
        parsed = json.loads(value or "[]") if isinstance(value, str) else (value or [])
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    result: list[int] = []
    for item in parsed:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _tree_commands(bot) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    def walk(commands, parent: str = "") -> None:
        for command in commands:
            name = f"{parent} {command.name}".strip()
            children = getattr(command, "commands", None)
            if children:
                walk(children, name)
            else:
                result.append(
                    {
                        "name": name,
                        "description": getattr(command, "description", "") or "",
                        "type": str(getattr(command, "type", "chat_input")),
                    }
                )

    walk(bot.tree.get_commands())
    return result


def _guild_is_managed(session_guild: dict[str, Any]) -> bool:
    try:
        permissions = int(session_guild.get("permissions", 0) or 0)
    except (TypeError, ValueError):
        permissions = 0
    return bool(
        permissions & ADMINISTRATOR
        or permissions & MANAGE_GUILD
        or session_guild.get("administrator")
        or session_guild.get("manage_guild")
    )


def _safe_error(exc: Exception) -> str:
    return str(exc).replace("<", "&lt;").replace(">", "&gt;")[:500]


def create_app(bot) -> FastAPI:
    cfg = bot.config.get("web", {}) or {}
    app = FastAPI(title="Ader Dashboard", version="3.0.0", docs_url="/api/docs", redoc_url=None)

    secret = os.getenv("DASHBOARD_SESSION_SECRET", "").strip() or str(cfg.get("session_secret", "")).strip()
    if len(secret) < 32:
        # Never use the bot token as the session key. A deterministic local fallback
        # keeps development usable while production is explicitly encouraged to set
        # DASHBOARD_SESSION_SECRET.
        secret = f"ader-dashboard:{os.getenv('DISCORD_CLIENT_ID', 'local')}:{str(cfg.get('public_url', 'local'))}"

    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        same_site="lax",
        https_only=False,
        max_age=86400,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.get("cors_origins", ["*"]),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    def oauth_ready() -> bool:
        return bool(
            os.getenv("DISCORD_CLIENT_ID", "").strip()
            and os.getenv("DISCORD_CLIENT_SECRET", "").strip()
        )

    def redirect_uri(request: Request) -> str:
        configured = os.getenv("DASHBOARD_REDIRECT_URI", "").strip()
        if configured:
            return configured.rstrip("/")
        public_url = str(cfg.get("public_url", "")).strip().rstrip("/")
        if public_url:
            return f"{public_url}/callback"
        return f"{str(request.base_url).rstrip('/')}/callback"

    async def require_user(request: Request) -> dict[str, Any]:
        user = request.session.get("discord_user")
        if not user:
            raise HTTPException(status_code=401, detail="تسجيل الدخول مطلوب")
        return user

    async def authorized_guild(request: Request, guild_id: int):
        await require_user(request)
        managed = request.session.get("managed_guilds", {}) or {}
        session_guild = managed.get(str(guild_id))
        if not isinstance(session_guild, dict) or not _guild_is_managed(session_guild):
            raise HTTPException(status_code=403, detail="لا تملك صلاحية إدارة هذا الخادم")

        guild = bot.get_guild(guild_id)
        if guild is None:
            raise HTTPException(status_code=404, detail="البوت غير متصل بهذا الخادم حالياً")
        return guild, request.session.get("discord_user")

    @app.get("/healthz")
    async def healthz():
        return {
            "ok": True,
            "bot_ready": bool(getattr(bot, "is_ready", lambda: False)()),
            "guilds": len(getattr(bot, "guilds", ())),
            "database": bool(getattr(bot.db, "is_connected", False)),
            "time": int(time.time()),
        }

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        if not request.session.get("discord_user"):
            return HTMLResponse(_login_html(oauth_ready()), headers={"Cache-Control": "no-store"})
        return HTMLResponse(_dashboard_html(), headers={"Cache-Control": "no-store"})

    @app.get("/login")
    async def login(request: Request):
        if not oauth_ready():
            return HTMLResponse(
                _error_html(
                    "إعدادات OAuth2 ناقصة",
                    "خاصك DISCORD_CLIENT_ID و DISCORD_CLIENT_SECRET في متغيرات البيئة.",
                ),
                status_code=503,
            )
        url = (
            "https://discord.com/oauth2/authorize?client_id="
            + quote(os.environ["DISCORD_CLIENT_ID"], safe="")
            + "&response_type=code&redirect_uri="
            + quote(redirect_uri(request), safe="")
            + "&scope=identify%20guilds"
        )
        return RedirectResponse(url)

    @app.get("/callback")
    async def callback(request: Request, code: str = "", error: str = "", error_description: str = ""):
        if error:
            return HTMLResponse(_error_html("تم إلغاء تسجيل الدخول", error_description or error), status_code=400)
        if not oauth_ready() or not code:
            return RedirectResponse("/")

        data = {
            "client_id": os.environ["DISCORD_CLIENT_ID"],
            "client_secret": os.environ["DISCORD_CLIENT_SECRET"],
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri(request),
        }
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post("https://discord.com/api/oauth2/token", data=data) as response:
                    token = await response.json(content_type=None)
                access_token = token.get("access_token") if isinstance(token, dict) else None
                if not access_token:
                    message = token.get("error_description") or token.get("error") or "تحقق من OAuth2 و Redirect URI."
                    return HTMLResponse(_error_html("فشل تسجيل الدخول", str(message)), status_code=400)

                headers = {"Authorization": f"Bearer {access_token}"}
                async with session.get("https://discord.com/api/users/@me", headers=headers) as response:
                    user = await response.json(content_type=None)
                async with session.get("https://discord.com/api/users/@me/guilds", headers=headers) as response:
                    user_guilds = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            return HTMLResponse(_error_html("تعذر الاتصال بـDiscord", _safe_error(exc)), status_code=502)
        except Exception as exc:
            return HTMLResponse(_error_html("وقع خطأ غير متوقع", _safe_error(exc)), status_code=500)

        if not isinstance(user, dict) or "id" not in user or not isinstance(user_guilds, list):
            return HTMLResponse(_error_html("استجابة Discord غير صالحة", "تعذر الحصول على بيانات الحساب والخوادم."), status_code=502)

        managed: dict[str, dict[str, Any]] = {}
        for item in user_guilds:
            if not isinstance(item, dict):
                continue
            try:
                guild_id = int(item["id"])
                permissions = int(item.get("permissions", 0) or 0)
            except (KeyError, TypeError, ValueError):
                continue
            administrator = bool(permissions & ADMINISTRATOR)
            manage_guild = bool(permissions & MANAGE_GUILD)
            if administrator or manage_guild:
                managed[str(guild_id)] = {
                    "id": guild_id,
                    "name": str(item.get("name") or "Unknown Server"),
                    "icon": item.get("icon"),
                    "permissions": permissions,
                    "administrator": administrator,
                    "manage_guild": manage_guild,
                }

        request.session.clear()
        request.session["discord_user"] = {
            "id": int(user["id"]),
            "username": str(user.get("username") or "Discord User"),
            "global_name": str(user.get("global_name") or user.get("username") or "Discord User"),
            "avatar": user.get("avatar"),
        }
        request.session["managed_guilds"] = managed
        return RedirectResponse("/")

    @app.get("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/")

    @app.get("/api/me")
    async def me(request: Request):
        user = request.session.get("discord_user")
        return {"user": user, "logged_in": bool(user)}

    @app.get("/api/guilds")
    async def guilds(request: Request):
        await require_user(request)
        return {"guilds": list((request.session.get("managed_guilds", {}) or {}).values())}

    @app.get("/api/guilds/{guild_id}/overview")
    async def overview(request: Request, guild_id: int):
        guild, _ = await authorized_guild(request, guild_id)
        try:
            tickets = await bot.db.fetchone(
                "SELECT COUNT(*) FROM tickets WHERE guild_id=? AND status='open'", (guild_id,)
            )
            ticket_count = int(tickets[0]) if tickets else 0
        except Exception:
            ticket_count = 0
        try:
            teams = await bot.db.fetchone(
                "SELECT COUNT(*) FROM verified_teams WHERE guild_id=? AND active=1", (guild_id,)
            )
            team_count = int(teams[0]) if teams else 0
        except Exception:
            team_count = 0
        try:
            commands = len(_tree_commands(bot))
        except Exception:
            commands = 0
        return {
            "id": guild.id,
            "name": guild.name,
            "icon": str(guild.icon.url) if guild.icon else None,
            "members": guild.member_count or len(getattr(guild, "members", ())),
            "channels": len(getattr(guild, "channels", ())),
            "roles": max(0, len(getattr(guild, "roles", ())) - 1),
            "open_tickets": ticket_count,
            "verified_teams": team_count,
            "commands": commands,
            "bot_latency_ms": round(float(getattr(bot, "latency", 0.0)) * 1000, 1),
        }

    @app.get("/api/guilds/{guild_id}/resources")
    async def resources(request: Request, guild_id: int):
        guild, _ = await authorized_guild(request, guild_id)
        return {
            "roles": [
                {"id": r.id, "name": r.name, "position": r.position, "managed": r.managed}
                for r in guild.roles
                if not r.is_default()
            ],
            "channels": [
                {"id": c.id, "name": c.name, "type": str(c.type), "position": getattr(c, "position", 0)}
                for c in guild.channels
                if hasattr(c, "name")
            ],
        }

    @app.get("/api/guilds/{guild_id}/commands")
    async def commands_list(request: Request, guild_id: int):
        await authorized_guild(request, guild_id)
        try:
            rows = await bot.db.fetchall(
                "SELECT * FROM dashboard_command_settings WHERE guild_id=?", (guild_id,)
            )
        except Exception:
            rows = []
        settings = {str(row["command_name"]): dict(row) for row in rows}
        output = []
        for command in _tree_commands(bot):
            row = settings.get(command["name"])
            output.append(
                {
                    **command,
                    "enabled": bool(row["enabled"]) if row else True,
                    "allowed_roles": _loads_ids(row["allowed_roles"]) if row else [],
                    "denied_roles": _loads_ids(row["denied_roles"]) if row else [],
                    "allowed_channels": _loads_ids(row["allowed_channels"]) if row else [],
                    "denied_channels": _loads_ids(row["denied_channels"]) if row else [],
                }
            )
        return {"commands": output}

    @app.put("/api/guilds/{guild_id}/commands/{command_name:path}")
    async def command_update(request: Request, guild_id: int, command_name: str):
        await authorized_guild(request, guild_id)
        data = await request.json()
        valid = {command["name"] for command in _tree_commands(bot)}
        if command_name not in valid:
            raise HTTPException(status_code=404, detail="الأمر غير موجود")
        await bot.db.execute(
            """INSERT INTO dashboard_command_settings
            (guild_id,command_name,enabled,allowed_roles,denied_roles,allowed_channels,denied_channels,updated_at)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(guild_id,command_name) DO UPDATE SET
              enabled=excluded.enabled,
              allowed_roles=excluded.allowed_roles,
              denied_roles=excluded.denied_roles,
              allowed_channels=excluded.allowed_channels,
              denied_channels=excluded.denied_channels,
              updated_at=excluded.updated_at""",
            (
                guild_id,
                command_name,
                1 if data.get("enabled", True) else 0,
                _json_ids(data.get("allowed_roles")),
                _json_ids(data.get("denied_roles")),
                _json_ids(data.get("allowed_channels")),
                _json_ids(data.get("denied_channels")),
                time.time(),
            ),
        )
        return {"ok": True}

    @app.get("/api/guilds/{guild_id}/shortcuts")
    async def shortcuts(request: Request, guild_id: int):
        await authorized_guild(request, guild_id)
        try:
            from cogs.shortcuts import DEFAULT_ALIASES, SHORTCUTS
        except Exception:
            DEFAULT_ALIASES, SHORTCUTS = {}, {}
        try:
            rows = await bot.db.fetchall(
                "SELECT * FROM dashboard_shortcut_settings WHERE guild_id=?", (guild_id,)
            )
        except Exception:
            rows = []
        settings = {str(row["shortcut_name"]): dict(row) for row in rows}
        output = []
        for key, label in SHORTCUTS.items():
            row = settings.get(key)
            output.append(
                {
                    "name": key,
                    "label": label,
                    "alias": (row.get("alias") if row else None) or DEFAULT_ALIASES.get(key, ""),
                    "enabled": bool(row["enabled"]) if row else True,
                    "allowed_roles": _loads_ids(row["allowed_roles"]) if row else [],
                    "denied_roles": _loads_ids(row["denied_roles"]) if row else [],
                    "allowed_channels": _loads_ids(row["allowed_channels"]) if row else [],
                    "denied_channels": _loads_ids(row["denied_channels"]) if row else [],
                }
            )
        return {"shortcuts": output}

    @app.put("/api/guilds/{guild_id}/shortcuts/{shortcut_name}")
    async def shortcut_update(request: Request, guild_id: int, shortcut_name: str):
        await authorized_guild(request, guild_id)
        data = await request.json()
        try:
            from cogs.shortcuts import SHORTCUTS
            valid = set(SHORTCUTS)
        except Exception:
            valid = set()
        if shortcut_name not in valid:
            raise HTTPException(status_code=404, detail="الاختصار غير موجود")
        await bot.db.execute(
            """INSERT INTO dashboard_shortcut_settings
            (guild_id,shortcut_name,alias,enabled,allowed_roles,denied_roles,allowed_channels,denied_channels,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(guild_id,shortcut_name) DO UPDATE SET
              alias=excluded.alias,
              enabled=excluded.enabled,
              allowed_roles=excluded.allowed_roles,
              denied_roles=excluded.denied_roles,
              allowed_channels=excluded.allowed_channels,
              denied_channels=excluded.denied_channels,
              updated_at=excluded.updated_at""",
            (
                guild_id,
                shortcut_name,
                str(data.get("alias", "")).strip() or None,
                1 if data.get("enabled", True) else 0,
                _json_ids(data.get("allowed_roles")),
                _json_ids(data.get("denied_roles")),
                _json_ids(data.get("allowed_channels")),
                _json_ids(data.get("denied_channels")),
                time.time(),
            ),
        )
        cog = bot.get_cog("Shortcuts")
        alias = str(data.get("alias", "")).strip()
        if cog and alias:
            if not alias.startswith("!"):
                alias = "!" + alias
            if " " not in alias:
                cog.set_alias(guild_id, shortcut_name, alias)
        return {"ok": True}

    @app.get("/api/guilds/{guild_id}/tickets")
    async def tickets(request: Request, guild_id: int):
        await authorized_guild(request, guild_id)
        try:
            panels = await bot.db.list_ticket_panels(guild_id)
        except Exception:
            panels = []
        try:
            rows = await bot.db.fetchall(
                "SELECT id,channel_id,user_id,status,claimed_by,created_at,closed_at FROM tickets WHERE guild_id=? ORDER BY id DESC LIMIT 100",
                (guild_id,),
            )
        except Exception:
            rows = []
        return {"panels": panels, "tickets": [dict(row) for row in rows]}

    @app.get("/api/guilds/{guild_id}/teams")
    async def teams(request: Request, guild_id: int):
        await authorized_guild(request, guild_id)
        try:
            rows = await bot.db.fetchall(
                "SELECT * FROM verified_teams WHERE guild_id=? AND active=1 ORDER BY team_type,id",
                (guild_id,),
            )
        except Exception:
            rows = []
        result = []
        for row in rows:
            item = dict(row)
            try:
                count = await bot.db.fetchone(
                    "SELECT COUNT(*) FROM team_members WHERE team_id=?", (row["id"],)
                )
                item["players"] = int(count[0]) if count else 0
            except Exception:
                item["players"] = 0
            result.append(item)
        return {"teams": result}

    return app


def _login_html(ready: bool) -> str:
    action = (
        '<a class="btn primary" href="/login">تسجيل الدخول بواسطة Discord</a>'
        if ready
        else '<div class="alert">⚠️ OAuth2 مازال ما متضبطش.<br><code>DISCORD_CLIENT_ID</code> و <code>DISCORD_CLIENT_SECRET</code> ضروريين.</div>'
    )
    return f"""<!doctype html><html lang='ar' dir='rtl'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Ader — Login</title>{_styles()}</head><body class='center'><main class='auth'><div class='brand'>ADER</div><h1>لوحة تحكم السيرفر</h1><p>دخل بحساب Discord باش تشوف غير السيرفرات اللي عندك فيها صلاحية الإدارة والبوت داخل فيها.</p>{action}</main></body></html>"""


def _error_html(title: str, message: str) -> str:
    return f"""<!doctype html><html lang='ar' dir='rtl'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Ader — Error</title>{_styles()}</head><body class='center'><main class='auth'><div class='brand'>ADER</div><h1>{html.escape(title)}</h1><p class='danger-text'>{html.escape(message)}</p><a class='btn primary' href='/'>العودة</a></main></body></html>"""


def _styles() -> str:
    return """<style>
:root{--bg:#070b14;--panel:#0f1728;--panel2:#141f34;--line:#25324b;--text:#f7f9fc;--muted:#9aa9c0;--accent:#5865f2;--accent2:#7c5cff;--ok:#43d17a;--danger:#ff6b7a;--shadow:0 20px 60px rgba(0,0,0,.28)}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 20% 0,#182544 0,transparent 34%),linear-gradient(145deg,#060a13,#0a1020 55%,#060a12);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,Tahoma,Arial,sans-serif;min-height:100vh}button,input,select{font:inherit}.center{display:grid;place-items:center;padding:24px}.auth{width:min(520px,100%);padding:36px;border:1px solid var(--line);border-radius:28px;background:rgba(15,23,40,.92);box-shadow:var(--shadow);text-align:center}.brand{font-size:42px;font-weight:1000;letter-spacing:.08em;background:linear-gradient(90deg,#8b80ff,#59c7ff);color:transparent;background-clip:text;-webkit-background-clip:text}.auth h1{margin:12px 0 8px}.auth p{color:var(--muted);line-height:1.9}.btn{display:inline-flex;align-items:center;justify-content:center;border:0;border-radius:12px;padding:11px 16px;text-decoration:none;color:#fff;font-weight:800;cursor:pointer}.primary{background:linear-gradient(135deg,var(--accent),var(--accent2))}.alert{margin-top:18px;padding:14px;border:1px solid #805d28;background:#2b2110;border-radius:13px;line-height:1.8}.danger-text{color:#ff9aa5}.app{display:grid;grid-template-columns:260px 1fr;min-height:100vh}.side{position:sticky;top:0;height:100vh;padding:20px;border-left:1px solid var(--line);background:rgba(8,13,24,.9);backdrop-filter:blur(12px)}.side .brand{font-size:30px;margin:2px 4px 22px}.nav{display:grid;gap:8px}.nav button{width:100%;text-align:right;background:transparent;color:var(--muted);border:1px solid transparent;border-radius:11px;padding:11px 12px;cursor:pointer}.nav button:hover,.nav button.active{background:var(--panel2);color:#fff;border-color:var(--line)}.user{position:absolute;bottom:18px;left:18px;right:18px;padding:12px;border:1px solid var(--line);background:var(--panel);border-radius:14px}.user small{color:var(--muted);display:block;margin-top:4px}.logout{float:left;color:#ff9aa5;text-decoration:none;font-size:12px}.main{padding:26px;min-width:0}.top{display:flex;gap:14px;align-items:center;justify-content:space-between;margin-bottom:20px}.top h1{font-size:28px;margin:0}.select{min-width:280px;background:var(--panel);color:#fff;border:1px solid var(--line);border-radius:12px;padding:11px}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.card{background:linear-gradient(180deg,rgba(20,31,52,.94),rgba(12,19,33,.94));border:1px solid var(--line);border-radius:18px;padding:18px;box-shadow:var(--shadow)}.card h3{margin:0 0 10px;font-size:14px;color:var(--muted)}.metric{font-size:31px;font-weight:950}.muted{color:var(--muted)}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:16px;background:var(--panel)}table{width:100%;border-collapse:collapse;min-width:720px}th,td{padding:12px 14px;border-bottom:1px solid var(--line);text-align:right}th{color:var(--muted);font-size:12px}tr:last-child td{border-bottom:0}.toggle{width:46px;height:25px;border-radius:20px;border:0;background:#3a465d;position:relative;cursor:pointer}.toggle::after{content:'';position:absolute;top:4px;right:4px;width:17px;height:17px;background:#fff;border-radius:50%;transition:.16s}.toggle.on{background:var(--ok)}.toggle.on::after{right:25px}.toolbar{display:flex;gap:10px;align-items:center;justify-content:space-between;margin-bottom:14px}.search{width:min(330px,100%);background:var(--panel);border:1px solid var(--line);color:#fff;border-radius:11px;padding:10px}.pill{display:inline-flex;padding:4px 8px;border-radius:999px;background:#17233b;color:#b7c9ff;font-size:12px}.empty{padding:32px;text-align:center;color:var(--muted)}.loading{padding:36px;text-align:center;color:var(--muted)}.error{padding:18px;border:1px solid #66313a;background:#271318;border-radius:14px;color:#ffb3ba}.toast{position:fixed;left:22px;bottom:22px;max-width:360px;padding:13px 15px;border:1px solid var(--line);background:#111b2d;border-radius:12px;box-shadow:var(--shadow);z-index:10}.hidden{display:none!important}@media(max-width:980px){.app{grid-template-columns:1fr}.side{position:relative;height:auto;border-left:0;border-bottom:1px solid var(--line)}.user{position:static;margin-top:16px}.nav{grid-template-columns:repeat(3,minmax(0,1fr))}.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.top{flex-direction:column;align-items:stretch}.select{min-width:0;width:100%}}@media(max-width:600px){.main{padding:16px}.nav{grid-template-columns:repeat(2,minmax(0,1fr))}.grid{grid-template-columns:1fr}.auth{padding:24px}}
</style>"""


def _dashboard_html() -> str:
    script = r"""
<script>
const state={guilds:[],guild:null,tab:'overview',data:{},filter:''};
const $=s=>document.querySelector(s);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(url,opts={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opts.headers||{})},...opts});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||`HTTP ${r.status}`);return d}
function toast(msg,ok=true){const el=document.createElement('div');el.className='toast';el.textContent=msg;document.body.appendChild(el);setTimeout(()=>el.remove(),3200)}
function shell(user){document.body.innerHTML=`<div class='app'><aside class='side'><div class='brand'>ADER</div><div id='nav' class='nav'></div><div class='user'><a class='logout' href='/logout'>خروج</a><strong>${esc(user.global_name||user.username)}</strong><small>لوحة تحكم Discord</small></div></aside><main class='main'><div class='top'><div><h1 id='title'>نظرة عامة</h1><div id='sub' class='muted'>اختار السيرفر من اللائحة</div></div><select id='guild' class='select'></select></div><section id='view'></section></main></div>`;renderNav();$('#guild').addEventListener('change',async e=>{state.guild=e.target.value;await loadTab(true)});}
function renderNav(){const items=[['overview','الرئيسية'],['commands','الأوامر'],['shortcuts','الاختصارات'],['tickets','التذاكر'],['teams','الفرق'],['resources','الرومات والرتب']];$('#nav').innerHTML=items.map(([id,label])=>`<button data-tab='${id}' class='${state.tab===id?'active':''}'>${label}</button>`).join('');document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{state.tab=b.dataset.tab;state.filter='';renderNav();loadTab(true)});}
function fillGuilds(){const s=$('#guild');s.innerHTML=state.guilds.map(g=>`<option value='${g.id}'>${esc(g.name)}</option>`).join('');state.guild=state.guilds[0]?.id||null;if(state.guild)s.value=state.guild;}
async function boot(){try{const me=await api('/api/me');if(!me.logged_in){location.href='/';return}shell(me.user);const g=await api('/api/guilds');state.guilds=g.guilds||[];fillGuilds();if(!state.guilds.length){$('#view').innerHTML=`<div class='card empty'>ماكاين حتى سيرفر متاح فحسابك والبوت داخل فيه.</div>`;return}await loadTab(false)}catch(e){document.body.innerHTML=`<main class='auth'><div class='brand'>ADER</div><h1>تعذر تحميل الداشبورد</h1><p class='danger-text'>${esc(e.message)}</p><a class='btn primary' href='/'>إعادة المحاولة</a></main>`}}
function setLoading(){ $('#view').innerHTML=`<div class='loading'>جاري التحميل...</div>`; }
async function loadTab(reset){if(!state.guild){return}if(reset)setLoading();$('#title').textContent={overview:'نظرة عامة',commands:'الأوامر',shortcuts:'الاختصارات',tickets:'التذاكر',teams:'الفرق',resources:'الرومات والرتب'}[state.tab]||'Ader';try{const d=state.tab==='overview'?await api(`/api/guilds/${state.guild}/overview`):await api(`/api/guilds/${state.guild}/${state.tab}`);state.data[state.tab]=d;render();}catch(e){$('#view').innerHTML=`<div class='error'>${esc(e.message)}</div>`}}
function render(){const d=state.data[state.tab]||{};if(state.tab==='overview')return renderOverview(d);if(state.tab==='commands')return renderCommands(d.commands||[]);if(state.tab==='shortcuts')return renderShortcuts(d.shortcuts||[]);if(state.tab==='tickets')return renderTickets(d);if(state.tab==='teams')return renderTeams(d.teams||[]);if(state.tab==='resources')return renderResources(d)}
function renderOverview(d){$('#sub').textContent=esc(d.name||'');$('#view').innerHTML=`<div class='grid'>${[['الأعضاء',d.members],['القنوات',d.channels],['الرتب',d.roles],['Ping',`${d.bot_latency_ms??0} ms`]].map(x=>`<div class='card'><h3>${x[0]}</h3><div class='metric'>${esc(x[1])}</div></div>`).join('')}</div><div style='height:14px'></div><div class='grid'>${[['التذاكر المفتوحة',d.open_tickets],['الفرق الموثقة',d.verified_teams],['أوامر Slash',d.commands],['حالة البوت','● متصل']].map(x=>`<div class='card'><h3>${x[0]}</h3><div class='metric'>${esc(x[1])}</div></div>`).join('')}</div>`}
function renderCommands(rows){tableSettings('commands',rows,'الأوامر','اسم الأمر','description')}
function renderShortcuts(rows){tableSettings('shortcuts',rows,'الاختصارات','الاختصار','label')}
function tableSettings(kind,rows,title,nameField,descField){const filtered=rows.filter(x=>`${x[nameField]||''} ${x[descField]||''}`.toLowerCase().includes(state.filter.toLowerCase()));$('#sub').textContent=`${rows.length} عنصر`;$('#view').innerHTML=`<div class='card'><div class='toolbar'><div class='muted'>فعّل أو عطّل ${title.toLowerCase()}</div><input id='filter' class='search' placeholder='بحث...' value='${esc(state.filter)}'></div><div class='table-wrap'><table><thead><tr><th>${nameField==='nameField'?'الاسم':'الاسم'}</th><th>الوصف</th><th>الحالة</th></tr></thead><tbody>${filtered.length?filtered.map((x,i)=>`<tr><td><strong>${esc(x[nameField])}</strong></td><td class='muted'>${esc(x[descField]||x.description||'')}</td><td><button class='toggle ${x.enabled?'on':''}' data-i='${rows.indexOf(x)}'></button></td></tr>`).join(''):`<tr><td colspan='3'><div class='empty'>ما لقيت والو.</div></td></tr>`}</tbody></table></div></div>`;$('#filter').oninput=e=>{state.filter=e.target.value;render()};document.querySelectorAll('.toggle').forEach(b=>b.onclick=async()=>{const x=rows[Number(b.dataset.i)];try{const body={enabled:!x.enabled};if(kind==='commands')body.allowed_roles=x.allowed_roles||[],body.denied_roles=x.denied_roles||[],body.allowed_channels=x.allowed_channels||[],body.denied_channels=x.denied_channels||[];else body.alias=x.alias||'';await api(`/api/guilds/${state.guild}/${kind}/${encodeURIComponent(x.name)}`,{method:'PUT',body:JSON.stringify(body)});x.enabled=!x.enabled;render();toast('تم حفظ التغيير')}catch(e){toast(e.message,false)}})}
function renderTickets(d){const rows=d.tickets||[];$('#sub').textContent=`${rows.length} آخر تذكرة`;$('#view').innerHTML=`<div class='card'><h2 style='margin-top:0'>لوحات التذاكر</h2><p class='muted'>${(d.panels||[]).length} لوحة محفوظة.</p></div><div style='height:14px'></div><div class='card'><h2 style='margin-top:0'>آخر التذاكر</h2><div class='table-wrap'><table><thead><tr><th>#</th><th>القناة</th><th>العضو</th><th>الحالة</th></tr></thead><tbody>${rows.length?rows.map(x=>`<tr><td>${esc(x.id)}</td><td>${esc(x.channel_id||'-')}</td><td>${esc(x.user_id||'-')}</td><td><span class='pill'>${esc(x.status||'-')}</span></td></tr>`).join(''):`<tr><td colspan='4'><div class='empty'>ماكايناش تذاكر.</div></td></tr>`}</tbody></table></div></div>`}
function renderTeams(rows){$('#sub').textContent=`${rows.length} فريق`;$('#view').innerHTML=`<div class='grid'>${rows.length?rows.map(x=>`<div class='card'><h3>${esc(x.team_type||'Team')}</h3><div style='font-size:20px;font-weight:900'>${esc(x.name||'بدون اسم')}</div><p class='muted'>${esc(x.players??0)} لاعبين</p></div>`).join(''):`<div class='card empty'>ماكايناش فرق موثقة.</div>`}</div>`}
function renderResources(d){$('#sub').textContent=`${(d.channels||[]).length} رومات · ${(d.roles||[]).length} رتب`;$('#view').innerHTML=`<div class='grid'><div class='card'><h3>الرومات</h3><div class='metric'>${(d.channels||[]).length}</div></div><div class='card'><h3>الرتب</h3><div class='metric'>${(d.roles||[]).length}</div></div></div><div style='height:14px'></div><div class='card'><h2 style='margin-top:0'>الرومات</h2><div class='table-wrap'><table><thead><tr><th>الاسم</th><th>النوع</th><th>الترتيب</th></tr></thead><tbody>${(d.channels||[]).map(x=>`<tr><td>#${esc(x.name)}</td><td>${esc(x.type)}</td><td>${esc(x.position)}</td></tr>`).join('')}</tbody></table></div></div>`}
boot();
</script>"""
    return """<!doctype html><html lang='ar' dir='rtl'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Ader Dashboard</title>""" + _styles() + "</head><body><div class='loading'>جاري فتح Ader...</div>" + script + "</body></html>"


# asyncio is imported at module level after the route definitions are assembled
# to keep the public API of this module simple for older Python runners.
import asyncio  # noqa: E402
