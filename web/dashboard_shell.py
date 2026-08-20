"""Robust dashboard entry shell.

This module wraps the existing Ader dashboard API and places a small,
server-rendered shell at `/` before the legacy client UI route. The shell is
intentionally dependency-light so a browser JavaScript parse/runtime error can
never result in an entirely blank page.
"""
from __future__ import annotations

from fastapi.responses import HTMLResponse
from starlette.routing import Route

from web.api_v2 import create_app as create_base_app


def _shell_html() -> str:
    return r'''<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Ader — Dashboard</title>
<style>
:root{color-scheme:dark;--bg:#070a12;--panel:#101625;--line:#27324a;--text:#f4f7fb;--muted:#9aa8bd;--accent:#7c5cff;--good:#31d58a;--bad:#ff6b78}
*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:radial-gradient(circle at top,#18203b 0,#070a12 58%);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Tahoma,Arial,sans-serif}
body{padding:18px}.wrap{width:min(1180px,100%);margin:0 auto}.top{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:18px}.brand{font-size:28px;font-weight:900}.muted{color:var(--muted)}.card{background:rgba(16,22,37,.96);border:1px solid var(--line);border-radius:18px;padding:20px;box-shadow:0 14px 40px rgba(0,0,0,.2)}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.metric{font-size:30px;font-weight:900;margin-top:5px}.btn{display:inline-block;border:0;border-radius:10px;padding:11px 15px;background:var(--accent);color:#fff;text-decoration:none;font-weight:800;cursor:pointer}.btn.secondary{background:#1a2338;border:1px solid var(--line)}select{background:#0b1220;border:1px solid var(--line);border-radius:10px;color:#fff;padding:10px 12px;min-width:240px}
#status{margin:0 0 16px}.login{text-align:center;padding:45px 20px}.error{border-color:rgba(255,107,120,.35)}.ok{border-color:rgba(49,213,138,.25)}
@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:560px){body{padding:12px}.grid{grid-template-columns:1fr}.brand{font-size:24px}select{width:100%}}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div><div class="brand">NOVA ARO</div><div class="muted">Ader Dashboard</div></div>
    <a class="btn secondary" href="/logout">تسجيل الخروج</a>
  </div>
  <div id="status" class="card">جاري تحميل لوحة التحكم…</div>
  <div id="app" class="card login">
    <h1>لوحة التحكم تعمل</h1>
    <p class="muted">جاري جلب بيانات الحساب والخوادم. هاد الـshell كيمنع الشاشة البيضاء حتى إلا وقع خطأ في JavaScript.</p>
    <a class="btn" href="/login">تسجيل الدخول بواسطة Discord</a>
  </div>
</div>
<script>
(function(){
  var status=document.getElementById('status');
  var app=document.getElementById('app');
  function esc(v){return String(v==null?'':v).replace(/[&<>"']/g,function(m){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m];});}
  function request(url){return fetch(url,{credentials:'same-origin',headers:{'Accept':'application/json'}}).then(function(r){return r.json().catch(function(){return {};}).then(function(d){if(!r.ok){throw new Error(d.detail||('HTTP '+r.status));}return d;});});}
  function render(data){
    var gs=data.guilds||[];
    if(!gs.length){
      status.className='card error';status.innerHTML='<strong>ماكاين حتى سيرفر مُدار.</strong><div class="muted" style="margin-top:6px">خاص الحساب تكون عندو Manage Server/Administrator والبوت يكون داخل السيرفر.</div>';
      app.innerHTML='<h2>لا توجد خوادم</h2><p class="muted">تأكد من صلاحيات Discord ثم عاود تسجيل الدخول.</p><a class="btn" href="/login">إعادة تسجيل الدخول</a>';
      return;
    }
    var options=gs.map(function(g){return '<option value="'+esc(g.id)+'">'+esc(g.name)+'</option>';}).join('');
    app.className='card';
    app.innerHTML='<div class="top"><div><h2 style="margin:0">السيرفر</h2><div class="muted">اختار السيرفر لإظهار الإحصائيات.</div></div><select id="guild">'+options+'</select></div><div id="stats" class="grid"><div class="card"><div class="muted">الأعضاء</div><div class="metric">—</div></div><div class="card"><div class="muted">القنوات</div><div class="metric">—</div></div><div class="card"><div class="muted">التذاكر المفتوحة</div><div class="metric">—</div></div><div class="card"><div class="muted">الفرق الموثقة</div><div class="metric">—</div></div></div><div style="margin-top:18px"><a class="btn" href="/api/docs">API Docs</a></div>';
    var sel=document.getElementById('guild');
    function load(){
      request('/api/guilds/'+encodeURIComponent(sel.value)+'/overview').then(function(d){
        status.className='card ok';status.innerHTML='<strong>Dashboard متصلة ✅</strong><div class="muted" style="margin-top:6px">'+esc(d.name)+'</div>';
        document.getElementById('stats').innerHTML=[['👥','الأعضاء',d.members],['📺','القنوات',d.channels],['🎫','التذاكر المفتوحة',d.open_tickets],['🏆','الفرق الموثقة',d.verified_teams]].map(function(x){return '<div class="card"><div class="muted">'+x[0]+' '+x[1]+'</div><div class="metric">'+esc(x[2])+'</div></div>';}).join('');
      }).catch(function(e){status.className='card error';status.innerHTML='<strong>تعذر جلب بيانات السيرفر.</strong><div class="muted" style="margin-top:6px">'+esc(e.message)+'</div>';});
    }
    sel.addEventListener('change',load);load();
  }
  request('/api/me').then(function(me){
    if(!me.logged_in){status.className='card';status.innerHTML='<strong>خاص تسجيل الدخول</strong><div class="muted" style="margin-top:6px">سجل الدخول بواسطة Discord باش تظهر ليك السيرفرات.</div>';return;}
    return request('/api/guilds').then(render);
  }).catch(function(e){status.className='card error';status.innerHTML='<strong>خطأ في Dashboard</strong><div class="muted" style="margin-top:6px">'+esc(e.message)+'</div>';});
})();
</script>
</body></html>'''


def create_app(bot):
    app = create_base_app(bot)

    async def shell(request):
        return HTMLResponse(_shell_html(), headers={"Cache-Control": "no-store"})

    # Starlette resolves routes in order. Put this deterministic root shell in
    # front of the old API-v2 HTML route so a client-side crash cannot produce
    # a totally blank page. All existing `/api/*` endpoints remain untouched.
    app.router.routes.insert(0, Route('/', shell, methods=['GET']))
    return app
