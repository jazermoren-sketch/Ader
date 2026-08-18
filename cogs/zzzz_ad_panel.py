from __future__ import annotations
from pathlib import Path
import discord
from discord.ext import commands

class GiveawaySettingsModal(discord.ui.Modal, title="إنشاء قيف أواي"):
    amount=discord.ui.TextInput(label="المبلغ بـ ANOCoin",required=False,max_length=15)
    minutes=discord.ui.TextInput(label="المدة بالدقائق",required=False,max_length=7)
    def __init__(self,cog,channel_id,owner_id):
        super().__init__(custom_id=f"ader:giveaway-settings:{channel_id}:{owner_id}"); self.cog,self.channel_id,self.owner_id=cog,channel_id,owner_id
    async def on_submit(self,i):
        if i.user.id!=self.owner_id:return await i.response.send_message("❌ هذه العملية لصاحب روم الإعلان فقط.",ephemeral=True)
        s=await self.cog.db.fetchone("SELECT giveaway_amount,giveaway_minutes FROM ad_settings WHERE guild_id=?",(i.guild.id,)); da=int(s["giveaway_amount"]) if s else 3000000; dm=int(s["giveaway_minutes"]) if s else 60
        try:
            amount=int(str(self.amount.value).replace(",","")) if str(self.amount.value).strip() else da; minutes=int(str(self.minutes.value)) if str(self.minutes.value).strip() else dm
            if amount<=0 or minutes<=0 or minutes>10080:raise ValueError
        except ValueError:return await i.response.send_message("❌ أدخل مبلغاً صحيحاً ومدة بين دقيقة و7 أيام.",ephemeral=True)
        ok,text=await self.cog.ad_cog.create_giveaway(i.guild,i.user,self.channel_id,amount,minutes*60); await i.response.send_message(text,ephemeral=True)

class AdPanelOverride(commands.Cog):
    def __init__(self,bot):self.bot=bot;self.db=bot.db;self.settings=None;self.ad_cog=None
    async def cog_load(self):
        self.settings=self.bot.get_cog("AdSettings");self.ad_cog=self.bot.get_cog("AdvertisingShop")
        if self.settings and self.ad_cog:self.settings.refresh_panel=self.refresh_panel;self.ad_cog.render_panel=self.refresh_panel;await self.settings.refresh_all()
    async def refresh_panel(self,channel):
        if not isinstance(channel,discord.TextChannel):return
        row=await self.db.fetchone("SELECT * FROM ad_rooms WHERE channel_id=? AND active=1",(channel.id,));
        if not row:return
        from .zzz_ad_settings import MentionChoiceView
        e=discord.Embed(title="📢 روم الإعلانات",description=row["template"],colour=discord.Colour.blurple());e.add_field(name="نوع المنشن",value="@everyone" if row["mention_type"]=="everyone" else "@here");e.set_footer(text="روم إعلان فقط • التخصيص عبر /ads-settings")
        v=discord.ui.View(timeout=None)
        b=discord.ui.Button(label="إعلان",emoji="📢",style=discord.ButtonStyle.primary,custom_id=f"ader:p2:a:{channel.id}")
        async def announce(i):
            if i.user.id!=int(row["owner_id"]):return await i.response.send_message("❌ هذا الزر لصاحب روم الإعلان فقط.",ephemeral=True)
            await i.response.send_message("**اختر نوع المنشن**",view=MentionChoiceView(self.settings,i.user.id,int(row["owner_id"]),channel.id),ephemeral=True)
        b.callback=announce;v.add_item(b)
        g=discord.ui.Button(label="قيف أواي",emoji="🎁",style=discord.ButtonStyle.success,custom_id=f"ader:p2:g:{channel.id}")
        async def giveaway(i):
            if i.user.id!=int(row["owner_id"]):return await i.response.send_message("❌ هذا الزر لصاحب روم الإعلان فقط.",ephemeral=True)
            await i.response.send_modal(GiveawaySettingsModal(self,channel.id,int(row["owner_id"])))
        g.callback=giveaway;v.add_item(g)
        msg=None
        if row["panel_message_id"]:
            try:msg=await channel.fetch_message(int(row["panel_message_id"]))
            except (discord.NotFound,discord.HTTPException):pass
        p=Path(row["image_path"]) if row["image_path"] else None
        if p and p.exists():
            f=discord.File(str(p),filename="ad-image");e.set_image(url="attachment://ad-image");msg=await msg.edit(embed=e,attachments=[f],view=v) if msg else await channel.send(embed=e,file=f,view=v)
        else:msg=await msg.edit(embed=e,attachments=[],view=v) if msg else await channel.send(embed=e,view=v)
        await self.db.execute("UPDATE ad_rooms SET panel_message_id=? WHERE channel_id=?",(msg.id,channel.id))
async def setup(bot):await bot.add_cog(AdPanelOverride(bot))
