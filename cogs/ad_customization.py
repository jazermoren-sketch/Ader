from __future__ import annotations

import json
import re
import time

import discord
from discord import app_commands
from discord.ext import commands

EVENTS = {
    "after_ad": "بعد الإعلان",
    "after_giveaway": "بعد القيف أواي",
    "after_image": "بعد الصورة",
    "after_all": "بعد اكتمال الإعلان",
}


def strip_mentions(text: str) -> str:
    text = re.sub(r"<@!?\d+>", "", text or "")
    text = re.sub(r"<@&\d+>", "", text)
    return text.replace("@everyone", "").replace("@here", "").strip()


def parse_duration(value: str) -> int:
    raw = (value or "").strip().lower().replace(" ", "")
    if raw.isdigit():
        seconds = int(raw) * 60
    else:
        match = re.fullmatch(r"(\d+)([smhd])", raw)
        if not match:
            raise ValueError("مدة غير صالحة")
        seconds = int(match.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]
    if seconds < 10 or seconds > 30 * 86400:
        raise ValueError("مدة غير صالحة")
    return seconds


class GiveawaySettingsModal(discord.ui.Modal, title="إعدادات قيف أواي ANORIS"):
    enabled_input = discord.ui.TextInput(label="تفعيل القيف أواي", default="نعم", max_length=10)
    amount_input = discord.ui.TextInput(label="مبلغ جائزة ANORIS", default="3000000", max_length=20)
    duration_input = discord.ui.TextInput(label="مدة القيف أواي", default="1h", max_length=20)
    def __init__(self, cog, row=None):
        super().__init__(); self.cog = cog
        if row:
            self.enabled_input.default = "نعم" if int(row["giveaway_enabled"] or 0) else "لا"
            self.amount_input.default = str(int(row["giveaway_amount"] or 3000000))
            self.duration_input.default = f"{int(row['giveaway_duration'] or 3600)}s"
    async def on_submit(self, interaction):
        if not self.cog.is_admin(interaction): return await interaction.response.send_message("❌ تحتاج إلى Manage Server أو Administrator.", ephemeral=True)
        raw = str(self.enabled_input.value).strip().lower()
        if raw in {"نعم","yes","y","on","1","true"}: enabled = 1
        elif raw in {"لا","no","n","off","0","false"}: enabled = 0
        else: return await interaction.response.send_message("❌ اكتب نعم أو لا.", ephemeral=True)
        try:
            amount = int(str(self.amount_input.value).replace(",", "").replace(" ", "")); duration = parse_duration(str(self.duration_input.value))
            if amount <= 0: raise ValueError
        except ValueError: return await interaction.response.send_message("❌ تحقق من المبلغ والمدة.", ephemeral=True)
        await self.cog.db.execute("INSERT INTO ad_settings_v2(guild_id,post_message,giveaway_enabled,giveaway_amount,giveaway_duration,giveaway_sponsor_id,image_path,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(guild_id) DO UPDATE SET giveaway_enabled=excluded.giveaway_enabled,giveaway_amount=excluded.giveaway_amount,giveaway_duration=excluded.giveaway_duration,giveaway_sponsor_id=excluded.giveaway_sponsor_id,updated_at=excluded.updated_at", (interaction.guild.id,"",enabled,amount,duration,interaction.user.id,None,time.time()))
        await interaction.response.send_message(f"✅ تم حفظ القيف أواي: **{'مفعّل' if enabled else 'معطّل'}**", ephemeral=True)


class MessageModal(discord.ui.Modal):
    def __init__(self, cog, message_id=None, row=None):
        super().__init__(title="إضافة رسالة" if message_id is None else "تعديل رسالة"); self.cog=cog; self.message_id=message_id
        self.name_input=discord.ui.TextInput(label="اسم الرسالة",max_length=80); self.content_input=discord.ui.TextInput(label="نص الرسالة",style=discord.TextStyle.paragraph,max_length=4000)
        if row: self.name_input.default=str(row["name"]); self.content_input.default=str(row["content"])
        self.add_item(self.name_input); self.add_item(self.content_input)
    async def on_submit(self,i):
        if not self.cog.is_admin(i): return await i.response.send_message("❌ تحتاج إلى Manage Server أو Administrator.",ephemeral=True)
        name=str(self.name_input.value).strip(); content=strip_mentions(str(self.content_input.value))
        if not name or not content:return await i.response.send_message("❌ الرسالة غير صالحة.",ephemeral=True)
        if self.message_id is None:
            await self.cog.db.execute("INSERT INTO ad_custom_messages(guild_id,name,content,event,reply_to,enabled,position,created_at) VALUES(?,?,?,?,?,?,?,?)",(i.guild.id,name,content,"after_ad",None,1,await self.cog.next_position(i.guild.id),time.time()))
            return await i.response.send_message("✅ تمت إضافة الرسالة.",ephemeral=True)
        await self.cog.db.execute("UPDATE ad_custom_messages SET name=?,content=? WHERE id=? AND guild_id=?",(name,content,self.message_id,i.guild.id)); await i.response.send_message("✅ تم تعديل الرسالة.",ephemeral=True)


