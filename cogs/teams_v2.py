"""Professional verified clubs/national teams system with secure player offers."""
from __future__ import annotations
import asyncio, time
from typing import Optional
import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import EmbedFactory, EmbedColor

TTL = 48 * 60 * 60
DEFAULT_LIMIT = 15

def label(kind: str) -> str:
    return "منتخب وطني" if kind == "national" else "نادي"

class OfferView(discord.ui.View):
    def __init__(self, cog, offer_id: int):
        super().__init__(timeout=None); self.cog=cog; self.offer_id=offer_id
        for item in self.children:
            item.custom_id=f"teamoffer:{offer_id}:{'accept' if item.label == 'قبول العرض' else 'reject'}"
    @discord.ui.button(label="قبول العرض", style=discord.ButtonStyle.success, emoji="✅")
    async def accept(self, i: discord.Interaction, _: discord.ui.Button): await self.cog.process_offer(i,self.offer_id,True)
    @discord.ui.button(label="رفض العرض", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject(self, i: discord.Interaction, _: discord.ui.Button): await self.cog.process_offer(i,self.offer_id,False)

class ListView(discord.ui.View):
    def __init__(self,cog,guild_id:int):
        super().__init__(timeout=None); self.cog=cog; self.guild_id=guild_id
        self.children[0].custom_id=f"teamlist:{guild_id}"
    @discord.ui.button(label="تحديث",style=discord.ButtonStyle.secondary,emoji="🔄")
    async def update(self,i:discord.Interaction,_:discord.ui.Button):
        if i.guild_id != self.guild_id: return await i.response.send_message("❌ هذه اللوحة لا تخص هذا الخادم.",ephemeral=True)
        await i.response.edit_message(embed=await self.cog.list_embed(self.guild_id),view=self)

class SettingsView(discord.ui.View):
    def __init__(self,cog,guild_id:int,user_id:int): super().__init__(timeout=300); self.cog=cog; self.guild_id=guild_id; self.user_id=user_id
    async def interaction_check(self,i):
        if i.user.id!=self.user_id or not i.user.guild_permissions.administrator:
            await i.response.send_message("❌ لا يمكنك تعديل هذه الإعدادات.",ephemeral=True); return False
        return True
    @discord.ui.button(label="تحديد رتبة المدربين",style=discord.ButtonStyle.primary,emoji="🎯")
    async def coach(self,i,_): await i.response.send_modal(CoachModal(self.cog,self.guild_id))
    @discord.ui.button(label="إلغاء رتبة المدربين",style=discord.ButtonStyle.secondary,emoji="🧹")
    async def clear(self,i,_): await self.cog.set_coach(self.guild_id,None); await i.response.edit_message(embed=self.cog.settings_embed(self.guild_id),view=self)
    @discord.ui.button(label="تحديد الحد الأقصى للاعبين",style=discord.ButtonStyle.primary,emoji="👥",row=1)
    async def limit(self,i,_): await i.response.send_modal(LimitModal(self.cog,self.guild_id))
    @discord.ui.button(label="إغلاق",style=discord.ButtonStyle.danger,emoji="✖️",row=1)
    async def close(self,i,_): self.stop(); await i.response.edit_message(view=None)

class CoachModal(discord.ui.Modal,title="رتبة المدربين"):
    role_id=discord.ui.TextInput(label="معرّف الرتبة",min_length=5,max_length=25)
    def __init__(self,cog,guild_id): super().__init__(); self.cog=cog; self.guild_id=guild_id
    async def on_submit(self,i):
        try: rid=int(str(self.role_id.value).strip())
        except ValueError: return await i.response.send_message("❌ معرّف الرتبة غير صالح.",ephemeral=True)
        role=i.guild.get_role(rid) if i.guild else None
        if not role: return await i.response.send_message("❌ لم يتم العثور على الرتبة.",ephemeral=True)
        await self.cog.set_coach(self.guild_id,rid); await i.response.send_message("✅ تم حفظ رتبة المدربين.",ephemeral=True)

class LimitModal(discord.ui.Modal,title="حد اللاعبين"):
    value=discord.ui.TextInput(label="العدد",placeholder="15",min_length=1,max_length=2)
    def __init__(self,cog,guild_id): super().__init__(); self.cog=cog; self.guild_id=guild_id
    async def on_submit(self,i):
        try: n=int(str(self.value.value).strip())
        except ValueError: return await i.response.send_message("❌ أدخل رقمًا صحيحًا.",ephemeral=True)
        if not 1<=n<=50: return await i.response.send_message("❌ يجب أن يكون العدد بين 1 و50.",ephemeral=True)
        await self.cog.set_limit(self.guild_id,n); await i.response.send_message(f"✅ تم تحديد الحد الأقصى بـ **{n}** لاعبًا.",ephemeral=True)

class TeamsV2(commands.Cog):
    def __init__(self,bot): self.bot=bot; self.db=bot.db; self.lock=asyncio.Lock()
    async def cog_load(self):
        await self.db.execute("CREATE TABLE IF NOT EXISTS verified_teams(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,role_id INTEGER NOT NULL,name TEXT NOT NULL,emoji TEXT NOT NULL,team_type TEXT NOT NULL DEFAULT 'club',logo_url TEXT,active INTEGER NOT NULL DEFAULT 1,created_at REAL NOT NULL,UNIQUE(guild_id,role_id))")
        await self.db.execute("CREATE TABLE IF NOT EXISTS team_members(team_id INTEGER NOT NULL,guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,joined_at REAL NOT NULL,PRIMARY KEY(team_id,user_id),UNIQUE(guild_id,user_id),FOREIGN KEY(team_id) REFERENCES verified_teams(id) ON DELETE CASCADE)")
        await self.db.execute("CREATE TABLE IF NOT EXISTS team_offers(id INTEGER PRIMARY KEY AUTOINCREMENT,team_id INTEGER NOT NULL,guild_id INTEGER NOT NULL,player_id INTEGER NOT NULL,offered_by INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created_at REAL NOT NULL,expires_at REAL NOT NULL,FOREIGN KEY(team_id) REFERENCES verified_teams(id) ON DELETE CASCADE)")
        await self.db.execute("CREATE TABLE IF NOT EXISTS team_settings(guild_id INTEGER PRIMARY KEY,coach_role_id INTEGER,max_players INTEGER NOT NULL DEFAULT 15,list_channel_id INTEGER,list_message_id INTEGER,updated_at REAL NOT NULL)")
        now=time.time(); await self.db.execute("UPDATE team_offers SET status='expired' WHERE status='pending' AND expires_at<=?",(now,))
        for r in await self.db.fetchall("SELECT id FROM team_offers WHERE status='pending' AND expires_at>?",(now,)): self.bot.add_view(OfferView(self,int(r[0])))
        for r in await self.db.fetchall("SELECT guild_id,message_id FROM team_settings WHERE message_id IS NOT NULL"): self.bot.add_view(ListView(self,int(r[0])),message_id=int(r[1]))
    async def cfg(self,g):
        r=await self.db.fetchone("SELECT * FROM team_settings WHERE guild_id=?",(g,))
        if r:return dict(r)
        await self.db.execute("INSERT INTO team_settings(guild_id,updated_at) VALUES(?,?)",(g,time.time())); return dict(await self.db.fetchone("SELECT * FROM team_settings WHERE guild_id=?",(g,)))
    async def set_coach(self,g,r): await self.cfg(g); await self.db.execute("UPDATE team_settings SET coach_role_id=?,updated_at=? WHERE guild_id=?",(r,time.time(),g))
    async def set_limit(self,g,n): await self.cfg(g); await self.db.execute("UPDATE team_settings SET max_players=?,updated_at=? WHERE guild_id=?",(n,time.time(),g))
    def settings_embed(self,g): return EmbedFactory.create("⚙️ إعدادات الأندية والمنتخبات","تخصيص صلاحيات المدربين وحدود قوائم الفرق.",color=EmbedColor.ECONOMY)
    async def list_embed(self,g):
        ts=await self.db.fetchall("SELECT * FROM verified_teams WHERE guild_id=? AND active=1 ORDER BY team_type,id",(g,)); e=EmbedFactory.create("🏅 الفرق الموثقة","الأندية والمنتخبات الموثقة في هذا الخادم.",color=EmbedColor.ECONOMY)
        if not ts:e.description="لا توجد أندية أو منتخبات موثقة حاليًا."; return e
        cfg=await self.cfg(g); lim=int(cfg.get('max_players') or DEFAULT_LIMIT); lines=[]
        for t in ts:
            c=await self.db.fetchone("SELECT COUNT(*) FROM team_members WHERE team_id=?",(t['id'],)); lines.append(f"{t['emoji']} **{t['name']}** — <@&{t['role_id']}>\n└ 👥 **{int(c[0])}/{lim}** لاعبًا · {label(t['team_type'])}")
        e.description="\n\n".join(lines); return e
    async def coach_ok(self,i):
        if i.user.guild_permissions.administrator:return True
        c=await self.cfg(i.guild.id); rid=c.get('coach_role_id'); return bool(rid and any(r.id==int(rid) for r in i.user.roles))

    @app_commands.command(name="addteam",description="إضافة نادٍ أو منتخب موثق")
    @app_commands.describe(role="رتبة الفريق",emoji="رمز الفريق",team_type="نوع الفريق",logo_url="رابط الشعار (اختياري)")
    @app_commands.choices(team_type=[app_commands.Choice(name="نادي",value="club"),app_commands.Choice(name="منتخب وطني",value="national")])
    @app_commands.checks.has_permissions(administrator=True)
    async def addteam(self,i,role:discord.Role,emoji:str,team_type:app_commands.Choice[str],logo_url:Optional[str]=None):
        if not emoji.strip() or len(emoji.strip())>16:return await i.response.send_message("❌ رمز الفريق غير صالح.",ephemeral=True)
        if role.is_default() or role.managed:return await i.response.send_message("❌ لا يمكن استخدام هذه الرتبة.",ephemeral=True)
        if not i.guild.me or role>=i.guild.me.top_role:return await i.response.send_message("❌ يجب أن تكون رتبة الفريق أسفل أعلى رتبة للبوت.",ephemeral=True)
        try: await self.db.execute("INSERT INTO verified_teams(guild_id,role_id,name,emoji,team_type,logo_url,created_at) VALUES(?,?,?,?,?,?,?)",(i.guild.id,role.id,role.name,emoji.strip(),team_type.value,logo_url,time.time()))
        except Exception:return await i.response.send_message("❌ هذه الرتبة مسجلة بالفعل كفريق موثق.",ephemeral=True)
        await i.response.send_message(f"✅ تم توثيق {label(team_type.value)} **{role.name}** {emoji.strip()} بنجاح.",ephemeral=True)

    @app_commands.command(name="removeteam",description="إزالة فريق موثق")
    @app_commands.checks.has_permissions(administrator=True)
    async def removeteam(self,i,role:discord.Role):
        r=await self.db.fetchone("SELECT id,name FROM verified_teams WHERE guild_id=? AND role_id=? AND active=1",(i.guild.id,role.id))
        if not r:return await i.response.send_message("❌ هذا الفريق غير موثق.",ephemeral=True)
        await self.db.execute("UPDATE verified_teams SET active=0 WHERE id=?",(r[0],)); await self.db.execute("UPDATE team_offers SET status='cancelled' WHERE team_id=? AND status='pending'",(r[0],)); await i.response.send_message(f"✅ تمت إزالة توثيق **{r[1]}** وإلغاء عروضه المعلقة.",ephemeral=True)

    @app_commands.command(name="verifiedteams",description="عرض الأندية والمنتخبات الموثقة")
    async def verifiedteams(self,i):
        v=ListView(self,i.guild.id); await i.response.send_message(embed=await self.list_embed(i.guild.id),view=v); m=await i.original_response(); await self.cfg(i.guild.id); await self.db.execute("UPDATE team_settings SET list_channel_id=?,list_message_id=?,updated_at=? WHERE guild_id=?",(m.channel.id,m.id,time.time(),i.guild.id))

    @app_commands.command(name="offer",description="إرسال عرض انضمام للاعب")
    @app_commands.describe(player="اللاعب",role="رتبة الفريق")
    async def offer(self,i,player:discord.Member,role:discord.Role):
        if not await self.coach_ok(i):return await i.response.send_message("❌ هذا الأمر متاح فقط لمن يملك رتبة المدربين المحددة من الإدارة.",ephemeral=True)
        if player.bot or player.id==i.user.id:return await i.response.send_message("❌ لا يمكن إرسال عرض إلى هذا الحساب.",ephemeral=True)
        t=await self.db.fetchone("SELECT * FROM verified_teams WHERE guild_id=? AND role_id=? AND active=1",(i.guild.id,role.id))
        if not t:return await i.response.send_message("❌ هذه الرتبة ليست لفريق موثق.",ephemeral=True)
        member=await self.db.fetchone("SELECT t.name FROM team_members m JOIN verified_teams t ON t.id=m.team_id WHERE m.guild_id=? AND m.user_id=? AND t.active=1",(i.guild.id,player.id))
        if member:return await i.response.send_message(f"❌ هذا اللاعب مرتبط بالفعل بـ **{member[0]}**.",ephemeral=True)
        c=await self.db.fetchone("SELECT COUNT(*) FROM team_members WHERE team_id=?",(t['id'],)); cfg=await self.cfg(i.guild.id); lim=int(cfg.get('max_players') or DEFAULT_LIMIT)
        if int(c[0])>=lim:return await i.response.send_message(f"❌ قائمة الفريق مكتملة ({lim} لاعبًا).",ephemeral=True)
        old=await self.db.fetchone("SELECT id FROM team_offers WHERE team_id=? AND player_id=? AND status='pending' AND expires_at>?",(t['id'],player.id,time.time()))
        if old:return await i.response.send_message("❌ يوجد بالفعل عرض نشط لهذا اللاعب.",ephemeral=True)
        await i.response.defer(ephemeral=True); cur=await self.db.execute("INSERT INTO team_offers(team_id,guild_id,player_id,offered_by,created_at,expires_at) VALUES(?,?,?,?,?,?)",(t['id'],i.guild.id,player.id,i.user.id,time.time(),time.time()+TTL)); oid=int(cur.lastrowid)
        e=EmbedFactory.create("📩 عرض انضمام إلى فريق",f"تلقيت عرضًا للانضمام إلى **{t['name']}** {t['emoji']}.",color=EmbedColor.ECONOMY); e.add_field(name="النوع",value=label(t['team_type'])); e.add_field(name="المرسل",value=i.user.mention); e.add_field(name="الصلاحية",value="48 ساعة")
        if t['logo_url']:e.set_thumbnail(url=t['logo_url'])
        try: await player.send(embed=e,view=OfferView(self,oid))
        except discord.Forbidden: await self.db.execute("UPDATE team_offers SET status='failed' WHERE id=?",(oid,)); return await i.followup.send("❌ تعذر إرسال رسالة خاصة للاعب.",ephemeral=True)
        self.bot.add_view(OfferView(self,oid)); await i.followup.send(f"✅ تم إرسال العرض إلى {player.mention} في الخاص.",ephemeral=True)

    @app_commands.command(name="teamsettings",description="إعدادات الأندية والمنتخبات")
    @app_commands.checks.has_permissions(administrator=True)
    async def teamsettings(self,i): await i.response.send_message(embed=self.settings_embed(i.guild.id),view=SettingsView(self,i.guild.id,i.user.id),ephemeral=True)

    async def process_offer(self,i,oid,accept):
        async with self.lock:
            o=await self.db.fetchone("SELECT o.*,t.name,t.emoji,t.role_id,t.active FROM team_offers o JOIN verified_teams t ON t.id=o.team_id WHERE o.id=?",(oid,))
            if not o or o['status']!='pending' or int(o['player_id'])!=i.user.id:return await i.response.send_message("❌ هذا العرض غير صالح أو تم التعامل معه مسبقًا.",ephemeral=True)
            if o['expires_at']<=time.time():await self.db.execute("UPDATE team_offers SET status='expired' WHERE id=?",(oid,)); return await i.response.send_message("❌ انتهت صلاحية العرض.",ephemeral=True)
            if not accept:await self.db.execute("UPDATE team_offers SET status='rejected' WHERE id=? AND status='pending'",(oid,)); return await i.response.edit_message(content="❌ تم رفض عرض الانضمام.",embed=None,view=None)
            if not o['active']:await self.db.execute("UPDATE team_offers SET status='cancelled' WHERE id=? AND status='pending'",(oid,)); return await i.response.send_message("❌ لم يعد الفريق موثقًا.",ephemeral=True)
            if await self.db.fetchone("SELECT 1 FROM team_members WHERE guild_id=? AND user_id=?",(o['guild_id'],i.user.id)):await self.db.execute("UPDATE team_offers SET status='conflict' WHERE id=?",(oid,)); return await i.response.send_message("❌ أنت مرتبط بالفعل بفريق آخر.",ephemeral=True)
            cfg=await self.cfg(o['guild_id']); lim=int(cfg.get('max_players') or DEFAULT_LIMIT); c=await self.db.fetchone("SELECT COUNT(*) FROM team_members WHERE team_id=?",(o['team_id'],))
            if int(c[0])>=lim:await self.db.execute("UPDATE team_offers SET status='full' WHERE id=?",(oid,)); return await i.response.send_message("❌ اكتملت قائمة الفريق.",ephemeral=True)
            cur=await self.db.execute("INSERT OR IGNORE INTO team_members(team_id,guild_id,user_id,joined_at) VALUES(?,?,?,?)",(o['team_id'],o['guild_id'],i.user.id,time.time()))
            if cur.rowcount!=1:return await i.response.send_message("❌ تعذر تسجيل انضمامك؛ ربما تم قبول عرض آخر.",ephemeral=True)
            cur=await self.db.execute("UPDATE team_offers SET status='accepted' WHERE id=? AND status='pending'",(oid,))
            if cur.rowcount!=1:await self.db.execute("DELETE FROM team_members WHERE team_id=? AND guild_id=? AND user_id=?",(o['team_id'],o['guild_id'],i.user.id)); return await i.response.send_message("❌ تعذر إتمام العرض.",ephemeral=True)
        g=self.bot.get_guild(int(o['guild_id']))
        if g:
            m=g.get_member(i.user.id); r=g.get_role(int(o['role_id']))
            if m and r:
                try: await m.add_roles(r,reason="قبول عرض فريق موثق")
                except discord.Forbidden: return await i.response.edit_message(content=f"⚠️ تم تسجيل انضمامك إلى **{o['name']}**، لكن تعذر إعطاؤك رتبة الفريق. يجب رفع رتبة البوت فوق رتبة الفريق.",embed=None,view=None)
        await i.response.edit_message(content=f"✅ تم قبول العرض والانضمام إلى **{o['name']}** {o['emoji']} بنجاح.",embed=None,view=None)

async def setup(bot): await bot.add_cog(TeamsV2(bot))
