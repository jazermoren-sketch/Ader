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

    def get_redirect_uri(request: Request) -> str:
        configured = str(os.getenv("DASHBOARD_REDIRECT_URI", "") or "").strip()
        if configured:
            return configured
        return str(request.base_url).rstrip("/") + "/callback"

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

        guild = bot.get_guild(guild_id)
        if guild is None:
            try:
                guild = await bot.fetch_guild(guild_id)
            except Exception as exc:
                raise HTTPException(status_code=404, detail="الخادم غير موجود أو البوت غير متصل به حالياً") from exc

        try:
            permissions = int(session_guild.get("permissions", 0) or 0)
        except (TypeError, ValueError):
            permissions = 0
        is_manager = bool(permissions & 0x8 or permissions & 0x20 or session_guild.get("administrator") or session_guild.get("manage_guild"))
        if not is_manager:
            raise HTTPException(status_code=403, detail="لا تملك صلاحية إدارة هذا الخادم")
        return guild, user

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        if not request.session.get("discord_user"):
            return HTMLResponse(_login_html(oauth_ready()), headers={"Cache-Control": "no-store"})
        return HTMLResponse(_dashboard_html(), headers={"Cache-Control": "no-store"})

    @app.get("/login")
    async def login(request: Request):
        if not oauth_ready():
            return HTMLResponse(_oauth_error_html("إعدادات Discord OAuth2 ناقصة", "أضف DISCORD_CLIENT_ID و DISCORD_CLIENT_SECRET في متغيرات البيئة ثم أعد تشغيل Ader."), status_code=503)
        from urllib.parse import quote
        redirect_uri = get_redirect_uri(request)
        url = (
            "https://discord.com/oauth2/authorize?client_id=" + os.environ["DISCORD_CLIENT_ID"]
            + "&response_type=code&redirect_uri=" + quote(redirect_uri, safe="")
            + "&scope=identify%20guilds"
        )
        return RedirectResponse(url)

    @app.get("/callback")
    async def callback(request: Request, code: str = "", error: str = "", error_description: str = ""):
        if error:
            return HTMLResponse(_oauth_error_html("تم إلغاء تسجيل الدخول", error_description or error), status_code=400)
        if not oauth_ready() or not code:
            return RedirectResponse("/")
        redirect_uri = get_redirect_uri(request)
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
                    return HTMLResponse(_oauth_error_html("فشل تسجيل الدخول", str(token.get("error_description") or token.get("error") or "تحقق من إعدادات OAuth2 و Redirect URI.")), status_code=400)
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
            try:
                permissions = int(g.get("permissions", 0) or 0)
                guild_id = int(g["id"])
            except (TypeError, ValueError, KeyError):
                continue
            administrator = bool(permissions & 0x8)
            manage_guild = bool(permissions & 0x20)
            if administrator or manage_guild:
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
        try:
            open_tickets = await bot.db.fetchone("SELECT COUNT(*) FROM tickets WHERE guild_id=? AND status='open'", (guild_id,))
        except Exception:
            open_tickets = (0,)
        try:
            teams = await bot.db.fetchone("SELECT COUNT(*) FROM verified_teams WHERE guild_id=? AND active=1", (guild_id,))
        except Exception:
            teams = (0,)
        return {
            "id": guild.id, "name": guild.name, "members": guild.member_count or len(getattr(guild, "members", ())),
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
    return r'''<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Nova Aro — لوحة التحكم</title><style>body{margin:0;background:#070a12;color:#fff;font-family:system-ui;padding:20px}.box{max-width:800px;margin:40px auto;background:#101625;border:1px solid #27324a;border-radius:18px;padding:24px}select{width:100%;background:#0b1220;border:1px solid #27324a;color:#fff;padding:12px;border-radius:10px}a{color:#fff;background:#7c5cff;padding:10px 14px;border-radius:9px;text-decoration:none;display:inline-block;margin-top:12px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:16px}.m{background:#0b1220;border:1px solid #27324a;border-radius:12px;padding:15px}.n{font-size:28px;font-weight:900}@media(max-width:700px){.grid{grid-template-columns:repeat(2,1fr)}}</style></head><body><div class="box"><h1>NOVA ARO</h1><div id="msg">جاري التحميل...</div><div id="content"></div></div><script>async function j(u){const r=await fetch(u,{credentials:'same-origin'});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.detail||('HTTP '+r.status));return d}async function main(){try{const g=await j('/api/guilds');if(!g.guilds?.length){document.getElementById('msg').textContent='ماكاين حتى سيرفر عندك فيه صلاحية الإدارة والبوت داخل فيه.';return}document.getElementById('msg').textContent='اختار السيرفر';const s=document.createElement('select');g.guilds.forEach(x=>{const o=document.createElement('option');o.value=x.id;o.textContent=x.name;s.appendChild(o)});const c=document.getElementById('content');c.appendChild(s);const grid=document.createElement('div');grid.className='grid';c.appendChild(grid);async function load(){const d=await j('/api/guilds/'+s.value+'/overview');grid.innerHTML=[['الأعضاء',d.members],['القنوات',d.channels],['التذاكر',d.open_tickets],['الفرق',d.verified_teams]].map(x=>`<div class="m">${x[0]}<div class="n">${x[1]}</div></div>`).join('')}s.onchange=load;await load()}catch(e){document.getElementById('msg').textContent='تعذر تحميل الداشبورد: '+e.message}}main()</script></body></html>'''
