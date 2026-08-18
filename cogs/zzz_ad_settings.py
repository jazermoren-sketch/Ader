from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands


class NewAdModal(discord.ui.Modal, title="اكتب إعلانك"):
    text = discord.ui.TextInput(label="نص الإعلان", style=discord.TextStyle.paragraph, max_length=4000)
    room_name = discord.ui.TextInput(label="اسم الروم", max_length=90)
    def __init__(self, cog, owner_id, channel_id, mention, actor_id):
        super().__init__(custom_id=f"ader:newmodal:{channel_id}:{actor_id}")
        self.cog,self.owner_id,self.channel_id,self.mention,self.actor_id=cog,owner_id,channel_id,mention,actor_id
    async def on_submit(self, interaction):
        if interaction.user.id != self.actor_id:
            return await interaction.response.send_message("❌ هذه العملية ليست لك.", ephemeral=True)
        row=await self.cog.db.fetchone("SELECT owner_id FROM ad_rooms WHERE channel_id=? AND active=1",(self.channel_id,))
        if not row or int(row["owner_id"])!=self.owner_id:
            return await interaction.response.send_message("❌ هذا الروم الإعلاني غير صالح.",ephemeral=True)
        channel=interaction.guild.get_channel(self.channel_id)
        if not isinstance(channel,discord.TextChannel): return await interaction.response.send_message("❌ الروم غير موجود.",ephemeral=True)
        mention="@everyone" if self.mention=="everyone" else "@here"
        content=f"{str(self.text.value).strip()}\n\n{mention}"
        try:
            await channel.edit(name=self.cog.clean_name(str(self.room_name.value)),category=None,reason="Ader advertisement")
            await channel.send(content,allowed_mentions=discord.AllowedMentions(everyone=True))
            await interaction.response.send_message("✅ تم إرسال الإعلان بنجاح.",ephemeral=True)
        except discord.Forbidden: await interaction.response.send_message("❌ البوت لا يملك الصلاحيات الكافية.",ephemeral=True)
        except discord.HTTPException: await interaction.response.send_message("❌ تعذر إرسال الإعلان حالياً.",ephemeral=True)


class MentionChoiceView(discord.ui.View):
    def __init__(self,cog,actor_id,owner_id,channel_id):
        super().__init__(timeout=120); self.cog,self.actor_id,self.owner_id,self.channel_id=cog,actor_id,owner_id,channel_id
        for label,emoji,style,mention in [("Everyone","🔴",discord.ButtonStyle.danger,"everyone"),("Here","🟢",discord.ButtonStyle.success,"here")]:
            b=discord.ui.Button(label=label,emoji=emoji,style=style,custom_id=f"ader:newmention:{channel_id}:{mention}"); b.callback=self.callback_for(mention); self.add_item(b)
    def callback_for(self,mention):
        async def callback(interaction):
            if interaction.user.id!=self.actor_id: return await interaction.response.send_message("❌ هذا الزر مخصص لصاحب الأمر فقط.",ephemeral=True)
            await interaction.response.send_modal(NewAdModal(self.cog,self.owner_id,self.channel_id,mention,self.actor_id))
        return callback


class SettingsModal(discord.ui.Modal,title="إعدادات روم الإعلان"):
    message=discord.ui.TextInput(label="الرسالة الافتراضية",style=discord.TextStyle.paragraph,required=False,max_length=4000)
    giveaway_amount=discord.ui.TextInput(label="مبلغ القيف أواي الافتراضي",required=False,max_length=15)
    giveaway_minutes=discord.ui.TextInput(label="مدة القيف أواي بالدقائق",required=False,max_length=7)
    def __init__(self,cog,channel_id,user_id):
        super().__init__(custom_id=f"ader:settings:{channel_id}:{user_id}"); self.cog,self.channel_id,self.user_id=cog,channel_id,user_id
    async def on_submit(self,interaction):
        if interaction.user.id!=self.user_id:return await interaction.response.send_message("❌ هذه الإعدادات ليست لك.",ephemeral=True)
        if not await self.cog.can_manage(interaction,self.channel_id):return
        message=str(self.message.value).strip()
        if message: await self.cog.db.execute("UPDATE ad_rooms SET template=? WHERE channel_id=?",(message,self.channel_id))
        if str(self.giveaway_amount.value).strip():
            try: amount=int(str(self.giveaway_amount.value).replace(",","")); assert amount>0
            except (ValueError,AssertionError): return await interaction.response.send_message("❌ مبلغ القيف أواي غير صحيح.",ephemeral=True)
            await self.cog.db.execute("UPDATE ad_settings SET giveaway_amount=? WHERE guild_id=?",(amount,interaction.guild.id))
        if str(self.giveaway_minutes.value).strip():
            try: minutes=int(str(self.giveaway_minutes.value)); assert 0<minutes<=10080
            except (ValueError,AssertionError): return await interaction.response.send_message("❌ مدة القيف أواي يجب أن تكون بين دقيقة و7 أيام.",ephemeral=True)
            await self.cog.db.execute("UPDATE ad_settings SET giveaway_minutes=? WHERE guild_id=?",(minutes,interaction.guild.id))
        await self.cog.refresh_panel(interaction.guild.get_channel(self.channel_id)); await interaction.response.send_message("✅ تم حفظ إعدادات روم الإعلان.",ephemeral=True)


