from __future__ import annotations

import json
import re

import discord
from discord.ext import commands

EVENTS = {"after_ad": "بعد الإعلان", "after_giveaway": "بعد القيف أواي", "after_image": "بعد الصورة", "after_all": "بعد اكتمال الإعلان"}


def strip_mentions(text: str) -> str:
    text = re.sub(r"<@!?\d+>", "", text or "")
    text = re.sub(r"<@&\d+>", "", text)
    return text.replace("@everyone", "").replace("@here", "").strip()


class MessageModal(discord.ui.Modal):
    content = discord.ui.TextInput(label="نص الرسالة", style=discord.TextStyle.paragraph, max_length=4000, required=True)
    name = discord.ui.TextInput(label="اسم الرسالة", max_length=80, required=True)
    def __init__(self, cog, message_id=None):
        super().__init__(title="إضافة رسالة" if message_id is None else "تعديل رسالة"); self.cog, self.message_id = cog, message_id
    async def on_submit(self, interaction):
        if not self.cog.is_admin(interaction): return await interaction.response.send_message("❌ Administrator فقط.", ephemeral=True)
        name, content = str(self.name.value).strip(), strip_mentions(str(self.content.value))
        if not content: return await interaction.response.send_message("❌ الرسالة فارغة بعد إزالة المنشنات.", ephemeral=True)
        if self.message_id is None:
            await self.cog.db.execute("INSERT INTO ad_custom_messages(guild_id,name,content,event,reply_to,enabled,position,created_at) VALUES(?,?,?,?,?,?,?,strftime('%s','now'))", (interaction.guild.id,name,content,"after_ad",None,1,await self.cog.next_position(interaction.guild.id)))
            text="✅ تمت إضافة الرسالة."
        else:
            await self.cog.db.execute("UPDATE ad_custom_messages SET name=?,content=? WHERE id=? AND guild_id=?",(name,content,self.message_id,interaction.guild.id)); text="✅ تم تعديل الرسالة."
        await interaction.response.send_message(text,ephemeral=True); await self.cog.show_panel(interaction,edit=True)


class EventSelect(discord.ui.Select):
    def __init__(self,cog):
        super().__init__(placeholder="اختر متى تُرسل الرسائل",options=[discord.SelectOption(label=v,value=k) for k,v in EVENTS.items()],custom_id="ader:adsettings:event"); self.cog=cog
    async def callback(self,interaction):
        if not self.cog.is_admin(interaction): return await interaction.response.send_message("❌ Administrator فقط.",ephemeral=True)
        self.cog.selected_event[interaction.user.id]=self.values[0]; await interaction.response.send_message(f"✅ تم اختيار: **{EVENTS[self.values[0]]}**",ephemeral=True)


class MessageSelect(discord.ui.Select):
    def __init__(self,cog,rows):
        opts=[discord.SelectOption(label=str(r["name"])[:100],value=str(r["id"]),description=EVENTS.get(str(r["event"]),"مخصص")) for r in rows[:25]]
        super().__init__(placeholder="اختر رسالة لتخصيصها",options=opts or [discord.SelectOption(label="لا توجد رسائل",value="0")],custom_id="ader:adsettings:message"); self.cog=cog
    async def callback(self,interaction):
        if not self.cog.is_admin(interaction): return await interaction.response.send_message("❌ Administrator فقط.",ephemeral=True)
        try: mid=int(self.values[0])
        except ValueError: mid=0
        if mid<=0: return await interaction.response.send_message("❌ اختر رسالة موجودة.",ephemeral=True)
        self.cog.selected_message[interaction.user.id]=mid; await interaction.response.send_message("✅ تم اختيار الرسالة.",ephemeral=True)


class ReplySelect(discord.ui.Select):
    def __init__(self,cog,rows):
        opts=[discord.SelectOption(label="بدون Reply",value="none")]+[discord.SelectOption(label=str(r["name"])[:100],value=str(r["id"])) for r in rows[:24]]
        super().__init__(placeholder="اختر رسالة تكون هذه الرسالة Reply لها",options=opts,custom_id="ader:adsettings:reply"); self.cog=cog
    async def callback(self,interaction):
        if not self.cog.is_admin(interaction): return await interaction.response.send_message("❌ Administrator فقط.",ephemeral=True)
        mid=self.cog.selected_message.get(interaction.user.id)
        if not mid: return await interaction.response.send_message("❌ اختر الرسالة أولاً.",ephemeral=True)
        value=None if self.values[0]=="none" else int(self.values[0])
        if value==mid: return await interaction.response.send_message("❌ لا يمكن للرسالة أن تكون Reply لنفسها.",ephemeral=True)
        await self.cog.db.execute("UPDATE ad_custom_messages SET reply_to=? WHERE id=? AND guild_id=?",(value,mid,interaction.guild.id)); await interaction.response.send_message("✅ تم حفظ الـReply.",ephemeral=True)


