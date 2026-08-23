"""Production dashboard for Ader.

Self-contained FastAPI dashboard with a small signed session cookie and
server-side session state. Discord OAuth2 is handled directly by the dashboard
without a frontend build or external assets.
"""
from __future__ import annotations

import asyncio
import html
import json
import os
import secrets
import time
from typing import Any
from urllib.parse import quote

import aiohttp
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

MANAGE_GUILD = 0x20
ADMINISTRATOR = 0x8

# Process-local state avoids putting the entire Discord guild list in the browser cookie.
_SESSIONS: dict[str, dict[str, Any]] = {}
_OAUTH_STATES: dict[str, float] = {}


def _guild_is_managed(item: dict[str, Any]) -> bool:
    try:
        permissions = int(item.get("permissions", 0) or 0)
    except (TypeError, ValueError):
        permissions = 0
    return bool(permissions & ADMINISTRATOR or permissions & MANAGE_GUILD)


def _safe_error(exc: Exception) -> str:
    return str(exc)[:500]


def _configured_public_url(cfg: dict[str, Any]) -> str:
    value = os.getenv("DASHBOARD_PUBLIC_URL", "").strip() or str(cfg.get("public_url", "")).strip()
    return value.rstrip("/")


def _redirect_uri(request: Request, cfg: dict[str, Any]) -> str:
    explicit = os.getenv("DASHBOARD_REDIRECT_URI", "").strip()
    if explicit:
        return explicit.rstrip("/")
    public_url = _configured_public_url(cfg)
    if public_url:
        return f"{public_url}/callback"
    return f"{str(request.base_url).rstrip('/')}/callback"


def _cleanup_state() -> None:
    now = time.time()
    for key, expires in list(_OAUTH_STATES.items()):
        if expires <= now:
            _OAUTH_STATES.pop(key, None)
    for key, session in list(_SESSIONS.items()):
        if float(session.get("expires_at", 0)) <= now:
            _SESSIONS.pop(key, None)


def _session_for(request: Request) -> dict[str, Any] | None:
    _cleanup_state()
    sid = request.session.get("sid")
    if not sid:
        return None
    session = _SESSIONS.get(str(sid))
    if not session:
        request.session.clear()
        return None
    session["expires_at"] = time.time() + 86400
    return session


def _json_ids(value: Any) -> str:
    if value is None:
        return "[]"
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            value = []
    if not isinstance(value, (list, tuple, set)):
        value = []
    result = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            pass
    return json.dumps(sorted(set(result)), separators=(",", ":"))


def _loads_ids(value: Any) -> list[int]:
    try:
        parsed = json.loads(value or "[]") if isinstance(value, str) else (value or [])
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    result = []
    for item in parsed:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            pass
    return result


def _tree_commands(bot) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    def walk(commands, parent=""):
        for command in commands:
            name = f"{parent} {command.name}".strip()
            children = getattr(command, "commands", None)
            if children:
                walk(children, name)
            else:
                result.append({
                    "name": name,
                    "description": str(getattr(command, "description", "") or ""),
                    "type": str(getattr(command, "type", "chat_input")),
                })

    walk(bot.tree.get_commands())
    return result


