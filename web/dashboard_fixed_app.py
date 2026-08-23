"""Runtime fixes for the Ader dashboard without duplicating the dashboard backend."""
from __future__ import annotations

import html
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from web import dashboard_app as base


def _remove_route(app, path: str, method: str) -> None:
    app.router.routes[:] = [
        route for route in app.router.routes
        if not (getattr(route, "path", None) == path and method in getattr(route, "methods", set()))
    ]


def _dashboard_html() -> str:
    return """<!doctype html><html lang='ar' dir='rtl'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Ader Dashboard</title>""" + base._styles() + """</head><body><main class='shell'><header class='top'><div><div class='brand small'>ADER</div><div class='muted'>Dashboard</div></div><a class='btn' href='/logout'>تسجيل الخروج</a></header><section class='grid'><div class='panel'><h2>السيرفرات</h2><div id='guilds' class='list'>جاري التحميل...</div></div><div class='panel'><h2>Overview</h2><div id='overview' class='cards'>اختار سيرفر.</div></div></section><section class='panel'><h2>الأوامر</h2><div id='commands' class='muted'>اختار سيرفر باش تشوف الأوامر.</div></section></main><script>
const $=s=>document.querySelector(s);
async function api(u,o){const r=await fetch(u,o);if(r.status===401){location.href='/';return null;}if(!r.ok){let t='';try{const j=await r.json();t=j.detail||JSON.stringify(j)}catch(_){t=await r.text()}throw new Error(t||('HTTP '+r.status));}return r.json();}
function escapeHtml(v){return String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
async function loadGuilds(){const d=await api('/api/guilds');if(!d)return;const root=$('#guilds');root.innerHTML='';for(const g of d.guilds){const b=document.createElement('button');b.className='guild';b.textContent=g.name;b.onclick=()=>selectGuild(g.id,g.name);root.appendChild(b);}if(!d.guilds.length)root.innerHTML='<div class="muted">ما كاين حتى سيرفر متاح: خاصك Manage Server أو Administrator والبوت يكون داخل السيرفر.</div>';}
async function selectGuild(id,name){$('#overview').textContent='جاري التحميل...';$('#commands').textContent='جاري التحميل...';try{const [o,c]=await Promise.all([api(`/api/guilds/${id}/overview`),api(`/api/guilds/${id}/commands`)]);if(!o||!c)return;$('#overview').innerHTML=`<div class="card"><b>${escapeHtml(o.name)}</b><span>${o.members} أعضاء</span></div><div class="card"><b>${o.channels}</b><span>رومات</span></div><div class="card"><b>${o.roles}</b><span>رتب</span></div><div class="card"><b>${o.open_tickets}</b><span>تذاكر مفتوحة</span></div>`;$('#commands').innerHTML=c.commands.length?c.commands.map(x=>`<div class="cmd"><b>/${escapeHtml(x.name)}</b><span>${escapeHtml(x.description)}</span></div>`).join(''):'<div class="muted">لا توجد أوامر متاحة.</div>';}catch(e){const msg='❌ '+escapeHtml(e.message||'تعذر تحميل بيانات السيرفر.');$('#overview').textContent=msg;$('#commands').textContent=msg;}}
loadGuilds().catch(e=>{$('#guilds').textContent='❌ '+(e.message||'تعذر تحميل السيرفرات.');});
</script></body></html>"""


def create_app(bot):
    app = base.create_app(bot)

    _remove_route(app, "/", "GET")
    _remove_route(app, "/api/guilds", "GET")

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        if not base._session_for(request):
            return HTMLResponse(base._login_html(bool(__import__("os").environ.get("DISCORD_CLIENT_ID") and __import__("os").environ.get("DISCORD_CLIENT_SECRET"))), headers={"Cache-Control": "no-store"})
        return HTMLResponse(_dashboard_html(), headers={"Cache-Control": "no-store"})

    @app.get("/api/guilds")
    async def guilds(request: Request):
        session = await base._session_for(request) if False else None
        session = base._session_for(request)
        if not session:
            raise HTTPException(status_code=401, detail="تسجيل الدخول مطلوب")
        managed = session.get("managed_guilds") or {}
        visible = []
        for raw_id, item in managed.items():
            try:
                guild_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if bot.get_guild(guild_id) is not None:
                visible.append(item)
        return {"guilds": visible}

    return app