class AdSettings(commands.Cog):
    def __init__(self,bot):
        self.bot,self.db=bot,bot.db; self.image_dir=Path(bot.config.get("database",{}).get("sqlite_path","data/ader.sqlite3")).parent/"ad_images"; self.ad_cog=None
    async def cog_load(self):
        try: await self.db.execute("ALTER TABLE ad_settings ADD COLUMN giveaway_amount INTEGER NOT NULL DEFAULT 3000000")
        except Exception: pass
        try: await self.db.execute("ALTER TABLE ad_settings ADD COLUMN giveaway_minutes INTEGER NOT NULL DEFAULT 60")
        except Exception: pass
        self.ad_cog=self.bot.get_cog("AdvertisingShop")
        if self.ad_cog:
            self.ad_cog.clean_name=self.clean_name; self.ad_cog.AdModal=NewAdModal; self.ad_cog.render_panel=self.refresh_panel
            await self.refresh_all()
    @staticmethod
    def clean_name(value):
        value=unicodedata.normalize("NFKC",value or ""); value="".join(c for c in value if c.isalnum() or c in " _-"); value=re.sub(r"\s+","-",value); return re.sub(r"-+","-",value).strip("-_").lower()[:90] or "advertisement"
    async def can_manage(self,interaction,channel_id):
        row=await self.db.fetchone("SELECT owner_id FROM ad_rooms WHERE channel_id=? AND active=1",(channel_id,))
        if not row:return await interaction.response.send_message("❌ استخدم هذا الأمر داخل روم إعلان.",ephemeral=True) or False
        if interaction.user.id!=int(row["owner_id"]) and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ هذه الإعدادات لصاحب روم الإعلان أو Administrator فقط.",ephemeral=True); return False
        return True
    async def refresh_all(self):
        for row in await self.db.fetchall("SELECT channel_id FROM ad_rooms WHERE active=1"):
            ch=self.bot.get_channel(int(row["channel_id"]));
            if ch:
                try: await self.refresh_panel(ch)
                except Exception: pass
    async def refresh_panel(self,channel):
        if not isinstance(channel,discord.TextChannel):return
        row=await self.db.fetchone("SELECT * FROM ad_rooms WHERE channel_id=? AND active=1",(channel.id,))
        if not row:return
        embed=discord.Embed(title="📢 روم الإعلانات",description=row["template"],colour=discord.Colour.blurple()); embed.set_footer(text="روم إعلان فقط • التخصيص عبر /ads-settings")
        embed.add_field(name="نوع المنشن",value="@everyone" if row["mention_type"]=="everyone" else "@here")
        view=discord.ui.View(timeout=None); b=discord.ui.Button(label="إعلان",emoji="📢",style=discord.ButtonStyle.primary,custom_id=f"ader:newpanel:{channel.id}")
        async def announce(i):
            if i.user.id!=int(row["owner_id"]):return await i.response.send_message("❌ هذا الزر لصاحب روم الإعلان فقط.",ephemeral=True)
            await i.response.send_message("**اختر نوع المنشن**",view=MentionChoiceView(self,i.user.id,int(row["owner_id"]),channel.id),ephemeral=True)
        b.callback=announce; view.add_item(b)
        msg=None
        if row["panel_message_id"]:
            try:msg=await channel.fetch_message(int(row["panel_message_id"]))
            except (discord.NotFound,discord.HTTPException):pass
        p=Path(row["image_path"]) if row["image_path"] else None
        if p and p.exists():
            f=discord.File(str(p),filename="ad-image"); embed.set_image(url="attachment://ad-image")
            msg=await msg.edit(embed=embed,attachments=[f],view=view) if msg else await channel.send(embed=embed,file=f,view=view)
        else: msg=await msg.edit(embed=embed,attachments=[],view=view) if msg else await channel.send(embed=embed,view=view)
        await self.db.execute("UPDATE ad_rooms SET panel_message_id=? WHERE channel_id=?",(msg.id,channel.id))
    @app_commands.command(name="ads-settings",description="تخصيص إعدادات روم الإعلان")
    async def ads_settings(self,interaction:discord.Interaction,image:discord.Attachment|None=None):
        if not isinstance(interaction.channel,discord.TextChannel):return await interaction.response.send_message("❌ استخدم الأمر داخل روم الإعلان.",ephemeral=True)
        if not await self.can_manage(interaction,interaction.channel.id):return
        if image:
            if not (image.content_type or "").startswith("image/"):return await interaction.response.send_message("❌ الملف يجب أن يكون صورة.",ephemeral=True)
            self.image_dir.mkdir(parents=True,exist_ok=True); path=self.image_dir/f"ad_{interaction.guild.id}_{interaction.channel.id}.img"; path.write_bytes(await image.read()); await self.db.execute("UPDATE ad_rooms SET image_path=? WHERE channel_id=?",(str(path),interaction.channel.id))
        await interaction.response.send_modal(SettingsModal(self,interaction.channel.id,interaction.user.id))

async def setup(bot): await bot.add_cog(AdSettings(bot))
