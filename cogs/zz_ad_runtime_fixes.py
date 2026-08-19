from __future__ import annotations

import asyncio
import inspect
import time

import discord
from discord.ext import commands, tasks


class AdRuntimeFixes(commands.Cog):
    """Final runtime fixes for multi-room advertising and custom reply delivery."""

    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.multi_room_worker.start()

    async def cog_load(self):
        await self.db.execute("""CREATE TABLE IF NOT EXISTS ad_custom_delivery(
            channel_id INTEGER NOT NULL,
            custom_message_id INTEGER NOT NULL,
            delivered_at REAL NOT NULL,
            PRIMARY KEY(channel_id, custom_message_id)
        )""")
        self._patch_shop_purchase()

    def cog_unload(self):
        self.multi_room_worker.cancel()

    def _patch_shop_purchase(self):
        shop_cog = self.bot.get_cog("Shop")
        if not shop_cog:
            return
        current = getattr(shop_cog, "_purchase", None)
        if not current or getattr(shop_cog, "_ader_multi_room_fixed", False):
            return

        # advertising_shop wraps the original purchase in a closure. Recover that
        # original function so we can reapply delivery without the one-active-room ban.
        original = None
        closure = getattr(current, "__closure__", None) or ()
        for cell in closure:
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            if inspect.iscoroutinefunction(value) and value is not current:
                original = value
                break
        if original is None:
            return

        db = self.db
        bot = self.bot

        async def purchase(guild_id, user_id, item_id):
            ok, text = await original(guild_id, user_id, item_id)
            if not ok:
                return ok, text
            item = await db.fetchone("SELECT * FROM shop WHERE guild_id=? AND id=?", (guild_id, item_id))
            if not item:
                return ok, text
            import json
            try:
                data = json.loads(item["data"] or "{}")
            except Exception:
                data = {}
            delivery = data.get("delivery") or {}
            if delivery.get("type") != "ad_room":
                return ok, text
            guild = bot.get_guild(guild_id)
            member = guild.get_member(user_id) if guild else None
            if not guild or not member:
                await db.add_balance(user_id, guild_id, int(item["price"]))
                return False, "❌ تعذر تسليم المنتج؛ تمت إعادة المبلغ."
            if not guild.me or not guild.me.guild_permissions.manage_channels:
                await db.add_balance(user_id, guild_id, int(item["price"]))
                return False, "❌ البوت يحتاج Manage Channels؛ تمت إعادة المبلغ."

            private = delivery.get("visibility") == "private"
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=not private, send_messages=False, read_message_history=True),
                member: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, embed_links=True, read_message_history=True),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_messages=True, attach_files=True, embed_links=True, read_message_history=True),
            }
            try:
                from cogs.advertising_shop import clean_name
                channel = await guild.create_text_channel(
                    clean_name(f"ad-{member.display_name}"),
                    category=None,
                    overwrites=overwrites,
                    reason="Ader shop advertising room",
                )
            except (discord.Forbidden, discord.HTTPException):
                await db.add_balance(user_id, guild_id, int(item["price"]))
                return False, "❌ تعذر إنشاء روم؛ تمت إعادة المبلغ."

            mention = delivery.get("mention_type", "everyone")
            await db.execute(
                "INSERT INTO ad_rooms(guild_id,channel_id,owner_id,mention_type,active) VALUES(?,?,?,?,1)",
                (guild_id, channel.id, user_id, mention),
            )
            ad_shop = bot.get_cog("AdvertisingShop")
            if ad_shop:
                await channel.send(member.mention, allowed_mentions=discord.AllowedMentions(users=True))
                await ad_shop.render_panel(channel)
            return True, text + f"\n🏠 تم تسليم الروم: {channel.mention}"

        shop_cog._purchase = purchase
        shop_cog._ader_ad_delivery = True
        shop_cog._ader_multi_room_fixed = True

    @tasks.loop(seconds=4)
    async def multi_room_worker(self):
        await self.bot.wait_until_ready()
        try:
            rooms = await self.db.fetchall("SELECT guild_id,channel_id FROM ad_rooms WHERE active=1")
            for room in rooms:
                channel = self.bot.get_channel(int(room["channel_id"]))
                if not isinstance(channel, discord.TextChannel):
                    continue
                await self._deliver_custom_messages(channel, int(room["guild_id"]))
        except Exception as exc:
            print(f"Ad custom delivery worker: {exc!r}")

    async def _deliver_custom_messages(self, channel: discord.TextChannel, guild_id: int):
        rows = await self.db.fetchall(
            "SELECT * FROM ad_custom_messages WHERE guild_id=? AND enabled=1 ORDER BY position,id",
            (guild_id,),
        )
        if not rows:
            return

        history = [m async for m in channel.history(limit=100, oldest_first=True)]
        bot_id = self.bot.user.id if self.bot.user else 0
        bot_messages = [m for m in history if m.author.id == bot_id]
        if not bot_messages:
            return

        anchors = {}
        # Main advertisement = first bot message containing text/mention and no giveaway embed.
        for m in bot_messages:
            if m.embeds and any((e.title or "").startswith("🎁") for e in m.embeds):
                anchors.setdefault("giveaway", m)
            elif m.attachments:
                anchors.setdefault("image", m)
            elif "ad" not in anchors and m.content:
                anchors["ad"] = m
        if not anchors.get("ad"):
            return

        # after_ad can run once the advertisement exists.
        # after_giveaway waits for the giveaway message if configured.
        # after_image waits for an image if one exists; otherwise after_all is used as final stage.
        custom_sent = {}
        for m in bot_messages:
            delivered = await self.db.fetchone(
                "SELECT 1 FROM ad_custom_delivery WHERE channel_id=? AND custom_message_id=?",
                (channel.id, 0),
            )
            if delivered:
                break

        for row in rows:
            done = await self.db.fetchone(
                "SELECT 1 FROM ad_custom_delivery WHERE channel_id=? AND custom_message_id=?",
                (channel.id, int(row["id"])),
            )
            if done:
                continue
            event = str(row["event"])
            ready = (
                event == "after_ad" or
                (event == "after_giveaway" and "giveaway" in anchors) or
                (event == "after_image" and "image" in anchors) or
                (event == "after_all" and bool(anchors.get("ad")))
            )
            if not ready:
                continue

            reply_to = row["reply_to"]
            reference = None
            if reply_to is not None:
                reply_to = int(reply_to)
                # Special negative IDs are reserved for the real ad flow anchors.
                if reply_to == -1:
                    reference = anchors.get("ad")
                elif reply_to == -2:
                    reference = anchors.get("giveaway")
                elif reply_to == -3:
                    reference = anchors.get("image")
                else:
                    reference = custom_sent.get(reply_to)
                    if reference is None:
                        prior = await self.db.fetchone(
                            "SELECT 1 FROM ad_custom_delivery WHERE channel_id=? AND custom_message_id=?",
                            (channel.id, reply_to),
                        )
                        if prior:
                            continue
            try:
                message = await channel.send(
                    str(row["content"]),
                    reference=reference,
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                custom_sent[int(row["id"])] = message
                await self.db.execute(
                    "INSERT OR IGNORE INTO ad_custom_delivery(channel_id,custom_message_id,delivered_at) VALUES(?,?,?)",
                    (channel.id, int(row["id"]), time.time()),
                )
            except (discord.Forbidden, discord.HTTPException):
                return

    @multi_room_worker.before_loop
    async def before_worker(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(AdRuntimeFixes(bot))
