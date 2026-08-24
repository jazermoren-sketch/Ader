from __future__ import annotations

import asyncio
import json
import os
import time
from urllib.parse import quote

import aiohttp
import discord
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

# This module hardens the existing dashboard instead of creating a second server.
# It uses the existing dashboard_app session store and API, while fixing stale
# OAuth permissions and providing a more resilient UI inspired by the uploaded
# self-contained dashboard's tabbed/settings pattern.


def _managed_guild(item: dict) -> dict | None:
    try:
        gid = int(item["id"])
        permissions = int(item.get("permissions", 0) or 0)
    except (KeyError, TypeError, ValueError):
        return None
    perms = discord.Permissions(permissions)
    if not (bool(item.get("owner")) or perms.administrator or perms.manage_guild):
        return None
    return {
        "id": gid,
        "name": str(item.get("name") or "Unknown Server"),
        "icon": item.get("icon"),
        "permissions": permissions,
        "administrator": bool(perms.administrator or item.get("owner")),
        "manage_guild": bool(perms.manage_guild or item.get("owner")),
        "owner": bool(item.get("owner")),
        "bot_connected": True,
    }


async def _refresh_oauth_guilds(request: Request, bot) -> tuple[bool, str]:
    from web import dashboard_app as base

    sid = request.session.get("sid")
    session = base._SESSIONS.get(str(sid)) if sid else None
    if not session:
        return False, "جلسة الدخول منتهية."

    token = str(session.get("access_token") or "")
    refresh = str(session.get("refresh_token") or "")
    client_id = os.getenv("DISCORD_CLIENT_ID", "").strip()
    client_secret = os.getenv("DISCORD_CLIENT_SECRET", "").strip()

    async def request_guilds(access_token: str):
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            return await client.get(
                "https://discord.com/api/users/@me/guilds",
                headers={"Authorization": f"Bearer {access_token}"},
            )

    response = None
    try:
        response = await request_guilds(token)
        if response.status == 401 and refresh and client_id and client_secret:
            await response.release()
            redirect_uri = os.getenv("DASHBOARD_REDIRECT_URI", "").strip() or f"{str(request.base_url).rstrip('/')}/callback"
            payload = {
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "redirect_uri": redirect_uri,
            }
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as client:
                async with client.post("https://discord.com/api/oauth2/token", data=payload) as token_response:
                    token_data = await token_response.json(content_type=None)
                    if token_response.status >= 400 or not isinstance(token_data, dict) or not token_data.get("access_token"):
                        return False, "جلسة Discord منتهية. سجل الدخول من جديد."
                    token = str(token_data["access_token"])
                    session["access_token"] = token
                    session["refresh_token"] = token_data.get("refresh_token") or refresh
            response = await request_guilds(token)

        if response is None or response.status >= 400:
            if response is not None:
                await response.release()
            return False, "تعذر التحقق من صلاحيات Discord."

        data = await response.json(content_type=None)
        await response.release()
        if not isinstance(data, list):
            return False, "Discord رجع بيانات غير صالحة."

        managed = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            row = _managed_guild(item)
            if row is None:
                continue
            if bot.get_guild(row["id"]) is None:
                continue
            managed[str(row["id"])] = row

        session["managed_guilds"] = managed
        session["guilds_refreshed_at"] = time.time()
        return True, ""
    except (aiohttp.ClientError, asyncio.TimeoutError, TypeError, ValueError) as exc:
        if response is not None:
            try:
                await response.release()
            except Exception:
                pass
        return False, f"تعذر الاتصال بـDiscord: {exc}"


