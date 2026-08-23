from __future__ import annotations

import asyncio
import json
import random
import re
import time
import unicodedata
from pathlib import Path

import discord
from discord.ext import commands, tasks

OWNER_ID = 1472570059367911587
DEFAULT_GIVEAWAY = 3_000_000


def clean_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = "".join(c for c in value if c.isalnum() or c in " _-")
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-_").lower()[:90]
    return value or "advertisement"


def ad_embed(text: str) -> discord.Embed:
    return discord.Embed(title="✅ تم نشر الإعلان", description=text, colour=discord.Colour.green())


class RoomSelect(discord.ui.Select):
    def __init__(self, cog, actor_id: int, owner_id: int, rows):
        options = []
        for row in rows[:25]:
            options.append(
                discord.SelectOption(
                    label=str(row["channel_id"]),
                    description=f"<#${row['channel_id']}>".replace("$", ""),
                    value=str(row["channel_id"]),
                )
            )
        super().__init__(placeholder="اختار روم الإعلان", options=options, min_values=1, max_values=1)
        self.cog, self.actor_id, self.owner_id = cog, actor_id, owner_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.actor_id:
            return await interaction.response.send_message("❌ هذا التحكم ليس لك.", ephemeral=True)
        channel_id = int(self.values[0])
        await interaction.response.edit_message(
            content="**اختر نوع المنشن حق الروم**",
            view=PrefixAdView(self.cog, self.actor_id, self.owner_id, channel_id, self.cog.control_message_id(interaction.message)),
        )


class RoomSelectView(discord.ui.View):
    def __init__(self, cog, actor_id: int, owner_id: int, rows):
        super().__init__(timeout=120)
        self.add_item(RoomSelect(cog, actor_id, owner_id, rows))