def create_app(bot) -> FastAPI:
    cfg = bot.config.get("web", {}) or {}
    app = FastAPI(title="Ader Dashboard", version="4.0.0", docs_url="/api/docs", redoc_url=None)

    session_secret = os.getenv("DASHBOARD_SESSION_SECRET", "").strip() or str(cfg.get("session_secret", "")).strip()
    if len(session_secret) < 32:
        session_secret = secrets.token_urlsafe(48)

    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret,
        same_site="lax",
        # Do not force Secure based solely on a possibly stale public_url value.
        # The reverse proxy already terminates HTTPS in the normal deployment.
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
        return bool(os.getenv("DISCORD_CLIENT_ID", "").strip() and os.getenv("DISCORD_CLIENT_SECRET", "").strip())

    async def require_session(request: Request) -> dict[str, Any]:
        session = _session_for(request)
        if not session:
            raise HTTPException(status_code=401, detail="تسجيل الدخول مطلوب")
        return session

    async def require_guild(request: Request, guild_id: int):
        session = await require_session(request)
        managed = session.get("managed_guilds", {})
        guild_data = managed.get(str(guild_id))
        if not isinstance(guild_data, dict) or not _guild_is_managed(guild_data):
            raise HTTPException(status_code=403, detail="لا تملك صلاحية إدارة هذا الخادم")
        guild = bot.get_guild(guild_id)
        if guild is None:
            raise HTTPException(status_code=404, detail="البوت غير متصل بهذا الخادم حالياً")
        return guild, session

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
        if not _session_for(request):
            return HTMLResponse(_login_html(oauth_ready()), headers={"Cache-Control": "no-store"})
        return HTMLResponse(_dashboard_html(), headers={"Cache-Control": "no-store"})

    @app.get("/login")
    async def login(request: Request):
        _cleanup_state()
        if not oauth_ready():
            return HTMLResponse(_error_html("إعدادات OAuth2 ناقصة", "خاصك DISCORD_CLIENT_ID و DISCORD_CLIENT_SECRET في متغيرات البيئة."), status_code=503)
        state = secrets.token_urlsafe(32)
        _OAUTH_STATES[state] = time.time() + 600
        request.session["oauth_state"] = state
        redirect_uri = _redirect_uri(request, cfg)
        url = (
            "https://discord.com/oauth2/authorize?client_id=" + quote(os.environ["DISCORD_CLIENT_ID"], safe="")
            + "&response_type=code&redirect_uri=" + quote(redirect_uri, safe="")
            + "&scope=identify%20guilds"
            + "&state=" + quote(state, safe="")
        )
        return RedirectResponse(url, status_code=302)

    @app.get("/callback")
    async def callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
        _cleanup_state()
        if error:
            return HTMLResponse(_error_html("تم إلغاء تسجيل الدخول", error_description or error), status_code=400)
        expected = request.session.get("oauth_state")
        if not code or not state or not expected or state != expected or state not in _OAUTH_STATES:
            request.session.clear()
            return HTMLResponse(_error_html("فشل تسجيل الدخول", "رابط OAuth غير صالح أو انتهت صلاحيته. عاود تسجيل الدخول من زر Discord."), status_code=400)
        _OAUTH_STATES.pop(state, None)
        redirect_uri = _redirect_uri(request, cfg)
        data = {
            "client_id": os.environ["DISCORD_CLIENT_ID"],
            "client_secret": os.environ["DISCORD_CLIENT_SECRET"],
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post("https://discord.com/api/oauth2/token", data=data) as response:
                    token = await response.json(content_type=None)
                    if response.status >= 400:
                        message = token.get("error_description") or token.get("error") or "تحقق من OAuth2 و Redirect URI."
                        return HTMLResponse(_error_html("فشل OAuth2", str(message)), status_code=400)
                access_token = token.get("access_token") if isinstance(token, dict) else None
                if not access_token:
                    return HTMLResponse(_error_html("فشل OAuth2", "Discord لم يرجع access token."), status_code=400)
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

        managed = {}
        for item in user_guilds:
            if not isinstance(item, dict) or not _guild_is_managed(item):
                continue
            try:
                guild_id = int(item["id"])
                permissions = int(item.get("permissions", 0) or 0)
            except (KeyError, TypeError, ValueError):
                continue
            managed[str(guild_id)] = {
                "id": guild_id,
                "name": str(item.get("name") or "Unknown Server"),
                "icon": item.get("icon"),
                "permissions": permissions,
                "administrator": bool(permissions & ADMINISTRATOR),
                "manage_guild": bool(permissions & MANAGE_GUILD),
            }

        sid = secrets.token_urlsafe(32)
        _SESSIONS[sid] = {
            "expires_at": time.time() + 86400,
            "access_token": access_token,
            "discord_user": {
                "id": int(user["id"]),
                "username": str(user.get("username") or "Discord User"),
                "global_name": str(user.get("global_name") or user.get("username") or "Discord User"),
                "avatar": user.get("avatar"),
            },
            "managed_guilds": managed,
        }
        request.session.clear()
        request.session["sid"] = sid
        return RedirectResponse("/", status_code=302)

    @app.get("/logout")
    async def logout(request: Request):
        sid = request.session.get("sid")
        if sid:
            _SESSIONS.pop(str(sid), None)
        request.session.clear()
        return RedirectResponse("/")

    @app.get("/api/me")
    async def me(request: Request):
        session = _session_for(request)
        return {"user": session.get("discord_user") if session else None, "logged_in": bool(session)}

    @app.get("/api/guilds")
    async def guilds(request: Request):
        session = await require_session(request)
        return {"guilds": list((session.get("managed_guilds") or {}).values())}

    @app.get("/api/guilds/{guild_id}/overview")
    async def overview(request: Request, guild_id: int):
        guild, _ = await require_guild(request, guild_id)
        try:
            row = await bot.db.fetchone("SELECT COUNT(*) AS n FROM tickets WHERE guild_id=? AND status='open'", (guild_id,))
            tickets = int(row["n"]) if row else 0
        except Exception:
            tickets = 0
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
            "open_tickets": tickets,
            "commands": commands,
            "bot_latency_ms": round(float(getattr(bot, "latency", 0.0)) * 1000, 1),
        }

    @app.get("/api/guilds/{guild_id}/resources")
    async def resources(request: Request, guild_id: int):
        guild, _ = await require_guild(request, guild_id)
        return {
            "roles": [{"id": r.id, "name": r.name, "position": r.position, "managed": r.managed} for r in guild.roles if not r.is_default()],
            "channels": [{"id": c.id, "name": c.name, "type": str(c.type), "position": getattr(c, "position", 0)} for c in guild.channels if hasattr(c, "name")],
        }

    @app.get("/api/guilds/{guild_id}/commands")
    async def commands_list(request: Request, guild_id: int):
        await require_guild(request, guild_id)
        try:
            rows = await bot.db.fetchall("SELECT * FROM dashboard_command_settings WHERE guild_id=?", (guild_id,))
        except Exception:
            rows = []
        settings = {str(row["command_name"]): dict(row) for row in rows}
        output = []
        for command in _tree_commands(bot):
            row = settings.get(command["name"])
            output.append({
                **command,
                "enabled": bool(row["enabled"]) if row else True,
                "allowed_roles": _loads_ids(row["allowed_roles"]) if row else [],
                "denied_roles": _loads_ids(row["denied_roles"]) if row else [],
                "allowed_channels": _loads_ids(row["allowed_channels"]) if row else [],
                "denied_channels": _loads_ids(row["denied_channels"]) if row else [],
            })
        return {"commands": output}

    @app.put("/api/guilds/{guild_id}/commands/{command_name:path}")
    async def command_update(request: Request, guild_id: int, command_name: str):
        await require_guild(request, guild_id)
        data = await request.json()
        valid = {command["name"] for command in _tree_commands(bot)}
        if command_name not in valid:
            raise HTTPException(status_code=404, detail="الأمر غير موجود")
        await bot.db.execute(
            """INSERT INTO dashboard_command_settings(guild_id,command_name,enabled,allowed_roles,denied_roles,allowed_channels,denied_channels,updated_at)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(guild_id,command_name) DO UPDATE SET enabled=excluded.enabled,allowed_roles=excluded.allowed_roles,denied_roles=excluded.denied_roles,allowed_channels=excluded.allowed_channels,denied_channels=excluded.denied_channels,updated_at=excluded.updated_at""",
            (guild_id, command_name, 1 if data.get("enabled", True) else 0, _json_ids(data.get("allowed_roles")), _json_ids(data.get("denied_roles")), _json_ids(data.get("allowed_channels")), _json_ids(data.get("denied_channels")), time.time()),
        )
        return {"ok": True}

    @app.get("/api/guilds/{guild_id}/shortcuts")
    async def shortcuts(request: Request, guild_id: int):
        await require_guild(request, guild_id)
        try:
            from cogs.shortcuts import DEFAULT_ALIASES, SHORTCUTS
        except Exception:
            DEFAULT_ALIASES, SHORTCUTS = {}, {}
        rows = await bot.db.fetchall("SELECT * FROM dashboard_shortcut_settings WHERE guild_id=?", (guild_id,))
        settings = {str(row["shortcut_name"]): dict(row) for row in rows}
        return {"shortcuts": [
            {
                "name": key,
                "label": label,
                "alias": (settings.get(key, {}).get("alias") or DEFAULT_ALIASES.get(key, "")),
                "enabled": bool(settings.get(key, {}).get("enabled", 1)),
                "allowed_roles": _loads_ids(settings.get(key, {}).get("allowed_roles")) if key in settings else [],
                "denied_roles": _loads_ids(settings.get(key, {}).get("denied_roles")) if key in settings else [],
                "allowed_channels": _loads_ids(settings.get(key, {}).get("allowed_channels")) if key in settings else [],
                "denied_channels": _loads_ids(settings.get(key, {}).get("denied_channels")) if key in settings else [],
            }
            for key, label in SHORTCUTS.items()
        ]}

    return app