def _dashboard_html() -> str:
    return """<!doctype html><html lang='ar' dir='rtl'><head>
<meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Ader Dashboard</title>
<style>
:root{--bg:#070b14;--panel:#10182a;--panel2:#151f34;--line:#293653;--text:#f7f9ff;--muted:#9daac0;--accent:#5865f2;--good:#39d98a;--bad:#ff6d7d}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(circle at 15% 0,#1b2851 0,transparent 32%),linear-gradient(145deg,#050812,#0a1121 58%,#070b14);color:var(--text);font-family:Inter,system-ui,-apple-system,Segoe UI,Tahoma,sans-serif}.shell{max-width:1280px;margin:auto;padding:18px}.top{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:18px}.brand{font-size:30px;font-weight:1000;letter-spacing:.08em;color:#8f95ff}.user{color:var(--muted);font-size:14px}.btn,.tab,.guild{border:1px solid var(--line);background:var(--panel2);color:#fff;border-radius:12px;padding:10px 13px;cursor:pointer;text-decoration:none;font-weight:800}.btn.primary,.tab.active{background:var(--accent);border-color:var(--accent)}.layout{display:grid;grid-template-columns:300px 1fr;gap:16px}.panel{background:rgba(16,24,42,.95);border:1px solid var(--line);border-radius:20px;padding:16px}.servers{min-height:520px}.list{display:grid;gap:8px}.guild{width:100%;display:flex;justify-content:space-between;gap:10px;align-items:center;text-align:right}.guild.selected{outline:2px solid var(--accent)}.guild small{display:block;color:var(--muted)}.tabs{display:flex;gap:8px;overflow:auto;margin-bottom:12px}.tab-panel{display:none}.tab-panel.active{display:block}.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.card{padding:15px;border:1px solid var(--line);border-radius:14px;background:var(--panel2);display:grid;gap:5px}.card span{color:var(--muted)}.card strong{font-size:20px}.row{display:flex;justify-content:space-between;align-items:center;padding:12px 2px;border-bottom:1px solid var(--line);gap:10px}.row p{margin:4px 0 0;color:var(--muted)}.loading{padding:28px;text-align:center;color:var(--muted)}.error,.ok{padding:12px;border-radius:12px;margin-bottom:12px}.error{background:#301720;border:1px solid #733141;color:#ffb2bc}.ok{background:#10271d;border:1px solid #2b6a4b;color:#a3f0c1}.switch{width:46px;height:25px;position:relative}.switch input{opacity:0;width:0;height:0}.switch i{position:absolute;inset:0;background:#34405a;border-radius:20px;cursor:pointer}.switch i:before{content:"";position:absolute;width:19px;height:19px;left:3px;top:3px;border-radius:50%;background:#fff;transition:.15s}.switch input:checked+i{background:var(--good)}.switch input:checked+i:before{transform:translateX(21px)}@media(max-width:900px){.layout{grid-template-columns:1fr}.servers{min-height:auto}.cards{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:560px){.shell{padding:10px}.cards{grid-template-columns:1fr}.top{align-items:flex-start}}
</style></head><body><main class='shell'><header class='top'><div><div class='brand'>ADER</div><div id='user' class='user'></div></div><a class='btn' href='/logout'>تسجيل الخروج</a></header>
<div id='notice'></div><div class='layout'><aside class='panel servers'><h2>السيرفرات</h2><div id='guilds' class='list'><div class='loading'>جاري التحميل...</div></div></aside>
<section><nav class='tabs'><button class='tab active' data-tab='overview'>Overview</button><button class='tab' data-tab='commands'>الأوامر</button><button class='tab' data-tab='shortcuts'>الاختصارات</button><button class='tab' data-tab='resources'>الرومات والرتب</button></nav>
<section id='overview' class='panel tab-panel active'><div id='overview-body' class='loading'>اختار السيرفر.</div></section>
<section id='commands' class='panel tab-panel'><div id='commands-body' class='loading'>اختار السيرفر.</div></section>
<section id='shortcuts' class='panel tab-panel'><div id='shortcuts-body' class='loading'>اختار السيرفر.</div></section>
<section id='resources' class='panel tab-panel'><div id='resources-body' class='loading'>اختار السيرفر.</div></section></section></div></main>
<script>
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];let current=null;
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function flash(msg,bad=false){$('#notice').innerHTML=msg?'<div class="'+(bad?'error':'ok')+'">'+esc(msg)+'</div>':'';}
async function api(url,opts={}){const r=await fetch(url,{credentials:'same-origin',...opts});const ct=r.headers.get('content-type')||'';const d=ct.includes('json')?await r.json():await r.text();if(r.status===401){location.href='/';return null}if(!r.ok)throw new Error(d?.detail||d||('HTTP '+r.status));return d;}
function tab(id){$$('.tab').forEach(x=>x.classList.toggle('active',x.dataset.tab===id));$$('.tab-panel').forEach(x=>x.classList.toggle('active',x.id===id));}
$$('.tab').forEach(x=>x.onclick=()=>tab(x.dataset.tab));
async function boot(){try{const me=await api('/api/me');if(me?.user)$('#user').textContent=me.user.global_name||me.user.username||'';await loadGuilds();}catch(e){$('#guilds').innerHTML='<div class="error">'+esc(e.message)+'</div>';}}
async function loadGuilds(){const d=await api('/api/guilds');if(!d)return;const root=$('#guilds');root.innerHTML='';if(!d.guilds.length){root.innerHTML='<div class="loading">ما كاين حتى سيرفر متاح. تأكد أن البوت داخل السيرفر وأن عندك Administrator أو Manage Server.</div>';return}for(const g of d.guilds){const b=document.createElement('button');b.className='guild';b.innerHTML='<span>'+esc(g.name)+'</span><small>'+(g.administrator?'Administrator':(g.owner?'Owner':'Manage Server'))+'</small>';b.onclick=()=>selectGuild(g.id,b);root.appendChild(b);}}
async function selectGuild(id,btn){current=id;$$('.guild').forEach(x=>x.classList.remove('selected'));btn.classList.add('selected');for(const id of ['overview','commands','shortcuts','resources'])$('#'+id+'-body').innerHTML='<div class="loading">جاري التحميل...</div>';const tasks=[['overview',api('/api/guilds/'+id+'/overview')],['commands',api('/api/guilds/'+id+'/commands')],['shortcuts',api('/api/guilds/'+id+'/shortcuts')],['resources',api('/api/guilds/'+id+'/resources')]];const rs=await Promise.allSettled(tasks.map(x=>x[1]));rs.forEach((r,i)=>{const name=tasks[i][0],el=$('#'+name+'-body');if(r.status==='rejected'){el.innerHTML='<div class="error">'+esc(r.reason?.message||'تعذر تحميل القسم')+'</div>';return}if(name==='overview')renderOverview(r.value);if(name==='commands')renderCommands(r.value);if(name==='shortcuts')renderShortcuts(r.value);if(name==='resources')renderResources(r.value);});}
function card(a,b){return '<div class="card"><span>'+esc(a)+'</span><strong>'+esc(b)+'</strong></div>';}
function renderOverview(d){$('#overview-body').innerHTML='<div class="cards">'+card('السيرفر',d.name)+card('الأعضاء',d.members)+card('الرومات',d.channels)+card('الرتب',d.roles)+card('التذاكر المفتوحة',d.open_tickets)+card('الأوامر',d.commands)+card('Latency',d.latency_ms+' ms')+'</div>';}
function renderCommands(d){const rows=d.commands||[];$('#commands-body').innerHTML=rows.length?rows.map(c=>'<div class="row"><div><b>/'+esc(c.name)+'</b><p>'+esc(c.description)+'</p></div><label class="switch"><input type="checkbox" '+(c.enabled?'checked':'')+' onchange="toggleCommand('+JSON.stringify(c.name).replace(/</g,'\\u003c')+',this.checked)"><i></i></label></div>').join(''):'<div class="loading">لا توجد أوامر.</div>';}
async function toggleCommand(name,enabled){try{await api('/api/guilds/'+current+'/commands/'+encodeURIComponent(name),{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled})});flash('تم حفظ إعداد الأمر');}catch(e){flash(e.message,true);}}
function renderShortcuts(d){const rows=d.shortcuts||[];$('#shortcuts-body').innerHTML=rows.length?rows.map(x=>'<div class="row"><div><b>'+esc(x.label)+'</b><p>'+esc(x.alias||'')+'</p></div><small>'+ (x.enabled?'مفعل':'معطل') +'</small></div>').join(''):'<div class="loading">لا توجد اختصارات.</div>';}
function renderResources(d){$('#resources-body').innerHTML='<div class="cards"><div class="card"><strong>الرومات</strong><div>'+((d.channels||[]).map(x=>'#'+esc(x.name)).join('<br>')||'لا توجد')+'</div></div><div class="card"><strong>الرتب</strong><div>'+((d.roles||[]).map(x=>esc(x.name)).join('<br>')||'لا توجد')+'</div></div></div>';}
boot();
</script></body></html>"""


def install_dashboard_hardening(app, bot) -> None:
    @app.middleware("http")
    async def hardening(request: Request, call_next):
        path = request.url.path
        if path == "/" and request.session.get("sid"):
            return RedirectResponse("/app", status_code=302)

        if path == "/api/guilds" or path.startswith("/api/guilds/"):
            ok, message = await _refresh_oauth_guilds(request, bot)
            if not ok and request.session.get("sid"):
                return JSONResponse({"detail": message or "تعذر التحقق من صلاحيات Discord."}, status_code=401)

        return await call_next(request)

    @app.get("/app", response_class=HTMLResponse, include_in_schema=False)
    async def hardened_dashboard(request: Request):
        from web.dashboard_app import _session_for
        if not _session_for(request):
            return RedirectResponse("/", status_code=302)
        return HTMLResponse(_dashboard_html(), headers={"Cache-Control": "no-store"})
'''
path=Path("/tmp/dashboard_hardening.py")
path.write_text(code, encoding="utf-8")
py_compile.compile(str(path), doraise=True)
print("ok