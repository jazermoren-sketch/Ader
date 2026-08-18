from __future__ import annotations

import asyncio
import json
import random
import re
import time
import unicodedata
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

OWNER_ID = 1472570059367911587
DEFAULT_GIVEAWAY = 3_000_000


def clean_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = "".join(c for c in value if c.isalnum() or c in " _-")
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-_").lower()[:90]
    return value or "advertisement"


class AdModal(discord.ui.Modal, title="اكتب إعلانك"):
    text = discord.ui.TextInput(label="اكتب إعلانك", style=discord.TextStyle.paragraph, max_length=4000)
    name = discord.ui.TextInput(label="اسم الروم", max_length=90)

    def __init__(self, cog, owner_id, channel_id, mention, actor_id):
        super().__init__(custom_id=f"ader:admodal:{channel_id}:{actor_id}")
        self.cog, self.owner_id, self.channel_id, self.mention, self.actor_id = cog, owner_id, channel_id, mention, actor_id

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.actor_id:
            return await interaction.response.send_message("❌ هذه العملية ليست لك.", ephemeral=True)
        row = await self.cog.db.fetchone("SELECT * FROM ad_rooms WHERE channel_id=? AND active=1", (self.channel_id,))
        channel = interaction.guild.get_channel(self.channel_id)
        if not row or int(row["owner_id"]) != self.owner_id or not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("❌ الروم غير موجود.", ephemeral=True)
        try:
            await channel.edit(name=clean_name(str(self.name.value)), category=None, reason="Ader advertisement")
            content = ("@everyone " if self.mention == "everyone" else "@here ") + str(self.text.value)
            await channel.send(content, allowed_mentions=discord.AllowedMentions(everyone=True))
            await interaction.response.send_message("✅ تم إرسال الإعلان بنجاح.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ البوت لا يملك الصلاحيات الكافية.", ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message("❌ تعذر إرسال الإعلان حالياً.", ephemeral=True)


class TemplateModal(discord.ui.Modal, title="تعديل الرسالة"):
    text = discord.ui.TextInput(label="الرسالة", style=discord.TextStyle.paragraph, max_length=4000)
    def __init__(self, cog, owner_id, channel_id):
        super().__init__(custom_id=f"ader:template:{channel_id}:{owner_id}")
        self.cog, self.owner_id, self.channel_id = cog, owner_id, channel_id
    async def on_submit(self, interaction):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("❌ هذه اللوحة مخصصة لصاحب الروم.", ephemeral=True)
        await self.cog.db.execute("UPDATE ad_rooms SET template=? WHERE channel_id=?", (str(self.text.value), self.channel_id))
        await self.cog.render_panel(interaction.guild.get_channel(self.channel_id))
        await interaction.response.send_message("✅ تم حفظ الرسالة.", ephemeral=True)


class GiveawayAmountModal(discord.ui.Modal, title="إنشاء قيف أواي"):
    amount = discord.ui.TextInput(label="مبلغ القيف أواي بـ ANOCoin", default=str(DEFAULT_GIVEAWAY), max_length=15)
    duration = discord.ui.TextInput(label="المدة بالدقائق", default="60", max_length=6)
    def __init__(self, cog, owner_id, channel_id):
        super().__init__(custom_id=f"ader:giveawaymodal:{channel_id}:{owner_id}")
        self.cog, self.owner_id, self.channel_id = cog, owner_id, channel_id
    async def on_submit(self, interaction):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("❌ هذه العملية ليست لك.", ephemeral=True)
        try:
            amount, minutes = int(str(self.amount.value).replace(",", "")), int(str(self.duration.value))
        except ValueError:
            return await interaction.response.send_message("❌ أدخل أرقاماً صحيحة.", ephemeral=True)
        if amount <= 0 or minutes <= 0 or minutes > 10080:
            return await interaction.response.send_message("❌ المبلغ والمدة يجب أن يكونا صحيحين، والمدة لا تتجاوز 7 أيام.", ephemeral=True)
        ok, msg = await self.cog.create_giveaway(interaction.guild, interaction.user, self.channel_id, amount, minutes * 60)
        await interaction.response.send_message(msg, ephemeral=True)


class GiveawayView(discord.ui.View):
    def __init__(self, cog, giveaway_id):
        super().__init__(timeout=None)
        self.cog, self.giveaway_id = cog, giveaway_id
        button = discord.ui.Button(label="مشاركة", emoji="🎉", style=discord.ButtonStyle.primary, custom_id=f"ader:giveaway:{giveaway_id}")
        button.callback = self.enter
        self.add_item(button)
    async def enter(self, interaction):
        row = await self.cog.db.fetchone("SELECT * FROM ad_giveaways WHERE id=? AND ended=0", (self.giveaway_id,))
        if not row or float(row["ends_at"]) <= time.time():
            return await interaction.response.send_message("❌ انتهى القيف أواي.", ephemeral=True)
        try:
            await self.cog.db.execute("INSERT INTO ad_giveaway_entries(giveaway_id,user_id) VALUES(?,?)", (self.giveaway_id, interaction.user.id))
        except Exception:
            return await interaction.response.send_message("ℹ️ أنت مسجل بالفعل في القيف أواي.", ephemeral=True)
        await interaction.response.send_message("🎉 تم تسجيل مشاركتك بنجاح.", ephemeral=True)


class AdPanel(discord.ui.View):
    def __init__(self, cog, owner_id, channel_id, mention):
        super().__init__(timeout=None)
        self.cog, self.owner_id, self.channel_id, self.mention = cog, owner_id, channel_id, mention
        for label, emoji, style, callback, key in [
            ("إعلان", "📢", discord.ButtonStyle.primary, self.announce, "announce"),
            ("قيف أواي", "🎁", discord.ButtonStyle.success, self.giveaway, "giveaway"),
            ("تعديل الرسالة", "📝", discord.ButtonStyle.secondary, self.template, "template"),
            ("إضافة صورة", "🖼️", discord.ButtonStyle.secondary, self.image, "image"),
        ]:
            b = discord.ui.Button(label=label, emoji=emoji, style=style, custom_id=f"ader:panel:{key}:{channel_id}")
            b.callback = callback
            self.add_item(b)
    async def check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ هذه اللوحة مخصصة لصاحب الروم فقط.", ephemeral=True)
            return False
        return True
    async def announce(self, interaction):
        if await self.check(interaction): await interaction.response.send_modal(AdModal(self.cog, self.owner_id, self.channel_id, self.mention, self.owner_id))
    async def giveaway(self, interaction):
        if await self.check(interaction): await interaction.response.send_modal(GiveawayAmountModal(self.cog, self.owner_id, self.channel_id))
    async def template(self, interaction):
        if await self.check(interaction): await interaction.response.send_modal(TemplateModal(self.cog, self.owner_id, self.channel_id))
    async def image(self, interaction):
        if not await self.check(interaction): return
        await interaction.response.send_message("📎 أرسل الصورة كـ Attachment في هذا الروم خلال 30 ثانية. سيتم حذف رسالة الصورة تلقائياً.", ephemeral=True)
        try:
            msg = await self.cog.bot.wait_for("message", timeout=30, check=lambda m: m.author.id == self.owner_id and m.channel.id == self.channel_id and bool(m.attachments))
        except asyncio.TimeoutError:
            return await interaction.followup.send("⌛ انتهى وقت رفع الصورة.", ephemeral=True)
        att = next((a for a in msg.attachments if (a.content_type or "").startswith("image/")), None)
        if not att:
            try: await msg.delete()
            except discord.HTTPException: pass
            return await interaction.followup.send("❌ الملف المرسل ليس صورة.", ephemeral=True)
        try:
            path = self.cog.image_dir / f"{interaction.guild.id}_{self.channel_id}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(await att.read())
            await self.cog.db.execute("UPDATE ad_rooms SET image_path=? WHERE channel_id=?", (str(path), self.channel_id))
            try: await msg.delete()
            except discord.HTTPException: pass
            await self.cog.render_panel(interaction.guild.get_channel(self.channel_id))
            await interaction.followup.send("✅ تم حفظ الصورة.", ephemeral=True)
        except (OSError, discord.HTTPException):
            await interaction.followup.send("❌ تعذر حفظ الصورة.", ephemeral=True)


class PrefixAdView(discord.ui.View):
    def __init__(self, cog, actor_id, owner_id, channel_id):
        super().__init__(timeout=120); self.cog, self.actor_id, self.owner_id, self.channel_id = cog, actor_id, owner_id, channel_id
        for label, style, mention in [("Everyone", discord.ButtonStyle.danger, "everyone"), ("Here", discord.ButtonStyle.success, "here")]:
            b = discord.ui.Button(label=label, style=style); b.callback = lambda i, m=mention: self.pick(i, m); self.add_item(b)
    async def pick(self, interaction, mention):
        if interaction.user.id != self.actor_id: return await interaction.response.send_message("❌ هذا التحكم ليس لك.", ephemeral=True)
        await interaction.response.send_modal(AdModal(self.cog, self.owner_id, self.channel_id, mention, self.actor_id))


class AdvertisingShop(commands.Cog):
    def __init__(self, bot):
        self.bot, self.db = bot, bot.db
        db_path = Path(bot.config.get("database", {}).get("sqlite_path", "data/ader.sqlite3"))
        self.image_dir = db_path.parent / "ad_images"
        self.worker.start()
    async def cog_load(self):
        await self.db.execute("CREATE TABLE IF NOT EXISTS ad_rooms(guild_id INTEGER NOT NULL,channel_id INTEGER PRIMARY KEY,owner_id INTEGER NOT NULL,mention_type TEXT NOT NULL,template TEXT NOT NULL DEFAULT 'مرحباً بك في روم الإعلانات الخاص بك.',image_path TEXT,panel_message_id INTEGER,active INTEGER NOT NULL DEFAULT 1)")
        await self.db.execute("CREATE TABLE IF NOT EXISTS ad_settings(guild_id INTEGER PRIMARY KEY,allowed_roles TEXT NOT NULL DEFAULT '[]')")
        await self.db.execute("CREATE TABLE IF NOT EXISTS ad_giveaways(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,channel_id INTEGER NOT NULL,owner_id INTEGER NOT NULL,amount INTEGER NOT NULL,ends_at REAL NOT NULL,ended INTEGER NOT NULL DEFAULT 0,winner_id INTEGER)")
        await self.db.execute("CREATE TABLE IF NOT EXISTS ad_giveaway_entries(giveaway_id INTEGER NOT NULL,user_id INTEGER NOT NULL,PRIMARY KEY(giveaway_id,user_id))")
        for row in await self.db.fetchall("SELECT * FROM ad_rooms WHERE active=1"):
            self.bot.add_view(AdPanel(self, int(row["owner_id"]), int(row["channel_id"]), str(row["mention_type"])))
        for row in await self.db.fetchall("SELECT id FROM ad_giveaways WHERE ended=0"):
            self.bot.add_view(GiveawayView(self, int(row["id"])))
        self._patch_shop()
    def cog_unload(self): self.worker.cancel()
    def _patch_shop(self):
        shop = self.bot.get_cog("Shop")
        if not shop or getattr(shop, "_ader_ad_delivery", False): return
        original = shop._purchase
        async def purchase(guild_id, user_id, item_id):
            ok, text = await original(guild_id, user_id, item_id)
            if not ok: return ok, text
            item = await self.db.fetchone("SELECT * FROM shop WHERE guild_id=? AND id=?", (guild_id, item_id))
            try: data = json.loads(item["data"] or "{}")
            except Exception: data = {}
            delivery = data.get("delivery") or {}
            if delivery.get("type") != "ad_room": return ok, text
            guild = self.bot.get_guild(guild_id); member = guild.get_member(user_id) if guild else None
            if not guild or not member: return False, "❌ تعذر تسليم المنتج."
            if not guild.me.guild_permissions.manage_channels:
                await self.db.add_balance(user_id, guild_id, int(item["price"])); return False, "❌ البوت يحتاج Manage Channels؛ تمت إعادة المبلغ."
            if await self.db.fetchone("SELECT channel_id FROM ad_rooms WHERE guild_id=? AND owner_id=? AND active=1", (guild_id, user_id)):
                await self.db.add_balance(user_id, guild_id, int(item["price"])); return False, "❌ لديك روم إعلاني نشط بالفعل؛ تمت إعادة المبلغ."
            private = delivery.get("visibility") == "private"
            overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=not private, send_messages=False, read_message_history=True), member: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, embed_links=True, read_message_history=True), guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_messages=True, attach_files=True, embed_links=True, read_message_history=True)}
            try: channel = await guild.create_text_channel(clean_name(f"ad-{member.display_name}"), category=None, overwrites=overwrites, reason="Ader shop advertising room")
            except (discord.Forbidden, discord.HTTPException):
                await self.db.add_balance(user_id, guild_id, int(item["price"])); return False, "❌ تعذر إنشاء الروم؛ تمت إعادة المبلغ."
            mention = delivery.get("mention_type", "everyone")
            await self.db.execute("INSERT INTO ad_rooms(guild_id,channel_id,owner_id,mention_type) VALUES(?,?,?,?)", (guild_id,channel.id,user_id,mention))
            await channel.send(member.mention, allowed_mentions=discord.AllowedMentions(users=True))
            await self.render_panel(channel)
            return True, text + f"\n🏠 تم تسليم الروم: {channel.mention}"
        shop._purchase, shop._ader_ad_delivery = purchase, True
    async def render_panel(self, channel):
        if not isinstance(channel, discord.TextChannel): return
        row = await self.db.fetchone("SELECT * FROM ad_rooms WHERE channel_id=? AND active=1", (channel.id,))
        if not row: return
        embed = discord.Embed(title="📢 لوحة الروم الإعلاني", description=row["template"], colour=discord.Colour.blurple())
        embed.add_field(name="نوع المنشن", value="@everyone" if row["mention_type"] == "everyone" else "@here")
        view = AdPanel(self, int(row["owner_id"]), channel.id, str(row["mention_type"]))
        msg = None
        if row["panel_message_id"]:
            try: msg = await channel.fetch_message(int(row["panel_message_id"]))
            except (discord.NotFound, discord.HTTPException): pass
        path = Path(row["image_path"]) if row["image_path"] else None
        if path and path.exists():
            f = discord.File(str(path), filename="ad-image.png"); embed.set_image(url="attachment://ad-image.png")
            msg = await msg.edit(embed=embed, attachments=[f], view=view) if msg else await channel.send(embed=embed, file=f, view=view)
        else: msg = await msg.edit(embed=embed, attachments=[], view=view) if msg else await channel.send(embed=embed, view=view)
        await self.db.execute("UPDATE ad_rooms SET panel_message_id=? WHERE channel_id=?", (msg.id, channel.id))
    async def authorized(self, member):
        if member.id == OWNER_ID or member.guild_permissions.administrator: return True
        row = await self.db.fetchone("SELECT allowed_roles FROM ad_settings WHERE guild_id=?", (member.guild.id,))
        try: roles = set(json.loads(row["allowed_roles"] or "[]")) if row else set()
        except Exception: roles = set()
        return any(r.id in roles for r in member.roles)
    @commands.command(name="اعلان")
    async def اعلان(self, ctx, member: discord.Member | None = None):
        if not member: return await ctx.reply("❌ الاستعمال الصحيح: `$اعلان @user`", mention_author=False)
        if not await self.authorized(ctx.author): return await ctx.reply("❌ هذا الأمر محمي. يلزم Administrator أو رتبة محددة.", mention_author=False)
        row = await self.db.fetchone("SELECT * FROM ad_rooms WHERE guild_id=? AND owner_id=? AND active=1", (ctx.guild.id, member.id))
        if not row: return await ctx.reply("❌ هذا العضو لا يملك روم إعلان نشطاً.", mention_author=False)
        await ctx.reply(f"**اختر نوع المنشن حق الروم**\n{member.mention}", mention_author=False, view=PrefixAdView(self,ctx.author.id,member.id,int(row["channel_id"])), allowed_mentions=discord.AllowedMentions(users=True))
    @app_commands.command(name="shop-add-ad", description="Create an advertising room shop offer")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.choices(mention=[app_commands.Choice(name="Everyone",value="everyone"),app_commands.Choice(name="Here",value="here")], visibility=[app_commands.Choice(name="Public",value="public"),app_commands.Choice(name="Private",value="private")])
    async def shop_add_ad(self, interaction, name: str, price: int, mention: app_commands.Choice[str], visibility: app_commands.Choice[str]):
        if not interaction.user.guild_permissions.manage_channels: return await interaction.response.send_message("❌ تحتاج Manage Channels.",ephemeral=True)
        if price <= 0: return await interaction.response.send_message("❌ السعر يجب أن يكون أكبر من صفر.",ephemeral=True)
        data={"description":"روم إعلاني يتم تسليمه فور الشراء.","stock":-1,"delivery":{"type":"ad_room","mention_type":mention.value,"visibility":visibility.value}}
        await self.db.execute("INSERT INTO shop(guild_id,name,price,data) VALUES(?,?,?,?)",(interaction.guild.id,name[:100],price,json.dumps(data,ensure_ascii=False)))
        await interaction.response.send_message(f"✅ تم إنشاء العرض **{name[:100]}** بسعر **{price:,} ANOCoin**.",ephemeral=True)
    @app_commands.command(name="ad-role-add", description="Allow a role to use $اعلان")
    @app_commands.default_permissions(administrator=True)
    async def ad_role_add(self, interaction, role: discord.Role):
        if not interaction.user.guild_permissions.administrator: return await interaction.response.send_message("❌ تحتاج Administrator.",ephemeral=True)
        row=await self.db.fetchone("SELECT allowed_roles FROM ad_settings WHERE guild_id=?",(interaction.guild.id,)); roles=set(json.loads(row["allowed_roles"] or "[]")) if row else set(); roles.add(role.id)
        await self.db.execute("INSERT INTO ad_settings(guild_id,allowed_roles) VALUES(?,?) ON CONFLICT(guild_id) DO UPDATE SET allowed_roles=excluded.allowed_roles",(interaction.guild.id,json.dumps(sorted(roles))))
        await interaction.response.send_message(f"✅ تمت إضافة {role.mention}.",ephemeral=True)
    async def create_giveaway(self,guild,owner,channel_id,amount,duration):
        row=await self.db.fetchone("SELECT * FROM ad_rooms WHERE guild_id=? AND channel_id=? AND owner_id=? AND active=1",(guild.id,channel_id,owner.id))
        if not row:return False,"❌ هذا ليس رومك الإعلاني."
        if await self.db.fetchone("SELECT id FROM ad_giveaways WHERE channel_id=? AND ended=0",(channel_id,)):return False,"❌ يوجد قيف أواي نشط بالفعل."
        if await self.db.get_balance(owner.id)<amount:return False,f"❌ يجب أن يكون لديك **{amount:,} ANOCoin** لبدء القيف أواي."
        if not await self.db.remove_balance(owner.id,guild.id,amount):return False,"❌ تعذر خصم المبلغ."
        ends=time.time()+duration
        cur=await self.db.execute("INSERT INTO ad_giveaways(guild_id,channel_id,owner_id,amount,ends_at) VALUES(?,?,?,?,?)",(guild.id,channel_id,owner.id,amount,ends)); gid=cur.lastrowid
        channel=guild.get_channel(channel_id)
        try:
            embed=discord.Embed(title="🎁 قيف أواي ANOCoin",description=f"الجائزة: **{amount:,} ANOCoin**\nينتهي: <t:{int(ends)}:R>\nاضغط **مشاركة** للدخول.",colour=discord.Colour.green())
            await channel.send(embed=embed,view=GiveawayView(self,gid))
        except discord.HTTPException:
            await self.db.execute("UPDATE ad_giveaways SET ended=1 WHERE id=?",(gid,)); await self.db.add_balance(owner.id,guild.id,amount); return False,"❌ تعذر نشر القيف أواي؛ تمت إعادة المبلغ."
        self.bot.add_view(GiveawayView(self,gid)); return True,f"✅ تم إنشاء القيف أواي وخصم **{amount:,} ANOCoin** من رصيدك."
    @tasks.loop(seconds=20)
    async def worker(self):
        for row in await self.db.fetchall("SELECT * FROM ad_giveaways WHERE ended=0 AND ends_at<=?",(time.time(),)):
            await self.finish_giveaway(row)
    @worker.before_loop
    async def before_worker(self): await self.bot.wait_until_ready()
    async def finish_giveaway(self,row):
        await self.db.execute("UPDATE ad_giveaways SET ended=1 WHERE id=? AND ended=0",(row["id"],))
        entries=await self.db.fetchall("SELECT user_id FROM ad_giveaway_entries WHERE giveaway_id=?",(row["id"],))
        channel=self.bot.get_channel(int(row["channel_id"]))
        if not entries:
            await self.db.add_balance(int(row["owner_id"]),int(row["guild_id"]),int(row["amount"]))
            if channel: await channel.send("⏰ انتهى القيف أواي دون مشاركين؛ تمت إعادة الجائزة إلى صاحب الروم.")
            return
        winner_id=int(random.choice(entries)["user_id"])
        await self.db.add_balance(winner_id,int(row["guild_id"]),int(row["amount"]))
        await self.db.execute("UPDATE ad_giveaways SET winner_id=? WHERE id=?",(winner_id,row["id"]))
        if channel: await channel.send(f"🎉 مبروك <@{winner_id}>! فزت بـ **{int(row['amount']):,} ANOCoin**.",allowed_mentions=discord.AllowedMentions(users=True))


async def setup(bot): await bot.add_cog(AdvertisingShop(bot))