class EventSelect(discord.ui.Select):
    def __init__(self,cog): super().__init__(placeholder="اختر توقيت الرسالة",options=[discord.SelectOption(label=v,value=k) for k,v in EVENTS.items()],row=0); self.cog=cog
    async def callback(self,i):
        if not self.cog.is_admin(i):return await i.response.send_message("❌ تحتاج إلى Manage Server أو Administrator.",ephemeral=True)
        self.cog.selected_event[(i.guild_id or 0,i.user.id)]=self.values[0]; await i.response.send_message(f"✅ تم اختيار: **{EVENTS[self.values[0]]}**",ephemeral=True)

class MessageSelect(discord.ui.Select):
    def __init__(self,cog,rows):
        options=[discord.SelectOption(label=str(r['name'])[:100],value=str(r['id']),description=EVENTS.get(str(r['event']),'مخصص')) for r in rows[:25]] or [discord.SelectOption(label='لا توجد رسائل',value='0')]
        super().__init__(placeholder='اختر رسالة لتخصيصها',options=options,row=1);self.cog=cog
    async def callback(self,i):
        if not self.cog.is_admin(i):return await i.response.send_message('❌ تحتاج إلى Manage Server أو Administrator.',ephemeral=True)
        mid=int(self.values[0]) if self.values[0].isdigit() else 0
        if not mid:return await i.response.send_message('❌ لا توجد رسالة للاختيار.',ephemeral=True)
        self.cog.selected_message[(i.guild_id or 0,i.user.id)]=mid;await i.response.send_message('✅ تم اختيار الرسالة.',ephemeral=True)

class ReplySelect(discord.ui.Select):
    def __init__(self,cog,rows):
        options=[discord.SelectOption(label='بدون Reply',value='none')]+[discord.SelectOption(label=str(r['name'])[:100],value=str(r['id'])) for r in rows[:24]]
        super().__init__(placeholder='اختر رسالة تكون الحالية Reply لها',options=options,row=2);self.cog=cog
    async def callback(self,i):
        if not self.cog.is_admin(i):return await i.response.send_message('❌ تحتاج إلى Manage Server أو Administrator.',ephemeral=True)
        mid=self.cog.selected_message.get((i.guild_id or 0,i.user.id))
        if not mid:return await i.response.send_message('❌ اختر الرسالة الحالية أولاً.',ephemeral=True)
        reply_to=None if self.values[0]=='none' else int(self.values[0])
        if reply_to==mid:return await i.response.send_message('❌ لا يمكن للرسالة أن تعمل Reply لنفسها.',ephemeral=True)
        await self.cog.db.execute('UPDATE ad_custom_messages SET reply_to=? WHERE id=? AND guild_id=?',(reply_to,mid,i.guild.id));await i.response.send_message('✅ تم حفظ إعداد الـReply.',ephemeral=True)

class RoleSelect(discord.ui.RoleSelect):
    def __init__(self,cog):super().__init__(placeholder='اختر رتبة لتفعيل/إزالة صلاحية $اعلان',min_values=1,max_values=1,row=3);self.cog=cog
    async def callback(self,i):
        if not self.cog.is_admin(i):return await i.response.send_message('❌ تحتاج إلى Manage Server أو Administrator.',ephemeral=True)
        role_id=self.values[0].id;row=await self.cog.db.fetchone('SELECT allowed_roles FROM ad_settings WHERE guild_id=?',(i.guild.id,))
        try:roles=set(json.loads(row['allowed_roles'] or '[]')) if row else set()
        except Exception:roles=set()
        if role_id in roles:roles.remove(role_id);action='تمت إزالة الرتبة من الرتب المسموحة'
        else:roles.add(role_id);action='تمت إضافة الرتبة إلى الرتب المسموحة'
        await self.cog.save_allowed_roles(i.guild.id,roles);await i.response.send_message(f'✅ {action}.',ephemeral=True)