def _login_html(ready: bool) -> str:
    action = '<a class="btn primary" href="/login">🔐 تسجيل الدخول بواسطة Discord</a>' if ready else '<div class="alert">⚠️ OAuth2 مازال ما متضبطش.<br><code>DISCORD_CLIENT_ID</code> و <code>DISCORD_CLIENT_SECRET</code> ضروريين.</div>'
    return f"""<!doctype html><html lang='ar' dir='rtl'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Ader Dashboard</title>{_styles()}</head><body class='center'><main class='auth'><div class='brand'>ADER</div><h1>لوحة تحكم السيرفر</h1><p>سجل الدخول بواسطة Discord لاختيار السيرفرات اللي عندك فيها Administrator أو Manage Server.</p>{action}</main></body></html>"""


def _dashboard_html() -> str:
    return """<!doctype html><html lang='ar' dir='rtl'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Ader Dashboard</title>""" + _styles() + """</head><body><main class='shell'><header class='top'><div><div class='brand small'>ADER</div><div class='muted'>Dashboard</div></div><a class='btn' href='/logout'>تسجيل الخروج</a></header><section class='grid'><div class='panel'><h2>السيرفرات</h2><div id='guilds' class='list'>جاري التحميل...</div></div><div class='panel'><h2>Overview</h2><div id='overview' class='cards'>اختار سيرفر.</div></div></section><section class='panel'><h2>الأوامر</h2><div id='commands' class='muted'>اختار سيرفر باش تشوف الأوامر.</div></section></main><script>
const $=s=>document.querySelector(s);let current=null;
async function api(u,o){const r=await fetch(u,o);if(r.status===401){location.href='/';return null;}if(!r.ok){const t=await r.text();throw new Error(t||('HTTP '+r.status));}return r.json();}
async function loadGuilds(){const d=await api('/api/guilds');if(!d)return;const root=$('#guilds');root.innerHTML='';for(const g of d.guilds){const b=document.createElement('button');b.className='guild';b.textContent=g.name;b.onclick=()=>selectGuild(g.id,g.name);root.appendChild(b);}if(!d.guilds.length)root.innerHTML='<div class="muted">ما عندك حتى سيرفر متاح للإدارة أو البوت ما داخلش فيه.</div>';}
async function selectGuild(id,name){current=id;$('#overview').textContent='جاري التحميل...';$('#commands').textContent='جاري التحميل...';const [o,c]=await Promise.all([api(`/api/guilds/${id}/overview`),api(`/api/guilds/${id}/commands`)]);$('#overview').innerHTML=`<div class="card"><b>${escapeHtml(o.name)}</b><span>${o.members} أعضاء</span></div><div class="card"><b>${o.channels}</b><span>رومات</span></div><div class="card"><b>${o.roles}</b><span>رتب</span></div><div class="card"><b>${o.open_tickets}</b><span>تذاكر مفتوحة</span></div>`;$('#commands').innerHTML=c.commands.map(x=>`<div class="cmd"><b>/${escapeHtml(x.name)}</b><span>${escapeHtml(x.description)}</span></div>`).join('');}
function escapeHtml(v){return String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
loadGuilds().catch(e=>{$('#guilds').textContent='❌ '+e.message;});
</script></body></html>"""