class RoleSelect(discord.ui.RoleSelect):
    def __init__(self,cog):
        super().__init__(placeholder="اختر رتبة للسماح لها بـ $اعلان",min_values=1,max_values=1,custom_id="ader:adsettings:role"); self.cog=cog
    async def callback(self,interaction):
        if not self.cog.is_admin(interaction): return await interaction.response.send_message("❌ Administrator فقط.",ephemeral=True)
        self.cog.selected_role[interaction.user.id]=self.values[0].id; await interaction.response.send_message(f"✅ تم اختيار {self.values[0].mention}.",ephemeral=True)


class SettingsView(discord.ui.View):
    def __init__(self,cog,rows):
        super().__init__(timeout=300); self.cog=cog
        self.add_item(EventSelect(cog)); self.add_item(MessageSelect(cog,rows)); self.add_item(ReplySelect(cog,rows)); self.add_item(RoleSelect(cog))
    async def guard(self,interaction):
        if not self.cog.is_admin(interaction): await interaction.response.send_message("❌ Administrator فقط.",ephemeral=True); return False
        return True
    @discord.ui.button(label="إضافة رسالة",emoji="➕",style=discord.ButtonStyle.success,row=4,custom_id="ader:adsettings:add")
    async def add(self,interaction,button):
        if await self.guard(interaction): await interaction.response.send_modal(MessageModal(self.cog))
    @discord.ui.button(label="تعديل",emoji="📝",style=discord.ButtonStyle.primary,row=4,custom_id="ader:adsettings:edit")
    async def edit(self,interaction,button):
        if not await self.guard(interaction): return
        mid=self.cog.selected_message.get(interaction.user.id)
        if not mid: return await interaction.response.send_message("❌ اختر رسالة أولاً.",ephemeral=True)
        row=await self.cog.db.fetchone("SELECT * FROM ad_custom_messages WHERE id=? AND guild_id=?",(mid,interaction.guild.id))
        if not row: return await interaction.response.send_message("❌ الرسالة غير موجودة.",ephemeral=True)
        modal=MessageModal(self.cog,mid); modal.content.default=str(row["content"]); modal.name.default=str(row["name"]); await interaction.response.send_modal(modal)
    @discord.ui.button(label="حذف",emoji="🗑️",style=discord.ButtonStyle.danger,row=4,custom_id="ader:adsettings:delete")
    async def delete(self,interaction,button):
        if not await self.guard(interaction): return
        mid=self.cog.selected_message.get(interaction.user.id)
        if not mid: return await interaction.response.send_message("❌ اختر رسالة أولاً.",ephemeral=True)
        await self.cog.db.execute("DELETE FROM ad_custom_messages WHERE id=? AND guild_id=?",(mid,interaction.guild.id)); await interaction.response.send_message("✅ تم حذف الرسالة.",ephemeral=True); await self.cog.show_panel(interaction,edit=True)
    @discord.ui.button(label="تفعيل/تعطيل",emoji="🔘",style=discord.ButtonStyle.secondary,row=4,custom_id="ader:adsettings:toggle")
    async def toggle(self,interaction,button):
        if not await self.guard(interaction): return
        mid=self.cog.selected_message.get(interaction.user.id)
        if not mid: return await interaction.response.send_message("❌ اختر رسالة أولاً.",ephemeral=True)
        row=await self.cog.db.fetchone("SELECT enabled FROM ad_custom_messages WHERE id=? AND guild_id=?",(mid,interaction.guild.id))
        if not row: return await interaction.response.send_message("❌ الرسالة غير موجودة.",ephemeral=True)
        await self.cog.db.execute("UPDATE ad_custom_messages SET enabled=? WHERE id=?",(0 if int(row["enabled"]) else 1,mid)); await interaction.response.send_message("✅ تم تغيير حالة الرسالة.",ephemeral=True); await self.cog.show_panel(interaction,edit=True)
    @discord.ui.button(label="إضافة رتبة",emoji="➕",style=discord.ButtonStyle.success,row=5,custom_id="ader:adsettings:roleadd")
    async def role_add(self,interaction,button):
        if not await self.guard(interaction): return
        rid=self.cog.selected_role.get(interaction.user.id)
        if not rid: return await interaction.response.send_message("❌ اختر رتبة أولاً.",ephemeral=True)
        row=await self.cog.db.fetchone("SELECT allowed_roles FROM ad_settings WHERE guild_id=?",(interaction.guild.id,))
        try: roles=set(json.loads(row["allowed_roles"] or "[]")) if row else set()
        except Exception: roles=set()
        roles.add(rid); await self.cog.db.execute("INSERT INTO ad_settings(guild_id,allowed_roles) VALUES(?,?) ON CONFLICT(guild_id) DO UPDATE SET allowed_roles=excluded.allowed_roles",(interaction.guild.id,json.dumps(sorted(roles)))); await interaction.response.send_message("✅ تمت إضافة الرتبة إلى الرتب المسموحة.",ephemeral=True)
    @discord.ui.button(label="حذف رتبة",emoji="➖",style=discord.ButtonStyle.danger,row=5,custom_id="ader:adsettings:roleremove")
    async def role_remove(self,interaction,button):
        if not await self.guard(interaction): return
        rid=self.cog.selected_role.get(interaction.user.id)
        if not rid: return await interaction.response.send_message("❌ اختر رتبة أولاً.",ephemeral=True)
        row=await self.cog.db.fetchone("SELECT allowed_roles FROM ad_settings WHERE guild_id=?",(interaction.guild.id,))
        try: roles=set(json.loads(row["allowed_roles"] or "[]")) if row else set()
        except Exception: roles=set()
        roles.discard(rid); await self.cog.db.execute("INSERT INTO ad_settings(guild_id,allowed_roles) VALUES(?,?) ON CONFLICT(guild_id) DO UPDATE SET allowed_roles=excluded.allowed_roles",(interaction.guild.id,json.dumps(sorted(roles)))); await interaction.response.send_message("✅ تمت إزالة الرتبة من الرتب المسموحة.",ephemeral=True)
    @discord.ui.button(label="حفظ الحدث",emoji="💾",style=discord.ButtonStyle.primary,row=5,custom_id="ader:adsettings:eventsave")
    async def save_event(self,interaction,button):
        if not await self.guard(interaction): return
        mid=self.cog.selected_message.get(interaction.user.id); event=self.cog.selected_event.get(interaction.user.id,"after_ad")
        if not mid: return await interaction.response.send_message("❌ اختر رسالة أولاً.",ephemeral=True)
        await self.cog.db.execute("UPDATE ad_custom_messages SET event=? WHERE id=? AND guild_id=?",(event,mid,interaction.guild.id)); await interaction.response.send_message(f"✅ تم حفظ الحدث: **{EVENTS[event]}**",ephemeral=True); await self.cog.show_panel(interaction,edit=True)