class SettingsView(discord.ui.View):
    def __init__(self,cog,rows):super().__init__(timeout=300);self.cog=cog;self.add_item(EventSelect(cog));self.add_item(MessageSelect(cog,rows));self.add_item(ReplySelect(cog,rows));self.add_item(RoleSelect(cog))
    async def guard(self,i):
        if not self.cog.is_admin(i):await i.response.send_message('❌ تحتاج إلى Manage Server أو Administrator.',ephemeral=True);return False
        return True
    def _key(self,i):return (i.guild_id or 0,i.user.id)
    @discord.ui.button(label='إضافة',emoji='➕',style=discord.ButtonStyle.success,row=4)
    async def add(self,i,b):
        if await self.guard(i):await i.response.send_modal(MessageModal(self.cog))
    @discord.ui.button(label='تعديل',emoji='📝',style=discord.ButtonStyle.primary,row=4)
    async def edit(self,i,b):
        if not await self.guard(i):return
        mid=self.cog.selected_message.get(self._key(i));row=await self.cog.db.fetchone('SELECT * FROM ad_custom_messages WHERE id=? AND guild_id=?',(mid,i.guild.id)) if mid else None
        if not row:return await i.response.send_message('❌ اختر رسالة أولاً.',ephemeral=True)
        await i.response.send_modal(MessageModal(self.cog,mid,row))
    @discord.ui.button(label='حذف',emoji='🗑️',style=discord.ButtonStyle.danger,row=4)
    async def delete(self,i,b):
        if not await self.guard(i):return
        mid=self.cog.selected_message.get(self._key(i))
        if not mid:return await i.response.send_message('❌ اختر رسالة أولاً.',ephemeral=True)
        await self.cog.db.execute('DELETE FROM ad_custom_messages WHERE id=? AND guild_id=?',(mid,i.guild.id));self.cog.selected_message.pop(self._key(i),None);await self.cog.show_panel(i,edit=True)
    @discord.ui.button(label='تفعيل',emoji='🔘',style=discord.ButtonStyle.secondary,row=4)
    async def toggle(self,i,b):
        if not await self.guard(i):return
        mid=self.cog.selected_message.get(self._key(i));row=await self.cog.db.fetchone('SELECT enabled FROM ad_custom_messages WHERE id=? AND guild_id=?',(mid,i.guild.id)) if mid else None
        if not row:return await i.response.send_message('❌ اختر رسالة أولاً.',ephemeral=True)
        await self.cog.db.execute('UPDATE ad_custom_messages SET enabled=? WHERE id=? AND guild_id=?',(0 if int(row['enabled']) else 1,mid,i.guild.id));await self.cog.show_panel(i,edit=True)
    @discord.ui.button(label='قيف أواي',emoji='🎁',style=discord.ButtonStyle.success,row=4)
    async def giveaway(self,i,b):
        if not await self.guard(i):return
        row=await self.cog.db.fetchone('SELECT * FROM ad_settings_v2 WHERE guild_id=?',(i.guild.id,));await i.response.send_modal(GiveawaySettingsModal(self.cog,row))

