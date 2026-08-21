"""Nova Aro dashboard API and Discord OAuth2 dashboard."""
from __future__ import annotations

import json
import os
import time
from typing import Any

import aiohttp
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware


def _json_ids(value: Any) -> str:
    if value is None:
        return "[]"
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = []
    return json.dumps([int(x) for x in value], separators=(",", ":"))


def _tree_commands(bot):
    result = []
    def walk(commands, parent=""):
        for cmd in commands:
            name = f"{parent} {cmd.name}".strip()
            if hasattr(cmd, "commands") and cmd.commands:
                walk(cmd.commands, name)
            else:
                result.append({
                    "name": name,
                    "description": getattr(cmd, "description", "") or "",
                    "type": str(getattr(cmd, "type", "chat_input")),
                })
    walk(bot.tree.get_commands())
    return result


def create_app(bot) -> FastAPI:
    cfg = bot.config.get("web", {}) or {}
    app = FastAPI(title="Nova Aro", version="2.0.0", docs_url="/api/docs")
    secret = os.getenv("DASHBOARD_SESSION_SECRET", "") or str(cfg.get("session_secret", ""))
    if not secret:
        secret = os.getenv("DISCORD_BOT_TOKEN", "nova-aro-change-this")
    app.add_middleware(SessionMiddleware, secret_key=secret, same_site="lax", https_only=False, max_age=86400)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.get("cors_origins", ["*"]),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def oauth_ready() -> bool:
        return bool(os.getenv("DISCORD_CLIENT_ID") and os.getenv("DISCORD_CLIENT_SECRET"))

    async def session_user(request: Request):
        user = request.session.get("discord_user")
        if not user:
            raise HTTPException(status_code=401, detail="تسجيل الدخول مطلوب")
        return user

    async def authorized_guild(request: Request, guild_id: int):
        user = await session_user(request)
        guilds = request.session.get("managed_guilds", {}) or {}
        session_guild = guilds.get(str(guild_id))
        if not session_guild:
            raise HTTPException(status_code=403, detail="لا تملك صلاحية إدارة هذا الخادم")

        # Do not depend exclusively on the local Discord.py guild cache. After
        # a restart, or in larger bots, the guild can be reachable but not yet
        # present in cache. fetch_guild() confirms that the bot is actually in
        # the server and prevents the dashboard from falsely showing an access
        # error.
        guild = bot.get_guild(guild_id)
        if guild is None:
            try:
                guild = await bot.fetch_guild(guild_id)
            except Exception as exc:
                raise HTTPException(
                    status_code=404,
                    detail="الخادم غير موجود أو البوت غير متصل به حالياً",
                ) from exc

        # The managed_guilds session is built from Discord OAuth2's /users/@me/guilds
        # endpoint and is already filtered to Administrator/Manage Server. Accept
        # both permission bits here, including an explicitly stored admin flag.
        try:
            permissions = int(session_guild.get("permissions", 0) or 0)
        except (TypeError, ValueError):
            permissions = 0
        is_manager = bool(permissions & 0x8 or permissions & 0x20 or session_guild.get("administrator"))
        if not is_manager:
            raise HTTPException(status_code=403, detail="لا تملك صلاحية إدارة هذا الخادم")

        return guild, user

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        # Render a server-side login screen for unauthenticated visitors.
        # This prevents a browser/JS failure from ever producing a blank page.
        if not request.session.get("discord_user"):
            return HTMLResponse(_login_html(oauth_ready()))
        return HTMLResponse(_dashboard_html())

    @app.get("/login")
    async def login():
        if not oauth_ready():
            return HTMLResponse(
                _oauth_error_html("إعدادات Discord OAuth2 ناقصة", "أضف DISCORD_CLIENT_ID و DISCORD_CLIENT_SECRET في متغيرات البيئة ثم أعد تشغيل Ader."),
                status_code=503,
            )
        redirect_uri = os.getenv("DASHBOARD_REDIRECT_URI", "")
        if not redirect_uri:
            redirect_uri = "/callback"
        from urllib.parse import quote
        url = (
            "https://discord.com/oauth2/authorize?client_id=" + os.environ["DISCORD_CLIENT_ID"]
            + "&response_type=code&redirect_uri=" + quote(redirect_uri, safe="")
            + "&scope=identify%20guilds"
        )
        return RedirectResponse(url)

    @app.get("/callback")
    async def callback(request: Request, code: str = ""):
        if not oauth_ready() or not code:
            return RedirectResponse("/")
        redirect_uri = os.getenv("DASHBOARD_REDIRECT_URI", "")
        if not redirect_uri:
            redirect_uri = str(request.base_url).rstrip("/") + "/callback"
        data = {
            "client_id": os.environ["DISCORD_CLIENT_ID"],
            "client_secret": os.environ["DISCORD_CLIENT_SECRET"],
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post("https://discord.com/api/oauth2/token", data=data) as r:
                    token = await r.json()
                if "access_token" not in token:
                    return HTMLResponse(_oauth_error_html("فشل تسجيل الدخول", "تحقق من Discord OAuth2 و Redirect URI."), status_code=400)
                headers = {"Authorization": f"Bearer {token['access_token']}"}
                async with s.get("https://discord.com/api/users/@me", headers=headers) as r:
                    user = await r.json()
                async with s.get("https://discord.com/api/users/@me/guilds", headers=headers) as r:
                    user_guilds = await r.json()
        except Exception as exc:
            return HTMLResponse(_oauth_error_html("تعذر الاتصال بـDiscord", str(exc)), status_code=502)

        if not isinstance(user, dict) or "id" not in user or not isinstance(user_guilds, list):
            return HTMLResponse(_oauth_error_html("استجابة Discord غير صالحة", "تعذر الحصول على بيانات الحساب والخوادم."), status_code=502)

        managed = {}
        for g in user_guilds:
            permissions = int(g.get("permissions", 0))
            administrator = bool(permissions & 0x8)
            manage_guild = bool(permissions & 0x20)
            if administrator or manage_guild:
                guild_id = int(g["id"])
                # Do not make OAuth login fail simply because Discord.py's
                # local guild cache is cold after a restart. The server can be
                # resolved later by authorized_guild() with fetch_guild().
                managed[str(guild_id)] = {
                    "id": guild_id,
                    "name": g.get("name", ""),
                    "icon": g.get("icon"),
                    "permissions": permissions,
                    "administrator": administrator,
                    "manage_guild": manage_guild,
                }

        request.session.clear()
        request.session["discord_user"] = {"id": int(user["id"]), "username": user.get("username", "")}
        request.session["managed_guilds"] = managed
        return RedirectResponse("/")

    @app.get("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/")

    @app.get("/api/me")
    async def me(request: Request):
        return {"user": request.session.get("discord_user"), "logged_in": bool(request.session.get("discord_user"))}

    @app.get("/api/guilds")
    async def guilds(request: Request):
        await session_user(request)
        return {"guilds": list(request.session.get("managed_guilds", {}).values())}

    @app.get("/api/guilds/{guild_id}/overview")
    async def overview(request: Request, guild_id: int):
        guild, _ = await authorized_guild(request, guild_id)
        open_tickets = await bot.db.fetchone("SELECT COUNT(*) FROM tickets WHERE guild_id=? AND status='open'", (guild_id,))
        teams = await bot.db.fetchone("SELECT COUNT(*) FROM verified_teams WHERE guild_id=? AND active=1", (guild_id,))
        return {
            "id": guild.id, "name": guild.name, "members": guild.member_count,
            "channels": len(guild.channels), "open_tickets": int(open_tickets[0]) if open_tickets else 0,
            "verified_teams": int(teams[0]) if teams else 0,
            "commands": len(_tree_commands(bot)),
        }

    @app.get("/api/guilds/{guild_id}/resources")
    async def resources(request: Request, guild_id: int):
        guild, _ = await authorized_guild(request, guild_id)
        return {
            "roles": [{"id": r.id, "name": r.name, "position": r.position} for r in guild.roles if not r.is_default()],
            "channels": [{"id": c.id, "name": c.name, "type": str(c.type)} for c in guild.channels if hasattr(c, "name")],
        }

    @app.get("/api/guilds/{guild_id}/commands")
    async def commands_list(request: Request, guild_id: int):
        await authorized_guild(request, guild_id)
        rows = await bot.db.fetchall("SELECT * FROM dashboard_command_settings WHERE guild_id=?", (guild_id,))
        settings = {r["command_name"]: dict(r) for r in rows}
        output = []
        for command in _tree_commands(bot):
            r = settings.get(command["name"])
            item = dict(command)
            item.update({
                "enabled": bool(r["enabled"]) if r else True,
                "allowed_roles": json.loads(r["allowed_roles"]) if r else [],
                "denied_roles": json.loads(r["denied_roles"]) if r else [],
                "allowed_channels": json.loads(r["allowed_channels"]) if r else [],
                "denied_channels": json.loads(r["denied_channels"]) if r else [],
            })
            output.append(item)
        return {"commands": output}

    @app.put("/api/guilds/{guild_id}/commands/{command_name:path}")
    async def command_update(request: Request, guild_id: int, command_name: str):
        await authorized_guild(request, guild_id)
        data = await request.json()
        valid = {c["name"] for c in _tree_commands(bot)}
        if command_name not in valid:
            raise HTTPException(status_code=404, detail="الأمر غير موجود")
        await bot.db.execute(
            """INSERT INTO dashboard_command_settings
            (guild_id,command_name,enabled,allowed_roles,denied_roles,allowed_channels,denied_channels,updated_at)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(guild_id,command_name) DO UPDATE SET enabled=excluded.enabled,
            allowed_roles=excluded.allowed_roles,denied_roles=excluded.denied_roles,
            allowed_channels=excluded.allowed_channels,denied_channels=excluded.denied_channels,updated_at=excluded.updated_at""",
            (guild_id, command_name, 1 if data.get("enabled", True) else 0,
             _json_ids(data.get("allowed_roles")), _json_ids(data.get("denied_roles")),
             _json_ids(data.get("allowed_channels")), _json_ids(data.get("denied_channels")), time.time()),
        )
        return {"ok": True}

    @app.get("/api/guilds/{guild_id}/shortcuts")
    async def shortcuts(request: Request, guild_id: int):
        await authorized_guild(request, guild_id)
        try:
            from cogs.shortcuts import SHORTCUTS, DEFAULT_ALIASES
        except Exception:
            SHORTCUTS, DEFAULT_ALIASES = {}, {}
        rows = await bot.db.fetchall("SELECT * FROM dashboard_shortcut_settings WHERE guild_id=?", (guild_id,))
        settings = {r["shortcut_name"]: dict(r) for r in rows}
        out = []
        for key, label in SHORTCUTS.items():
            r = settings.get(key)
            out.append({
                "name": key, "label": label,
                "alias": (r.get("alias") if r else None) or DEFAULT_ALIASES.get(key, ""),
                "enabled": bool(r["enabled"]) if r else True,
                "allowed_roles": json.loads(r["allowed_roles"]) if r else [],
                "denied_roles": json.loads(r["denied_roles"]) if r else [],
                "allowed_channels": json.loads(r["allowed_channels"]) if r else [],
                "denied_channels": json.loads(r["denied_channels"]) if r else [],
            })
        return {"shortcuts": out}

    @app.put("/api/guilds/{guild_id}/shortcuts/{shortcut_name}")
    async def shortcut_update(request: Request, guild_id: int, shortcut_name: str):
        await authorized_guild(request, guild_id)
        data = await request.json()
        await bot.db.execute(
            """INSERT INTO dashboard_shortcut_settings
            (guild_id,shortcut_name,alias,enabled,allowed_roles,denied_roles,allowed_channels,denied_channels,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(guild_id,shortcut_name) DO UPDATE SET alias=excluded.alias,enabled=excluded.enabled,
            allowed_roles=excluded.allowed_roles,denied_roles=excluded.denied_roles,
            allowed_channels=excluded.allowed_channels,denied_channels=excluded.denied_channels,updated_at=excluded.updated_at""",
            (guild_id, shortcut_name, str(data.get("alias", "")).strip() or None,
             1 if data.get("enabled", True) else 0,
             _json_ids(data.get("allowed_roles")), _json_ids(data.get("denied_roles")),
             _json_ids(data.get("allowed_channels")), _json_ids(data.get("denied_channels")), time.time()),
        )
        cog = bot.get_cog("Shortcuts")
        if cog and data.get("alias"):
            alias = str(data["alias"]).strip()
            if alias and not alias.startswith("!"):
                alias = "!" + alias
            if " " not in alias:
                cog.set_alias(guild_id, shortcut_name, alias)
        return {"ok": True}

    @app.get("/api/guilds/{guild_id}/tickets")
    async def ticket_panels(request: Request, guild_id: int):
        await authorized_guild(request, guild_id)
        panels = await bot.db.list_ticket_panels(guild_id)
        open_rows = await bot.db.fetchall("SELECT id,channel_id,user_id,status,claimed_by,created_at FROM tickets WHERE guild_id=? ORDER BY id DESC LIMIT 100", (guild_id,))
        return {"panels": panels, "tickets": [dict(r) for r in open_rows]}

    @app.post("/api/guilds/{guild_id}/tickets/panels")
    async def ticket_panel_create(request: Request, guild_id: int):
        guild, _ = await authorized_guild(request, guild_id)
        data = await request.json()
        channel = guild.get_channel(int(data.get("channel_id", 0)))
        category = guild.get_channel(int(data.get("category_id", 0)))
        if not isinstance(channel, __import__("discord").TextChannel) or not isinstance(category, __import__("discord").CategoryChannel):
            raise HTTPException(status_code=400, detail="القناة أو الفئة غير صالحة")
        cog = bot.get_cog("TicketManager")
        if not cog:
            raise HTTPException(status_code=503, detail="نظام التذاكر غير محمّل")
        options = data.get("options") or [{"name": "فتح تذكرة", "emoji": "🎫", "ticket_name": "ticket-{user}", "description": "فتح تذكرة"}]
        panel_data = {
            "guild_id": guild_id, "channel_id": channel.id, "title": str(data.get("title") or "🎫 الدعم الفني"),
            "description": str(data.get("description") or "اختار القسم المناسب لفتح تذكرة."),
            "image_url": data.get("image_url"), "mode": data.get("mode", "buttons"),
            "category_id": category.id, "support_role_id": data.get("support_role_id"),
            "ticket_description": str(data.get("ticket_description") or "شرح لينا المشكل بالتفصيل."),
            "options": options,
        }
        panel_id = await bot.db.create_ticket_panel(panel_data)
        panel = await bot.db.get_ticket_panel(panel_id)
        try:
            message = await channel.send(embed=cog.panel_embed(panel), view=cog.__class__.__dict__["__name__"] and __import__("cogs.ticket_manager", fromlist=["TicketPanelView"]).TicketPanelView(cog, panel))
            await bot.db.update_ticket_panel(panel_id, {"channel_id": channel.id, "message_id": message.id})
            bot.add_view(__import__("cogs.ticket_manager", fromlist=["TicketPanelView"]).TicketPanelView(cog, panel), message_id=message.id)
        except Exception as exc:
            await bot.db.delete_ticket_panel(panel_id)
            raise HTTPException(status_code=500, detail=f"تعذر نشر اللوحة: {exc}")
        return {"ok": True, "panel_id": panel_id, "message_id": message.id}

    @app.get("/api/guilds/{guild_id}/teams")
    async def teams(request: Request, guild_id: int):
        await authorized_guild(request, guild_id)
        rows = await bot.db.fetchall("SELECT * FROM verified_teams WHERE guild_id=? AND active=1 ORDER BY team_type,id", (guild_id,))
        result = []
        for r in rows:
            c = await bot.db.fetchone("SELECT COUNT(*) FROM team_members WHERE team_id=?", (r["id"],))
            item = dict(r); item["players"] = int(c[0]) if c else 0
            result.append(item)
        return {"teams": result}

    return app


def _login_html(oauth_ready: bool) -> str:
    action = '<a class="btn primary" href="/login">تسجيل الدخول بواسطة Discord</a>' if oauth_ready else '<div class="warn">⚠️ تسجيل الدخول غير متاح حالياً.<br>خاص إدارة البوت تضيف <code>DISCORD_CLIENT_ID</code> و <code>DISCORD_CLIENT_SECRET</code>.</div>'
    return f'''<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Nova Aro — تسجيل الدخول</title><style>:root{{--bg:#070a12;--panel:#101625;--line:#25304a;--text:#f4f7fb;--muted:#9aa8bd;--accent:#7c5cff;--warn:#ffb84d}}*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:radial-gradient(circle at top,#18203b 0,#070a12 55%);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Tahoma,Arial,sans-serif;padding:20px}}.card{{width:min(500px,100%);background:rgba(16,22,37,.96);border:1px solid var(--line);border-radius:22px;padding:34px;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,.35)}}.logo{{font-size:48px;font-weight:1000;color:#9c87ff}}h1{{margin:10px 0}}p{{color:var(--muted);line-height:1.8}}.btn{{display:inline-block;text-decoration:none;color:#fff;padding:12px 18px;border-radius:10px;background:var(--accent);font-weight:800;margin-top:12px}}.warn{{margin-top:18px;padding:14px;border:1px solid rgba(255,184,77,.35);background:rgba(255,184,77,.08);border-radius:12px;color:#ffd48a;line-height:1.8}}code{{background:#090f1c;padding:2px 6px;border-radius:5px}}</style></head><body><div class="card"><div class="logo">NOVA ARO</div><h1>لوحة التحكم</h1><p>خاصك تسجل الدخول بواسطة Discord باش تقدر تدير السيرفرات اللي عندك فيها صلاحية الإدارة والبوت موجود فيها.</p>{action}</div></body></html>'''


def _oauth_error_html(title: str, message: str) -> str:
    safe_title = title.replace("<", "&lt;").replace(">", "&gt;")
    safe_message = message.replace("<", "&lt;").replace(">", "&gt;")
    return f'''<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Nova Aro — خطأ</title><style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#070a12;color:#fff;font-family:system-ui;padding:20px}}.card{{max-width:620px;padding:30px;border:1px solid #25304a;border-radius:18px;background:#101625;text-align:center}}.err{{color:#ff8d99;line-height:1.8}}a{{display:inline-block;margin-top:18px;color:#fff;background:#7c5cff;padding:10px 15px;border-radius:9px;text-decoration:none}}</style></head><body><div class="card"><h1>Nova Aro</h1><h2>{safe_title}</h2><p class="err">{safe_message}</p><a href="/">العودة</a></div></body></html>'''


def _dashboard_html() -> str:
    return r'''<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nova Aro — لوحة التحكم</title><style>
:root{--bg:#070a12;--panel:#101625;--panel2:#151d2f;--text:#f4f7fb;--muted:#9aa8bd;--line:#25304a;--accent:#7c5cff;--good:#31d58a;--bad:#ff5f6d}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at top,#18203b 0,#070a12 48%);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Tahoma,Arial,sans-serif}button,input,select,textarea{font:inherit}button{cursor:pointer}.app{display:grid;grid-template-columns:250px 1fr;min-height:100vh}.side{border-left:1px solid var(--line);background:rgba(8,11,20,.92);padding:24px 16px;position:sticky;top:0;height:100vh}.brand{font-size:24px;font-weight:900;margin-bottom:8px}.sub{color:var(--muted);font-size:12px;margin-bottom:28px}.nav button{width:100%;border:0;background:transparent;color:var(--muted);padding:12px 14px;text-align:right;border-radius:12px;margin:3px 0}.nav button.active,.nav button:hover{background:var(--panel2);color:#fff}.main{padding:28px;max-width:1500px;width:100%;margin:auto}.top{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:24px}.select{background:var(--panel);border:1px solid var(--line);color:#fff;padding:11px 14px;border-radius:10px;min-width:260px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.card{background:linear-gradient(180deg,rgba(21,29,47,.94),rgba(13,18,31,.94));border:1px solid var(--line);border-radius:16px;padding:18px}.metric{font-size:30px;font-weight:900;margin-top:7px}.muted{color:var(--muted)}.section{margin-top:22px}.table{width:100%;border-collapse:collapse}.table th,.table td{padding:13px;border-bottom:1px solid var(--line);text-align:right;vertical-align:top}.table th{color:var(--muted);font-size:12px}.pill{display:inline-block;padding:5px 9px;border-radius:999px;background:#202a43;color:#b9c6de;font-size:12px}.on{background:rgba(49,213,138,.12);color:var(--good)}.off{background:rgba(255,95,109,.12);color:var(--bad)}.btn{border:1px solid var(--line);background:#1a2338;color:#fff;padding:9px 12px;border-radius:9px}.btn.primary{background:var(--accent);border-color:var(--accent)}.btn.good{background:#187c58;border-color:#187c58}.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}.modal{position:fixed;inset:0;background:rgba(0,0,0,.7);display:none;align-items:center;justify-content:center;padding:18px}.modal.show{display:flex}.dialog{width:min(760px,100%);max-height:90vh;overflow:auto;background:#0d1424;border:1px solid var(--line);border-radius:18px;padding:22px}.field{margin:12px 0}.field label{display:block;color:var(--muted);font-size:13px;margin-bottom:6px}.field input,.field select,.field textarea{width:100%;background:#090f1c;border:1px solid var(--line);color:#fff;border-radius:9px;padding:10px}.checks{display:grid;grid-template-columns:repeat(2,1fr);gap:7px;max-height:180px;overflow:auto}.check{background:#0b1220;border:1px solid var(--line);padding:8px;border-radius:8px}.login{min-height:100vh;display:grid;place-items:center;padding:20px}.login .card{max-width:480px;text-align:center}.logo{font-size:50px;font-weight:1000;color:#9c87ff}.empty{padding:40px;text-align:center;color:var(--muted)}@media(max-width:900px){.app{grid-template-columns:1fr}.side{height:auto;position:static;border-left:0;border-bottom:1px solid var(--line)}.nav{display:flex;overflow:auto}.nav button{min-width:120px;text-align:center}.grid{grid-template-columns:repeat(2,1fr)}.main{padding:16px}}@media(max-width:560px){.grid{grid-template-columns:1fr}.top{align-items:stretch;flex-direction:column}.select{width:100%}}
</style></head><body><div id="root"></div><script>
const S={guild:null,view:'overview',guilds:[],roles:[],channels:[]};
const rootEl=document.getElementById('root');
const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
async function api(url,opt){let r=await fetch(url,opt);if(r.status===401){location.href='/login';throw new Error('تسجيل الدخول مطلوب')}let d;try{d=await r.json()}catch{throw new Error('استجابة غير صالحة من الخادم')}if(!r.ok)throw new Error(d.detail||'فشل الطلب');return d}
function shell(){rootEl.innerHTML=`<div class="app"><aside class="side"><div class="brand">NOVA ARO</div><div class="sub">Ader Dashboard</div><div class="nav"><button data-v="overview">نظرة عامة</button><button data-v="commands">الأوامر</button><button data-v="shortcuts">الاختصارات</button><button data-v="tickets">التذاكر</button><button data-v="teams">الفرق</button><button onclick="location.href='/logout'">تسجيل الخروج</button></div></aside><main class="main"><div id="main"></div></main></div>`;document.querySelectorAll('.nav button[data-v]').forEach(b=>b.onclick=()=>{S.view=b.dataset.v;loadView()})}
async function loadGuilds(){const d=await api('/api/guilds');S.guilds=d.guilds||[];if(!S.guild){S.guild=S.guilds[0]?.id}if(!S.guild){rootEl.innerHTML='<div class="login"><div class="card"><div class="logo">NOVA ARO</div><h2>لا توجد سيرفرات قابلة للإدارة</h2><p class="muted">تأكد من صلاحياتك في Discord وأن البوت موجود في السيرفر.</p></div></div>';return}shell();loadView()}
async function loadView(){document.querySelectorAll('.nav button[data-v]').forEach(b=>b.classList.toggle('active',b.dataset.v===S.view));const main=document.getElementById('main');main.innerHTML='<div class="empty">جار تحميل البيانات...</div>';try{if(S.view==='overview')return overview();if(S.view==='commands')return commands();if(S.view==='shortcuts')return shortcuts();if(S.view==='tickets')return tickets();if(S.view==='teams')return teams()}catch(e){main.innerHTML=`<div class="card"><h2>تعذر جلب بيانات السيرفر.</h2><p class="muted">${esc(e.message)}</p></div>`}}
async function overview(){const d=await api(`/api/guilds/${S.guild}/overview`);document.getElementById('main').innerHTML=`<div class="top"><div><h1>السيرفر</h1><p class="muted">اختار السيرفر لإظهار الإحصائيات.</p></div><select class="select" onchange="S.guild=Number(this.value);loadView()">${S.guilds.map(g=>`<option value="${g.id}" ${g.id===S.guild?'selected':''}>${esc(g.name)}</option>`).join('')}</select></div><div class="grid">${[['الأعضاء',d.members],['القنوات',d.channels],['التذاكر المفتوحة',d.open_tickets],['الفرق الموثقة',d.verified_teams]].map(x=>`<div class="card"><div class="muted">${x[0]}</div><div class="metric">${x[1]}</div></div>`).join('')}</div><div class="section card"><h2>أدوات الإدارة</h2><p class="muted">عدد الأوامر المتاحة: ${d.commands}</p><div class="toolbar"><button class="btn primary" onclick="S.view='commands';loadView()">إعداد الأوامر</button><button class="btn" onclick="S.view='shortcuts';loadView()">إعداد الاختصارات</button><button class="btn" onclick="S.view='tickets';loadView()">إدارة التذاكر</button></div></div>`}
async function commands(){const d=await api(`/api/guilds/${S.guild}/commands`);renderSettings('الأوامر',d.commands||[],'command')}
async function shortcuts(){const d=await api(`/api/guilds/${S.guild}/shortcuts`);renderSettings('الاختصارات',d.shortcuts||[],'shortcut')}
function renderSettings(title,items,type){document.getElementById('main').innerHTML=`<div class="top"><div><h1>${title}</h1><p class="muted">تقدر تفعل/تعطل وتخصص الصلاحيات.</p></div><button class="btn" onclick="S.view='overview';loadView()">رجوع</button></div><div class="card"><table class="table"><thead><tr><th>الاسم</th><th>الاختصار/الوصف</th><th>الحالة</th><th></th></tr></thead><tbody>${items.map(x=>`<tr><td><b>${esc(x.label||x.name)}</b></td><td class="muted">${esc(x.alias||x.description||'')}</td><td><span class="pill ${x.enabled?'on':'off'}">${x.enabled?'مفعل':'معطل'}</span></td><td><button class="btn primary" onclick='editItem(${JSON.stringify(x)},${JSON.stringify(type)})'>تعديل</button></td></tr>`).join('')}</tbody></table></div>`}
async function tickets(){const d=await api(`/api/guilds/${S.guild}/tickets`);document.getElementById('main').innerHTML=`<div class="top"><div><h1>التذاكر</h1><p class="muted">لوحات التذاكر والتذاكر المسجلة.</p></div></div><div class="grid"><div class="card"><div class="muted">لوحات</div><div class="metric">${(d.panels||[]).length}</div></div><div class="card"><div class="muted">التذاكر</div><div class="metric">${(d.tickets||[]).length}</div></div></div><div class="section card"><h2>آخر التذاكر</h2><table class="table"><thead><tr><th>ID</th><th>القناة</th><th>العضو</th><th>الحالة</th></tr></thead><tbody>${(d.tickets||[]).map(t=>`<tr><td>${t.id}</td><td>${t.channel_id}</td><td>${t.user_id}</td><td>${esc(t.status)}</td></tr>`).join('')}</tbody></table></div>`}
async function teams(){const d=await api(`/api/guilds/${S.guild}/teams`);document.getElementById('main').innerHTML=`<div class="top"><div><h1>الفرق</h1><p class="muted">الفرق الموثقة وأعداد الأعضاء.</p></div></div><div class="card"><table class="table"><thead><tr><th>الفريق</th><th>النوع</th><th>الأعضاء</th></tr></thead><tbody>${(d.teams||[]).map(t=>`<tr><td>${esc(t.name||t.team_name||t.id)}</td><td>${esc(t.team_type||'')}</td><td>${t.players||0}</td></tr>`).join('')}</tbody></table></div>`}
async function editItem(item,type){const channelOptions=S.channels.map(c=>`<label class="check"><input type="checkbox" name="channels" value="${c.id}" ${(item.allowed_channels||[]).includes(c.id)?'checked':''}> ${esc(c.name)}</label>`).join('');const roleOptions=S.roles.map(r=>`<label class="check"><input type="checkbox" name="roles" value="${r.id}" ${(item.allowed_roles||[]).includes(r.id)?'checked':''}> ${esc(r.name)}</label>`).join('');if(!S.roles.length||!S.channels.length){try{const d=await api(`/api/guilds/${S.guild}/resources`);S.roles=d.roles||[];S.channels=d.channels||[]}catch(e){}}document.body.insertAdjacentHTML('beforeend',`<div class="modal show" id="m"><div class="dialog"><h2>تعديل ${esc(item.label||item.name)}</h2><div class="field"><label>مفعل</label><input id="enabled" type="checkbox" ${item.enabled?'checked':''}></div>${type==='shortcut'?'<div class="field"><label>الاختصار</label><input id="alias" value="'+esc(item.alias||'')+'"></div>':''}<div class="field"><label>الرتب المسموحة</label><div class="checks">${roleOptions}</div></div><div class="field"><label>القنوات المسموحة</label><div class="checks">${channelOptions}</div></div><div class="toolbar"><button class="btn primary" onclick="saveItem(${JSON.stringify(item)},${JSON.stringify(type)})">حفظ</button><button class="btn" onclick="document.getElementById('m').remove()">إلغاء</button></div></div></div>`)}
async function saveItem(item,type){const enabled=document.getElementById('enabled').checked;const roles=[...document.querySelectorAll('input[name=roles]:checked')].map(x=>Number(x.value));const channels=[...document.querySelectorAll('input[name=channels]:checked')].map(x=>Number(x.value));const data={enabled,allowed_roles:roles,denied_roles:[],allowed_channels:channels,denied_channels:[]};if(type==='shortcut')data.alias=document.getElementById('alias').value;try{const path=type==='shortcut'?`/api/guilds/${S.guild}/shortcuts/${encodeURIComponent(item.name)}`:`/api/guilds/${S.guild}/commands/${encodeURIComponent(item.name)}`;await api(path,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});document.getElementById('m').remove();loadView()}catch(e){alert(e.message)}}
loadGuilds();
</script></body></html>'''
