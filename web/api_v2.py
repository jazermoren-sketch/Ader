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
        # The site still boots without OAuth credentials, but writes remain protected
        # by the dashboard setup requirement instead of exposing an unauthenticated admin panel.
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
        guild = bot.get_guild(guild_id)
        if not guild:
            raise HTTPException(status_code=404, detail="الخادم غير موجود أو البوت غير موجود فيه")
        guilds = request.session.get("managed_guilds", {})
        if str(guild_id) not in guilds:
            raise HTTPException(status_code=403, detail="لا تملك صلاحية إدارة هذا الخادم")
        return guild, user

    @app.get("/", response_class=HTMLResponse)
    async def home():
        html = _dashboard_html()
        return HTMLResponse(html)

    @app.get("/login")
    async def login():
        if not oauth_ready():
            return HTMLResponse("<h2>Nova Aro</h2><p>أضف DISCORD_CLIENT_ID و DISCORD_CLIENT_SECRET أولاً.</p>", status_code=503)
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
        async with aiohttp.ClientSession() as s:
            async with s.post("https://discord.com/api/oauth2/token", data=data) as r:
                token = await r.json()
            if "access_token" not in token:
                return HTMLResponse("<h2>فشل تسجيل الدخول</h2><p>تحقق من OAuth Redirect URI.</p>", status_code=400)
            headers = {"Authorization": f"Bearer {token['access_token']}"}
            async with s.get("https://discord.com/api/users/@me", headers=headers) as r:
                user = await r.json()
            async with s.get("https://discord.com/api/users/@me/guilds", headers=headers) as r:
                user_guilds = await r.json()
        managed = {}
        for g in user_guilds:
            permissions = int(g.get("permissions", 0))
            if permissions & 0x8 or permissions & 0x20:
                if bot.get_guild(int(g["id"])):
                    managed[str(g["id"])] = {
                        "id": int(g["id"]), "name": g.get("name", ""),
                        "icon": g.get("icon"), "permissions": permissions,
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
        # Keep the existing shortcuts JSON system synchronized when the alias is changed.
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


def _dashboard_html() -> str:
    return r'''<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nova Aro — لوحة التحكم</title><style>
:root{--bg:#070a12;--panel:#101625;--panel2:#151d2f;--text:#f4f7fb;--muted:#9aa8bd;--line:#25304a;--accent:#7c5cff;--good:#31d58a;--bad:#ff5f6d}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at top,#18203b 0,#070a12 48%);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Tahoma,Arial,sans-serif}button,input,select,textarea{font:inherit}button{cursor:pointer}.app{display:grid;grid-template-columns:250px 1fr;min-height:100vh}.side{border-left:1px solid var(--line);background:rgba(8,11,20,.92);padding:24px 16px;position:sticky;top:0;height:100vh}.brand{font-size:24px;font-weight:900;margin-bottom:8px}.sub{color:var(--muted);font-size:12px;margin-bottom:28px}.nav button{width:100%;border:0;background:transparent;color:var(--muted);padding:12px 14px;text-align:right;border-radius:12px;margin:3px 0}.nav button.active,.nav button:hover{background:var(--panel2);color:#fff}.main{padding:28px;max-width:1500px;width:100%;margin:auto}.top{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:24px}.select{background:var(--panel);border:1px solid var(--line);color:#fff;padding:11px 14px;border-radius:10px;min-width:260px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.card{background:linear-gradient(180deg,rgba(21,29,47,.94),rgba(13,18,31,.94));border:1px solid var(--line);border-radius:16px;padding:18px}.metric{font-size:30px;font-weight:900;margin-top:7px}.muted{color:var(--muted)}.section{margin-top:22px}.table{width:100%;border-collapse:collapse}.table th,.table td{padding:13px;border-bottom:1px solid var(--line);text-align:right;vertical-align:top}.table th{color:var(--muted);font-size:12px}.pill{display:inline-block;padding:5px 9px;border-radius:999px;background:#202a43;color:#b9c6de;font-size:12px}.on{background:rgba(49,213,138,.12);color:var(--good)}.off{background:rgba(255,95,109,.12);color:var(--bad)}.btn{border:1px solid var(--line);background:#1a2338;color:#fff;padding:9px 12px;border-radius:9px}.btn.primary{background:var(--accent);border-color:var(--accent)}.btn.good{background:#187c58;border-color:#187c58}.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}.modal{position:fixed;inset:0;background:rgba(0,0,0,.7);display:none;align-items:center;justify-content:center;padding:18px}.modal.show{display:flex}.dialog{width:min(760px,100%);max-height:90vh;overflow:auto;background:#0d1424;border:1px solid var(--line);border-radius:18px;padding:22px}.field{margin:12px 0}.field label{display:block;color:var(--muted);font-size:13px;margin-bottom:6px}.field input,.field select,.field textarea{width:100%;background:#090f1c;border:1px solid var(--line);color:#fff;border-radius:9px;padding:10px}.checks{display:grid;grid-template-columns:repeat(2,1fr);gap:7px;max-height:180px;overflow:auto}.check{background:#0b1220;border:1px solid var(--line);padding:8px;border-radius:8px}.login{min-height:100vh;display:grid;place-items:center;padding:20px}.login .card{max-width:480px;text-align:center}.logo{font-size:50px;font-weight:1000;color:#9c87ff}.empty{padding:40px;text-align:center;color:var(--muted)}@media(max-width:900px){.app{grid-template-columns:1fr}.side{height:auto;position:static;border-left:0;border-bottom:1px solid var(--line)}.nav{display:flex;overflow:auto}.nav button{min-width:120px;text-align:center}.grid{grid-template-columns:repeat(2,1fr)}.main{padding:16px}}@media(max-width:560px){.grid{grid-template-columns:1fr}.top{align-items:stretch;flex-direction:column}.select{width:100%}}
</style></head><body><div id="root"></div><script>
const S={guild:null,view:'overview',guilds:[],roles:[],channels:[]};
const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
async function api(url,opt){let r=await fetch(url,opt);if(r.status===401){location.href='/login';throw 0}let d=await r.json();if(!r.ok)throw Error(d.detail||'حدث خطأ');return d}
async function init(){let me=await api('/api/me');if(!me.logged_in)return login();let g=await api('/api/guilds');S.guilds=g.guilds;S.guild=S.guilds[0]?.id;render();}
function login(){root.innerHTML='<div class="login"><div class="card"><div class="logo">NOVA ARO</div><h1>لوحة التحكم الاحترافية</h1><p class="muted">إدارة البوت، الأوامر، الاختصارات، التذاكر والأنظمة من مكان واحد.</p><a class="btn primary" href="/login">تسجيل الدخول بواسطة Discord</a></div></div>'}
function render(){root.innerHTML='<div class="app"><aside class="side"><div class="brand">NOVA ARO</div><div class="sub">لوحة تحكم Ader</div><div class="nav">'+[['overview','نظرة عامة'],['commands','الأوامر'],['shortcuts','الاختصارات'],['tickets','التذاكر'],['teams','الأندية والمنتخبات']].map(x=>`<button class="${S.view==x[0]?'active':''}" onclick="go('${x[0]}')">${x[1]}</button>`).join('')+'</div><div style="margin-top:28px"><a class="muted" href="/logout">تسجيل الخروج</a></div></aside><main class="main"><div class="top"><div><h1 style="margin:0">${labelView()}</h1><div class="muted">كل الإعدادات محفوظة مباشرة في قاعدة بيانات البوت.</div></div><select class="select" onchange="changeGuild(this.value)">${S.guilds.map(g=>`<option value="${g.id}" ${String(g.id)==String(S.guild)?'selected':''}>${esc(g.name)}</option>`).join('')}</select></div><div id="content"></div></main></div>';loadView()}
function labelView(){return {overview:'نظرة عامة',commands:'إدارة الأوامر',shortcuts:'إدارة الاختصارات',tickets:'نظام التذاكر',teams:'الأندية والمنتخبات'}[S.view]}
function go(v){S.view=v;render()}function changeGuild(v){S.guild=Number(v);loadView()}
async function loadView(){try{if(!S.guild)return content.innerHTML='<div class="empty">لا يوجد خادم مُدار وموجود فيه البوت.</div>';if(S.view==='overview')return overview();if(S.view==='commands')return commands();if(S.view==='shortcuts')return shortcuts();if(S.view==='tickets')return tickets();return teams()}catch(e){content.innerHTML='<div class="card">❌ '+esc(e.message)+'</div>'}}
async function overview(){let d=await api(`/api/guilds/${S.guild}/overview`);content.innerHTML='<div class="grid">'+[['👥','الأعضاء',d.members],['📺','القنوات',d.channels],['🎫','التذاكر المفتوحة',d.open_tickets],['🏆','الفرق الموثقة',d.verified_teams]].map(x=>`<div class="card"><div class="muted">${x[0]} ${x[1]}</div><div class="metric">${x[2]}</div></div>`).join('')+'</div><div class="section card"><h2>Nova Aro</h2><p class="muted">عدد أوامر البوت المتاحة حاليًا: ${d.commands}. يمكنك ضبط صلاحيات كل أمر حسب الرتب والقنوات.</p></div>'}
async function resources(){if(!S.roles.length){let d=await api(`/api/guilds/${S.guild}/resources`);S.roles=d.roles;S.channels=d.channels}return {roles:S.roles,channels:S.channels}}
function multi(items,selected,name){return '<div class="checks">'+items.map(x=>`<label class="check"><input type="checkbox" name="${name}" value="${x.id}" ${selected.includes(x.id)?'checked':''}> ${esc(x.name)}</label>`).join('')+'</div>'}
async function commands(){let [d,r]=await Promise.all([api(`/api/guilds/${S.guild}/commands`),resources()]);content.innerHTML='<div class="toolbar"><input id="filter" class="select" style="min-width:220px" placeholder="بحث عن أمر..." oninput="filterRows()"></div><div class="card"><table class="table"><thead><tr><th>الأمر</th><th>الحالة</th><th>التحكم</th></tr></thead><tbody id="rows">'+d.commands.map((c,i)=>`<tr data-name="${esc(c.name.toLowerCase())}"><td><b>/${esc(c.name)}</b><div class="muted">${esc(c.description)}</div></td><td><span class="pill ${c.enabled?'on':'off'}">${c.enabled?'مفعل':'معطل'}</span></td><td><button class="btn primary" onclick='editCommand(${JSON.stringify(c)})'>تخصيص</button></td></tr>`).join('')+'</tbody></table></div>'}
function filterRows(){let q=document.getElementById('filter').value.toLowerCase();document.querySelectorAll('#rows tr').forEach(r=>r.style.display=r.dataset.name.includes(q)?'':'none')}
async function editCommand(c){let r=await resources();showModal(`<h2>تخصيص /${esc(c.name)}</h2><div class="field"><label>الحالة</label><select id="enabled"><option value="1" ${c.enabled?'selected':''}>مفعل</option><option value="0" ${!c.enabled?'selected':''}>معطل</option></select></div><div class="field"><label>الرتب المسموحة (فارغ = الجميع)</label>${multi(r.roles,c.allowed_roles,'ar')}</div><div class="field"><label>الرتب المرفوضة</label>${multi(r.roles,c.denied_roles,'dr')}</div><div class="field"><label>القنوات المسموحة (فارغ = الجميع)</label>${multi(r.channels,c.allowed_channels,'ac')}</div><div class="field"><label>القنوات المرفوضة</label>${multi(r.channels,c.denied_channels,'dc')}</div><div class="toolbar"><button class="btn primary" onclick="saveCommand('${encodeURIComponent(c.name)}')">حفظ</button><button class="btn" onclick="closeModal()">إلغاء</button></div>`)}
function vals(n){return [...document.querySelectorAll(`input[name="${n}"]:checked`)].map(x=>Number(x.value))}
async function saveCommand(n){await api(`/api/guilds/${S.guild}/commands/${n}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:document.getElementById('enabled').value==='1',allowed_roles:vals('ar'),denied_roles:vals('dr'),allowed_channels:vals('ac'),denied_channels:vals('dc')})});closeModal();commands()}
async function shortcuts(){let [d,r]=await Promise.all([api(`/api/guilds/${S.guild}/shortcuts`),resources()]);content.innerHTML='<div class="card"><table class="table"><thead><tr><th>الاختصار</th><th>Alias</th><th>الحالة</th><th></th></tr></thead><tbody>'+d.shortcuts.map(s=>`<tr><td><b>${esc(s.label)}</b><div class="muted">${esc(s.name)}</div></td><td><code>${esc(s.alias)}</code></td><td><span class="pill ${s.enabled?'on':'off'}">${s.enabled?'مفعل':'معطل'}</span></td><td><button class="btn primary" onclick='editShortcut(${JSON.stringify(s)})'>تخصيص</button></td></tr>`).join('')+'</tbody></table></div>'}
async function editShortcut(s){let r=await resources();showModal(`<h2>تخصيص ${esc(s.label)}</h2><div class="field"><label>الاختصار</label><input id="alias" value="${esc(s.alias)}"></div><div class="field"><label>الحالة</label><select id="enabled"><option value="1" ${s.enabled?'selected':''}>مفعل</option><option value="0" ${!s.enabled?'selected':''}>معطل</option></select></div><div class="field"><label>الرتب المسموحة</label>${multi(r.roles,s.allowed_roles,'ar')}</div><div class="field"><label>الرتب المرفوضة</label>${multi(r.roles,s.denied_roles,'dr')}</div><div class="field"><label>القنوات المسموحة</label>${multi(r.channels,s.allowed_channels,'ac')}</div><div class="field"><label>القنوات المرفوضة</label>${multi(r.channels,s.denied_channels,'dc')}</div><div class="toolbar"><button class="btn primary" onclick="saveShortcut('${encodeURIComponent(s.name)}')">حفظ</button><button class="btn" onclick="closeModal()">إلغاء</button></div>`)}
async function saveShortcut(n){await api(`/api/guilds/${S.guild}/shortcuts/${n}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({alias:document.getElementById('alias').value,enabled:document.getElementById('enabled').value==='1',allowed_roles:vals('ar'),denied_roles:vals('dr'),allowed_channels:vals('ac'),denied_channels:vals('dc')})});closeModal();shortcuts()}
async function tickets(){let [d,r]=await Promise.all([api(`/api/guilds/${S.guild}/tickets`),resources()]);content.innerHTML='<div class="toolbar"><button class="btn primary" onclick="newPanel()">+ إنشاء لوحة تذاكر</button></div><div class="grid"><div class="card"><div class="muted">لوحات التذاكر</div><div class="metric">'+d.panels.length+'</div></div><div class="card"><div class="muted">آخر التذاكر</div><div class="metric">'+d.tickets.length+'</div></div></div><div class="section card"><h2>لوحات التذاكر</h2><table class="table"><thead><tr><th>#</th><th>العنوان</th><th>الوضع</th><th>القناة</th></tr></thead><tbody>'+d.panels.map(p=>`<tr><td>#${p.id}</td><td>${esc(p.title)}</td><td>${esc(p.mode)}</td><td>${p.channel_id?'<#'+p.channel_id+'>':'—'}</td></tr>`).join('')+'</tbody></table></div>'}
async function newPanel(){let r=await resources();showModal(`<h2>إنشاء لوحة تذاكر</h2><div class="field"><label>العنوان</label><input id="ptitle" value="🎫 الدعم الفني"></div><div class="field"><label>الوصف</label><textarea id="pdesc">اختار القسم المناسب لفتح تذكرة.</textarea></div><div class="field"><label>قناة النشر</label><select id="pchannel">${r.channels.filter(c=>c.type.includes('text')).map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select></div><div class="field"><label>الفئة</label><select id="pcategory">${r.channels.filter(c=>c.type.includes('category')).map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select></div><div class="field"><label>دور الدعم</label><select id="prole"><option value="">بدون</option>${r.roles.map(x=>`<option value="${x.id}">${esc(x.name)}</option>`).join('')}</select></div><div class="field"><label>الوضع</label><select id="pmode"><option value="buttons">أزرار</option><option value="select">قائمة اختيار</option></select></div><div class="toolbar"><button class="btn primary" onclick="savePanel()">نشر اللوحة</button><button class="btn" onclick="closeModal()">إلغاء</button></div>`)}
async function savePanel(){await api(`/api/guilds/${S.guild}/tickets/panels`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:document.getElementById('ptitle').value,description:document.getElementById('pdesc').value,channel_id:Number(document.getElementById('pchannel').value),category_id:Number(document.getElementById('pcategory').value),support_role_id:Number(document.getElementById('prole').value)||null,mode:document.getElementById('pmode').value,options:[{name:'فتح تذكرة',emoji:'🎫',ticket_name:'ticket-{user}',description:'فتح تذكرة'}]})});closeModal();tickets()}
async function teams(){let d=await api(`/api/guilds/${S.guild}/teams`);content.innerHTML='<div class="card"><h2>الفرق الموثقة</h2><table class="table"><thead><tr><th>الفريق</th><th>النوع</th><th>اللاعبون</th><th>الرتبة</th></tr></thead><tbody>'+d.teams.map(t=>`<tr><td>${esc(t.emoji)} <b>${esc(t.name)}</b></td><td>${t.team_type==='national'?'منتخب وطني':'نادي'}</td><td>${t.players}</td><td><@&${t.role_id}></td></tr>`).join('')+'</tbody></table></div>'}
function showModal(html){let m=document.createElement('div');m.id='modal';m.className='modal show';m.innerHTML='<div class="dialog">'+html+'</div>';document.body.appendChild(m)}function closeModal(){document.getElementById('modal')?.remove()}
init();
</script></body></html>'''