class AdCustomization(commands.Cog):
    def __init__(self,bot):self.bot=bot;self.db=bot.db;self.selected_event={};self.selected_message={}
    async def _columns(self,table):
        rows=await self.db.fetchall(f'PRAGMA table_info({table})');return {str(r[1]) for r in rows}
    async def _ensure_column(self,table,column,definition):
        if column not in await self._columns(table):await self.db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
    async def cog_load(self):
        await self.db.execute("CREATE TABLE IF NOT EXISTS ad_custom_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,name TEXT NOT NULL,content TEXT NOT NULL,event TEXT NOT NULL DEFAULT 'after_ad',reply_to INTEGER,enabled INTEGER NOT NULL DEFAULT 1,position INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL DEFAULT 0)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_ad_custom_guild_event ON ad_custom_messages(guild_id,event,position)")
        await self.db.execute("CREATE TABLE IF NOT EXISTS ad_settings(guild_id INTEGER PRIMARY KEY,allowed_roles TEXT NOT NULL DEFAULT '[]')")
        await self.db.execute("CREATE TABLE IF NOT EXISTS ad_settings_v2(guild_id INTEGER PRIMARY KEY,post_message TEXT NOT NULL DEFAULT '',giveaway_enabled INTEGER NOT NULL DEFAULT 0,giveaway_amount INTEGER NOT NULL DEFAULT 3000000,giveaway_duration INTEGER NOT NULL DEFAULT 3600,giveaway_sponsor_id INTEGER,image_path TEXT,updated_at REAL NOT NULL DEFAULT 0)")
        for col,definition in [('post_message',"TEXT NOT NULL DEFAULT ''"),('giveaway_enabled','INTEGER NOT NULL DEFAULT 0'),('giveaway_amount','INTEGER NOT NULL DEFAULT 3000000'),('giveaway_duration','INTEGER NOT NULL DEFAULT 3600'),('giveaway_sponsor_id','INTEGER'),('image_path','TEXT'),('updated_at','REAL NOT NULL DEFAULT 0')]:await self._ensure_column('ad_settings_v2',col,definition)
        for col,definition in [('event',"TEXT NOT NULL DEFAULT 'after_ad'"),('reply_to','INTEGER'),('enabled','INTEGER NOT NULL DEFAULT 1'),('position','INTEGER NOT NULL DEFAULT 0'),('created_at','REAL NOT NULL DEFAULT 0')]:await self._ensure_column('ad_custom_messages',col,definition)
    def is_admin(self,i):return bool(i.guild and (i.user.guild_permissions.administrator or i.user.guild_permissions.manage_guild))
    async def next_position(self,guild_id):row=await self.db.fetchone('SELECT COALESCE(MAX(position),0)+1 AS p FROM ad_custom_messages WHERE guild_id=?',(guild_id,));return int(row['p'])
    async def save_allowed_roles(self,guild_id,roles):await self.db.execute('INSERT INTO ad_settings(guild_id,allowed_roles) VALUES(?,?) ON CONFLICT(guild_id) DO UPDATE SET allowed_roles=excluded.allowed_roles',(guild_id,json.dumps(sorted(int(x) for x in roles))))
    async def show_panel(self,i,edit=False):
        try:
            rows=await self.db.fetchall('SELECT * FROM ad_custom_messages WHERE guild_id=? ORDER BY position,id',(i.guild.id,));g=await self.db.fetchone('SELECT giveaway_enabled,giveaway_amount,giveaway_duration FROM ad_settings_v2 WHERE guild_id=?',(i.guild.id,))
            embed=discord.Embed(title='⚙️ تخصيص نظام الإعلانات',description='اختر الرسالة والتوقيت، واضبط الـReply والرتب والقيف أواي التلقائي.',colour=discord.Colour.blurple())
            for event,label,emoji in [('after_ad','بعد الإعلان','📢'),('after_giveaway','بعد القيف أواي','🎁'),('after_image','بعد الصورة','🖼️'),('after_all','بعد الاكتمال','🔗')]:embed.add_field(name=f'{emoji} {label}',value=f"**{sum(str(r['event'])==event and int(r['enabled']) for r in rows)}** رسالة",inline=True)
            embed.add_field(name='🎁 القيف أواي التلقائي',value=f"**{int(g['giveaway_amount']):,} ANORIS** لمدة **{int(g['giveaway_duration'])} ثانية**" if g and int(g['giveaway_enabled']) else 'معطّل — اضغط زر **قيف أواي** لتفعيله.',inline=False)
            if edit and i.message:await i.response.edit_message(embed=embed,view=SettingsView(self,rows))
            else:await i.response.send_message(embed=embed,view=SettingsView(self,rows),ephemeral=True)
        except Exception as exc:
            self.bot.logger.error('ad-settings failed: %s',exc,exc_info=True)
            if i.response.is_done():await i.followup.send('❌ حدث خطأ أثناء فتح الإعدادات. تحقق من Logs البوت.',ephemeral=True)
            else:await i.response.send_message('❌ حدث خطأ أثناء فتح الإعدادات. تحقق من Logs البوت.',ephemeral=True)
    @app_commands.command(name='ad-settings',description='تخصيص نظام الإعلانات')
    async def ad_settings(self,i):
        if not self.is_admin(i):return await i.response.send_message('❌ تحتاج إلى Manage Server أو Administrator.',ephemeral=True)
        await self.show_panel(i)

async def setup(bot):await bot.add_cog(AdCustomization(bot))