class AdCustomization(commands.Cog):
    def __init__(self,bot): self.bot,self.db=bot,bot.db; self.selected_event={}; self.selected_message={}; self.selected_role={}
    async def cog_load(self):
        await self.db.execute("CREATE TABLE IF NOT EXISTS ad_custom_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,name TEXT NOT NULL,content TEXT NOT NULL,event TEXT NOT NULL DEFAULT 'after_ad',reply_to INTEGER,enabled INTEGER NOT NULL DEFAULT 1,position INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL DEFAULT 0)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_ad_custom_guild_event ON ad_custom_messages(guild_id,event,position)")
    def is_admin(self,interaction): return bool(interaction.guild and interaction.user.guild_permissions.administrator)
    async def next_position(self,guild_id):
        row=await self.db.fetchone("SELECT COALESCE(MAX(position),0)+1 AS p FROM ad_custom_messages WHERE guild_id=?",(guild_id,)); return int(row["p"])
    async def show_panel(self,interaction,edit=False):
        rows=await self.db.fetchall("SELECT * FROM ad_custom_messages WHERE guild_id=? ORDER BY position,id",(interaction.guild.id,))
        embed=discord.Embed(title="⚙️ تخصيص نظام الإعلانات",description="الرسائل، توقيتها، الـReply، والصلاحيات كلها من نفس اللوحة.",colour=discord.Colour.blurple())
        for event,label,emoji in [("after_ad","بعد الإعلان","📢"),("after_giveaway","بعد القيف أواي","🎁"),("after_image","بعد الصورة","🖼️"),("after_all","بعد الاكتمال","🔗")]: embed.add_field(name=f"{emoji} {label}",value=f"**{sum(r['event']==event and int(r['enabled']) for r in rows)}** رسالة",inline=True)
        if rows:
            lines=[]
            for r in rows[:20]: lines.append(f"{'✅' if int(r['enabled']) else '⛔'} `{r['id']}` **{r['name']}** — {EVENTS.get(r['event'],r['event'])}{f' → Reply #{r[\"reply_to\"]}' if r['reply_to'] else ''}")
            embed.add_field(name="📋 الرسائل",value="\n".join(lines),inline=False)
        else: embed.add_field(name="📋 الرسائل",value="لا توجد رسائل مخصصة بعد.",inline=False)
        view=SettingsView(self,rows)
        if edit and interaction.message: await interaction.message.edit(embed=embed,view=view)
        else: await interaction.response.send_message(embed=embed,view=view,ephemeral=True)
    async def dispatch(self,channel,event,context=None):
        rows=await self.db.fetchall("SELECT * FROM ad_custom_messages WHERE guild_id=? AND event=? AND enabled=1 ORDER BY position,id",(channel.guild.id,event)); sent={}
        for row in rows:
            content=strip_mentions(str(row['content']))
            if not content: continue
            reference=sent.get(int(row['reply_to'])) if row['reply_to'] is not None else None
            try: sent[int(row['id'])]=await channel.send(content,reference=reference.to_reference(fail_if_not_exists=False) if reference else None,allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException: pass
        return sent
    @discord.app_commands.command(name="ad-settings",description="تخصيص نظام الإعلانات")
    async def ad_settings(self,interaction):
        if not self.is_admin(interaction): return await interaction.response.send_message("❌ هذا الأمر مخصص للـAdministrator فقط.",ephemeral=True)
        await self.show_panel(interaction)


async def setup(bot): await bot.add_cog(AdCustomization(bot))