def _error_html(title: str, message: str) -> str:
    return f"""<!doctype html><html lang='ar' dir='rtl'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Ader — Error</title>{_styles()}</head><body class='center'><main class='auth'><div class='brand'>ADER</div><h1>{html.escape(title)}</h1><p class='danger-text'>{html.escape(message)}</p><a class='btn primary' href='/'>العودة</a></main></body></html>"""


def _styles() -> str:
    return """<style>
:root{--bg:#070b14;--panel:#0f1728;--panel2:#141f34;--line:#25324b;--text:#f7f9fc;--muted:#9aa9c0;--accent:#5865f2;--accent2:#7c5cff;--danger:#ff6b7a}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 20% 0,#182544 0,transparent 34%),linear-gradient(145deg,#060a13,#0a1020 55%,#060a12);color:var(--text);font-family:Inter,system-ui,-apple-system,Segoe UI,Tahoma,Arial,sans-serif;min-height:100vh}.center{display:grid;place-items:center;padding:24px}.auth{width:min(520px,100%);padding:36px;border:1px solid var(--line);border-radius:28px;background:rgba(15,23,40,.94);text-align:center}.brand{font-size:42px;font-weight:1000;letter-spacing:.08em;background:linear-gradient(90deg,#8b80ff,#59c7ff);color:transparent;background-clip:text;-webkit-background-clip:text}.brand.small{font-size:28px}.auth h1{margin:12px 0 8px}.auth p{color:var(--muted);line-height:1.9}.btn{display:inline-flex;align-items:center;justify-content:center;border:1px solid var(--line);border-radius:12px;padding:10px 15px;text-decoration:none;color:#fff;font-weight:800;background:#151f34}.btn.primary{background:linear-gradient(135deg,var(--accent),var(--accent2));border:0}.alert{margin-top:18px;padding:14px;border:1px solid #805d28;background:#2b2110;border-radius:13px;line-height:1.8}.danger-text{color:#ff9aa5}.muted{color:var(--muted)}.shell{max-width:1180px;margin:0 auto;padding:22px}.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.grid{display:grid;grid-template-columns:300px 1fr;gap:18px}.panel{padding:18px;border:1px solid var(--line);border-radius:20px;background:rgba(15,23,40,.92);margin-bottom:18px}.panel h2{margin-top:0}.list{display:grid;gap:8px}.guild{border:1px solid var(--line);border-radius:12px;padding:12px;text-align:right;background:var(--panel2);color:#fff;cursor:pointer}.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.card{padding:14px;border:1px solid var(--line);border-radius:14px;background:var(--panel2);display:grid;gap:5px}.cmd{padding:11px 0;border-bottom:1px solid var(--line);display:flex;gap:12px}.cmd:last-child{border-bottom:0}@media(max-width:760px){.grid{grid-template-columns:1fr}.cards{grid-template-columns:repeat(2,minmax(0,1fr))}.shell{padding:12px}}
</style>"""