class AdModal(discord.ui.Modal, title="اكتب إعلانك"):
    text = discord.ui.TextInput(label="اكتب إعلانك", style=discord.TextStyle.paragraph, max_length=4000)
    name = discord.ui.TextInput(label="اسم الروم", max_length=90)

    def __init__(self, cog, owner_id: int, channel_id: int, mention: str, actor_id: int, control_message_id: int | None = None):
        super().__init__(custom_id=f"ader:admodal:{channel_id}:{actor_id}")
        self.cog = cog
        self.owner_id = owner_id
        self.channel_id = channel_id
        self.mention = mention
        self.actor_id = actor_id
        self.control_message_id = control_message_id

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.actor_id:
            return await interaction.response.send_message("❌ هذه العملية ليست لك.", ephemeral=True)
        row = await self.cog.db.fetchone("SELECT * FROM ad_rooms WHERE channel_id=? AND active=1", (self.channel_id,))
        channel = interaction.guild.get_channel(self.channel_id) if interaction.guild else None
        if not row or int(row["owner_id"]) != self.owner_id or not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("❌ الروم غير موجود.", ephemeral=True)
        try:
            await channel.edit(name=clean_name(str(self.name.value)), reason="Ader advertisement")
            mention_text = "@everyone" if self.mention == "everyone" else "@here"
            content = f"{mention_text} {str(self.text.value).strip()}"
            message = await channel.send(content, allowed_mentions=discord.AllowedMentions(everyone=True))
            await self.cog.run_custom_event(channel, "after_ad", message)
            await interaction.response.send_message(embed=ad_embed(f"تم نشر إعلانك في {channel.mention} ✅"), ephemeral=True)
            await self.cog.finish_control_message(interaction, self.control_message_id, channel)
        except discord.Forbidden:
            await interaction.response.send_message("❌ البوت لا يملك الصلاحيات الكافية.", ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message("❌ تعذر إرسال الإعلان حالياً.", ephemeral=True)


class GiveawayAmountModal(discord.ui.Modal, title="إنشاء قيف أواي"):
    amount = discord.ui.TextInput(label="مبلغ القيف أواي بـ ANOCoin", default=str(DEFAULT_GIVEAWAY), max_length=15)
    duration = discord.ui.TextInput(label="المدة بالدقائق", default="60", max_length=7)

    def __init__(self, cog, owner_id: int, channel_id: int):
        super().__init__(custom_id=f"ader:giveawaymodal:{channel_id}:{owner_id}")
        self.cog, self.owner_id, self.channel_id = cog, owner_id, channel_id

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("❌ هذه العملية ليست لك.", ephemeral=True)
        try:
            amount = int(str(self.amount.value).replace(",", "").strip())
            minutes = int(str(self.duration.value).strip())
        except ValueError:
            return await interaction.response.send_message("❌ أدخل أرقاماً صحيحة.", ephemeral=True)
        if amount <= 0 or minutes <= 0 or minutes > 10080:
            return await interaction.response.send_message("❌ المبلغ والمدة غير صالحين.", ephemeral=True)
        ok, msg = await self.cog.create_giveaway(interaction.guild, interaction.user, self.channel_id, amount, minutes * 60)
        await interaction.response.send_message(msg, ephemeral=True)


class GiveawayView(discord.ui.View):
    def __init__(self, cog, giveaway_id: int):
        super().__init__(timeout=None)
        self.cog, self.giveaway_id = cog, giveaway_id
        button = discord.ui.Button(emoji="🎉", style=discord.ButtonStyle.primary, custom_id=f"ader:giveaway:{giveaway_id}")
        button.callback = self.enter
        self.add_item(button)

    async def enter(self, interaction: discord.Interaction):
        row = await self.cog.db.fetchone("SELECT * FROM ad_giveaways WHERE id=? AND ended=0", (self.giveaway_id,))
        if not row or float(row["ends_at"]) <= time.time():
            return await interaction.response.send_message("❌ انتهى القيف أواي.", ephemeral=True)
        try:
            await self.cog.db.execute(
                "INSERT INTO ad_giveaway_entries(giveaway_id,user_id) VALUES(?,?)",
                (self.giveaway_id, interaction.user.id),
            )
        except Exception:
            return await interaction.response.send_message("ℹ️ أنت مسجل بالفعل في القيف أواي.", ephemeral=True)
        await interaction.response.send_message("🎉 تم تسجيل مشاركتك بنجاح.", ephemeral=True)


class AdPanel(discord.ui.View):
    def __init__(self, cog, owner_id: int, channel_id: int, mention: str):
        super().__init__(timeout=None)
        self.cog, self.owner_id, self.channel_id, self.mention = cog, owner_id, channel_id, mention
        for label, emoji, style, callback, key in [
            ("إعلان", "📢", discord.ButtonStyle.primary, self.announce, "announce"),
            ("قيف أواي", "🎁", discord.ButtonStyle.success, self.giveaway, "giveaway"),
            ("تعديل الرسالة", "📝", discord.ButtonStyle.secondary, self.template, "template"),
            ("إضافة صورة", "🖼️", discord.ButtonStyle.secondary, self.image, "image"),
        ]:
            button = discord.ui.Button(label=label, emoji=emoji, style=style, custom_id=f"ader:panel:{key}:{channel_id}")
            button.callback = callback
            self.add_item(button)

    async def check(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ هذه اللوحة مخصصة لصاحب الروم فقط.", ephemeral=True)
            return False
        return True

    async def announce(self, interaction: discord.Interaction):
        if await self.check(interaction):
            await interaction.response.send_modal(AdModal(self.cog, self.owner_id, self.channel_id, self.mention, self.owner_id))

    async def giveaway(self, interaction: discord.Interaction):
        if await self.check(interaction):
            await interaction.response.send_modal(GiveawayAmountModal(self.cog, self.owner_id, self.channel_id))

    async def template(self, interaction: discord.Interaction):
        if await self.check(interaction):
            await interaction.response.send_modal(TemplateModal(self.cog, self.owner_id, self.channel_id))

    async def image(self, interaction: discord.Interaction):
        if not await self.check(interaction):
            return
        await interaction.response.send_message("📎 أرسل الصورة كـAttachment في هذا الروم خلال 60 ثانية.", ephemeral=True)
        try:
            message = await self.cog.bot.wait_for(
                "message",
                timeout=60,
                check=lambda m: m.author.id == self.owner_id and m.channel.id == self.channel_id and bool(m.attachments),
            )
        except asyncio.TimeoutError:
            return await interaction.followup.send("⌛ انتهى وقت رفع الصورة.", ephemeral=True)
        attachment = next((a for a in message.attachments if (a.content_type or "").startswith("image/")), None)
        if attachment is None:
            return await interaction.followup.send("❌ الملف المرسل ليس صورة.", ephemeral=True)
        try:
            self.cog.image_dir.mkdir(parents=True, exist_ok=True)
            path = self.cog.image_dir / f"{interaction.guild.id}_{self.channel_id}_{message.id}.png"
            path.write_bytes(await attachment.read())
            target = await self.cog.send_image_message(message.channel, path)
            await self.cog.run_custom_event(message.channel, "after_image", target)
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            await interaction.followup.send("✅ تم نشر الصورة.", ephemeral=True)
        except (OSError, discord.HTTPException):
            await interaction.followup.send("❌ تعذر حفظ/نشر الصورة.", ephemeral=True)


class TemplateModal(discord.ui.Modal, title="تعديل الرسالة"):
    text = discord.ui.TextInput(label="الرسالة", style=discord.TextStyle.paragraph, max_length=4000)

    def __init__(self, cog, owner_id: int, channel_id: int):
        super().__init__(custom_id=f"ader:template:{channel_id}:{owner_id}")
        self.cog, self.owner_id, self.channel_id = cog, owner_id, channel_id

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("❌ هذه اللوحة مخصصة لصاحب الروم.", ephemeral=True)
        await self.cog.db.execute("UPDATE ad_rooms SET template=? WHERE channel_id=?", (str(self.text.value), self.channel_id))
        await self.cog.render_panel(interaction.guild.get_channel(self.channel_id))
        await interaction.response.send_message("✅ تم حفظ الرسالة.", ephemeral=True)


class PrefixAdView(discord.ui.View):
    def __init__(self, cog, actor_id: int, owner_id: int, channel_id: int, control_message_id: int | None = None):
        super().__init__(timeout=120)
        self.cog, self.actor_id, self.owner_id, self.channel_id = cog, actor_id, owner_id, channel_id
        self.control_message_id = control_message_id
        for label, style, mention in [("Everyone", discord.ButtonStyle.danger, "everyone"), ("Here", discord.ButtonStyle.success, "here")]:
            button = discord.ui.Button(label=label, style=style)
            button.callback = lambda i, m=mention: self.pick(i, m)
            self.add_item(button)

    async def pick(self, interaction, mention):
        if interaction.user.id != self.actor_id:
            return await interaction.response.send_message("❌ هذا التحكم ليس لك.", ephemeral=True)
        await interaction.response.edit_message(
            content=f"{interaction.guild.get_member(self.owner_id).mention if interaction.guild else ''}\nاضغط لكتابة إعلانك.",
            view=WriteAdView(self.cog, self.owner_id, self.channel_id, mention, self.control_message_id),
        )


class WriteAdView(discord.ui.View):
    """The owner-bound final step of an administrator-created ad session."""
    def __init__(self, cog, owner_id: int, channel_id: int, mention: str, control_message_id: int | None):
        super().__init__(timeout=120)
        self.cog, self.owner_id, self.channel_id = cog, owner_id, channel_id
        self.mention, self.control_message_id = mention, control_message_id

    @discord.ui.button(label="اكتب إعلانك", emoji="📢", style=discord.ButtonStyle.primary)
    async def write(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("❌ فقط صاحب الروم يمكنه كتابة الإعلان.", ephemeral=True)
        await interaction.response.send_modal(
            AdModal(self.cog, self.owner_id, self.channel_id, self.mention, self.owner_id, self.control_message_id)
        )


class AdvertisingShop(commands.Cog):
    def __init__(self, bot):
        self.bot, self.db = bot, bot.db
        db_path = Path(bot.config.get("database", {}).get("sqlite_path", "data/ader.sqlite3"))
        self.image_dir = db_path.parent / "ad_images"
        self.worker.start()

    def control_message_id(self, message):
        return int(message.id) if message else None

    async def cog_load(self):
        for row in await self.db.fetchall("SELECT * FROM ad_rooms WHERE active=1"):
            self.bot.add_view(AdPanel(self, int(row["owner_id"]), int(row["channel_id"]), str(row["mention_type"])))
        for row in await self.db.fetchall("SELECT id FROM ad_giveaways WHERE ended=0"):
            self.bot.add_view(GiveawayView(self, int(row["id"])))
        self.bot.ad_delivery_handler = self.deliver_shop_item

    def cog_unload(self):
        self.worker.cancel()

    async def deliver_shop_item(self, guild_id, user_id, item):
        try:
            data = json.loads(item["data"] or "{}")
        except (TypeError, json.JSONDecodeError):
            data = {}
        delivery = data.get("delivery") or {}
        if delivery.get("type") != "ad_room":
            return True, ""
        guild = self.bot.get_guild(guild_id)
        member = guild.get_member(user_id) if guild else None
        if not guild or not member:
            return False, "❌ تعذر تسليم المنتج."
        if not guild.me.guild_permissions.manage_channels:
            await self.db.add_balance(user_id, guild_id, int(item["price"]))
            return False, "❌ البوت يحتاج Manage Channels؛ تمت إعادة المبلغ."
        private = delivery.get("visibility") == "private"
        overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=not private, send_messages=False, read_message_history=True),
                member: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, embed_links=True, read_message_history=True),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_messages=True, attach_files=True, embed_links=True, read_message_history=True),
        }
        try:
            channel = await guild.create_text_channel(clean_name(f"ad-{member.display_name}"), category=None, overwrites=overwrites, reason="Ader shop advertising room")
        except (discord.Forbidden, discord.HTTPException):
            await self.db.add_balance(user_id, guild_id, int(item["price"]))
            return False, "❌ تعذر إنشاء الروم؛ تمت إعادة المبلغ."
        mention = delivery.get("mention_type", "everyone")
        await self.db.execute("INSERT INTO ad_rooms(guild_id,channel_id,owner_id,mention_type) VALUES(?,?,?,?)", (guild_id, channel.id, user_id, mention))
        await channel.send(member.mention, allowed_mentions=discord.AllowedMentions(users=True))
        await self.render_panel(channel)
        return True, f"🏠 تم تسليم الروم: {channel.mention}"

    async def render_panel(self, channel):
        if not isinstance(channel, discord.TextChannel):
            return
        row = await self.db.fetchone("SELECT * FROM ad_rooms WHERE channel_id=? AND active=1", (channel.id,))
        if not row:
            return
        embed = discord.Embed(title="📢 لوحة الروم الإعلاني", description=row["template"], colour=discord.Colour.blurple())
        embed.add_field(name="نوع المنشن", value="@everyone" if row["mention_type"] == "everyone" else "@here")
        view = AdPanel(self, int(row["owner_id"]), channel.id, str(row["mention_type"]))
        msg = None
        if row["panel_message_id"]:
            try:
                msg = await channel.fetch_message(int(row["panel_message_id"]))
            except (discord.NotFound, discord.HTTPException):
                msg = None
        path = Path(row["image_path"]) if row["image_path"] else None
        if path and path.exists():
            file = discord.File(str(path), filename="ad-image.png")
            embed.set_image(url="attachment://ad-image.png")
            msg = await msg.edit(embed=embed, attachments=[file], view=view) if msg else await channel.send(embed=embed, file=file, view=view)
        else:
            msg = await msg.edit(embed=embed, attachments=[], view=view) if msg else await channel.send(embed=embed, view=view)
        await self.db.execute("UPDATE ad_rooms SET panel_message_id=? WHERE channel_id=?", (msg.id, channel.id))

    async def authorized(self, member):
        if member.id == OWNER_ID or member.guild_permissions.administrator or member.guild_permissions.manage_guild:
            return True
        row = await self.db.fetchone("SELECT allowed_roles FROM ad_settings WHERE guild_id=?", (member.guild.id,))
        try:
            roles = {int(x) for x in json.loads(row["allowed_roles"] or "[]")} if row else set()
        except Exception:
            roles = set()
        return any(r.id in roles for r in member.roles)

    async def finish_control_message(self, interaction, message_id: int | None, channel: discord.TextChannel):
        if not message_id:
            return
        try:
            message = await interaction.channel.fetch_message(message_id)
            await message.edit(content=None, embed=ad_embed(f"تم نشر اعلانك في {channel.mention} ✅"), view=None)
        except (discord.NotFound, discord.HTTPException, discord.Forbidden):
            pass

    @commands.command(name="اعلان")
    async def اعلان(self, ctx, member: discord.Member | None = None):
        if not ctx.guild:
            return
        if not await self.authorized(ctx.author):
            return await ctx.reply("❌ ليست لديك صلاحية استعمال أمر `$اعلان`.", mention_author=False)
        if member is None:
            return await ctx.reply("❌ الاستعمال الصحيح: `$اعلان @user`", mention_author=False)
        if member.bot:
            return await ctx.reply("❌ لا يمكن إنشاء إعلان لبوت.", mention_author=False)
        rows = await self.db.fetchall("SELECT * FROM ad_rooms WHERE guild_id=? AND owner_id=? AND active=1 ORDER BY channel_id", (ctx.guild.id, member.id))
        if not rows:
            return await ctx.reply("❌ هذا العضو لا يملك أي روم إعلان نشطاً.", mention_author=False)
        if len(rows) == 1:
            view = PrefixAdView(self, ctx.author.id, member.id, int(rows[0]["channel_id"]))
            control = await ctx.reply(
                f"{member.mention}\n**اختر نوع المنشن حق الروم**",
                mention_author=False,
                view=view,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
            view.control_message_id = control.id
            return
        message = await ctx.reply(
            f"{member.mention}\n**اختار روم الإعلان اللي بغيتي تنشر فيه**",
            mention_author=False,
            view=RoomSelectView(self, ctx.author.id, member.id, rows),
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    async def send_image_message(self, channel: discord.TextChannel, path: Path):
        return await channel.send(file=discord.File(str(path), filename="advertisement-image.png"))

    async def get_custom_target(self, channel: discord.TextChannel, target: str | None):
        target = str(target or "none")
        if target == "none":
            return None
        if target in {"ad", "giveaway", "image"}:
            return self.last_event_messages.get(channel.id, {}).get(target)
        if target.startswith("custom:"):
            try:
                custom_id = int(target.split(":", 1)[1])
            except ValueError:
                return None
            row = await self.db.fetchone("SELECT last_message_id FROM ad_custom_messages WHERE id=? AND guild_id=?", (custom_id, channel.guild.id))
            if row and row["last_message_id"]:
                try:
                    return await channel.fetch_message(int(row["last_message_id"]))
                except (discord.NotFound, discord.HTTPException):
                    return None
        if target.startswith("message:"):
            try:
                return await channel.fetch_message(int(target.split(":", 1)[1]))
            except (discord.NotFound, discord.HTTPException, ValueError):
                return None
        return None

    async def run_custom_event(self, channel: discord.TextChannel, event: str, event_message: discord.Message | None = None):
        if not hasattr(self, "last_event_messages"):
            self.last_event_messages = {}
        bucket = self.last_event_messages.setdefault(channel.id, {})
        if event in {"after_ad", "after_giveaway", "after_image"} and event_message is not None:
            bucket[event.replace("after_", "")] = event_message
        rows = await self.db.fetchall("SELECT * FROM ad_custom_messages WHERE guild_id=? AND event=? AND enabled=1 ORDER BY position,id", (channel.guild.id, event))
        for row in rows:
            reference = await self.get_custom_target(channel, row["reply_target"] if "reply_target" in row.keys() else row["reply_to"])
            kwargs = {"content": str(row["content"])}
            if reference is not None:
                kwargs["reference"] = reference
                kwargs["mention_author"] = False
            message = await channel.send(**kwargs)
            await self.db.execute("UPDATE ad_custom_messages SET last_message_id=? WHERE id=?", (message.id, row["id"]))

    async def create_giveaway(self, guild, owner: discord.Member, channel_id: int, amount: int, duration: int):
        if guild is None:
            return False, "❌ السيرفر غير موجود."
        row = await self.db.fetchone("SELECT * FROM ad_rooms WHERE guild_id=? AND channel_id=? AND owner_id=? AND active=1", (guild.id, channel_id, owner.id))
        if not row:
            return False, "❌ هذا ليس رومك الإعلاني."
        if await self.db.get_balance(owner.id) < amount:
            return False, f"❌ يجب أن يكون لديك **{amount:,} ANOCoin** لبدء القيف أواي."
        settings = await self.db.fetchone("SELECT giveaway_enabled,required_guild_id FROM ad_settings_v2 WHERE guild_id=?", (guild.id,))
        required_guild_id = int(settings["required_guild_id"]) if settings and settings["required_guild_id"] else None
        if not await self.db.remove_balance(owner.id, guild.id, amount):
            return False, "❌ تعذر خصم المبلغ."
        ends = time.time() + duration
        cur = await self.db.execute("INSERT INTO ad_giveaways(guild_id,channel_id,owner_id,amount,ends_at,required_guild_id) VALUES(?,?,?,?,?,?)", (guild.id, channel_id, owner.id, amount, ends, required_guild_id))
        gid = int(cur.lastrowid)
        channel = guild.get_channel(channel_id)
        try:
            embed = discord.Embed(title="🎁 قيف أواي ANOCoin", description=f"الجائزة: **{amount:,} ANOCoin**\nينتهي: <t:{int(ends)}:R>\nاضغط على 🎉 للمشاركة.", colour=discord.Colour.green())
            message = await channel.send(embed=embed, view=GiveawayView(self, gid))
            await self.db.execute("UPDATE ad_giveaways SET message_id=? WHERE id=?", (message.id, gid))
            self.bot.add_view(GiveawayView(self, gid))
            await self.run_custom_event(channel, "after_giveaway", message)
        except discord.HTTPException:
            await self.db.execute("UPDATE ad_giveaways SET ended=1 WHERE id=?", (gid,))
            await self.db.add_balance(owner.id, guild.id, amount)
            return False, "❌ تعذر نشر القيف أواي؛ تمت إعادة المبلغ."
        return True, f"✅ تم إنشاء القيف أواي **#{gid}** وخصم **{amount:,} ANOCoin** من رصيدك."

    async def process_view_membership(self, row, user_id: int):
        required = row["required_guild_id"]
        if not required:
            return True
        guild = self.bot.get_guild(int(required))
        if guild is None:
            return False
        member = guild.get_member(user_id)
        if member is not None:
            return True
        try:
            await guild.fetch_member(user_id)
            return True
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return False

    @tasks.loop(seconds=10)
    async def worker(self):
        rows = await self.db.fetchall("SELECT * FROM ad_giveaways WHERE ended=0 AND ends_at<=?", (time.time(),))
        for row in rows:
            try:
                await self.finish_giveaway(row)
            except Exception as exc:
                self.bot.logger.error("Giveaway finish failed: %s", exc, exc_info=True)

    @worker.before_loop
    async def before_worker(self):
        await self.bot.wait_until_ready()

    async def finish_giveaway(self, row):
        cur = await self.db.execute("UPDATE ad_giveaways SET ended=1 WHERE id=? AND ended=0", (row["id"],))
        if cur.rowcount != 1:
            return
        channel = self.bot.get_channel(int(row["channel_id"]))
        entries = await self.db.fetchall("SELECT user_id FROM ad_giveaway_entries WHERE giveaway_id=?", (row["id"],))
        if not entries:
            await self.db.add_balance(int(row["owner_id"]), int(row["guild_id"]), int(row["amount"]))
            if channel:
                await channel.send("**لا يوجد اي فائز بسبب ان لا احد شارك بالقيف اواي**")
            return
        eligible = []
        for entry in entries:
            user_id = int(entry["user_id"])
            if await self.process_view_membership(row, user_id):
                eligible.append(user_id)
        if not eligible and row["required_guild_id"]:
            await self.db.add_balance(int(row["owner_id"]), int(row["guild_id"]), int(row["amount"]))
            if channel:
                await channel.send("**لا يوجد اي فائز بسبب ان لا احد دخل السيرفر المعلن عنه**")
            return
        if not eligible:
            eligible = [int(x["user_id"]) for x in entries]
        winner_id = random.choice(eligible)
        await self.db.add_balance(winner_id, int(row["guild_id"]), int(row["amount"]))
        await self.db.execute("UPDATE ad_giveaways SET winner_id=? WHERE id=?", (winner_id, row["id"]))
        if channel:
            message = await channel.send(f"🎉 مبروك <@{winner_id}>! فزت بـ **{int(row['amount']):,} ANOCoin**.", allowed_mentions=discord.AllowedMentions(users=True))
            await self.run_custom_event(channel, "after_all", message)


async def setup(bot):
    await bot.add_cog(AdvertisingShop(bot))
