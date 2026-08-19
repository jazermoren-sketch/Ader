from __future__ import annotations

import random
import re
import time
from typing import Optional

import discord
from discord.ext import commands, tasks

EMOJI = "🎉"


def parse_duration(value: str) -> Optional[int]:
    """Parse 30s, 10m, 2h, 1d into seconds."""
    match = re.fullmatch(r"\s*(\d+)\s*([smhd])\s*", value.lower())
    if not match:
        return None
    amount = int(match.group(1))
    unit = {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]
    seconds = amount * unit
    return seconds if 10 <= seconds <= 7 * 86400 else None


def parse_prize(value: str):
    """Return ('anoris', amount) or ('external', text)."""
    raw = value.strip()
    match = re.fullmatch(r"(?:anoris|anoris\s*[:=])\s*([0-9][0-9_,]*)", raw, re.I)
    if match:
        amount = int(match.group(1).replace(",", "").replace("_", ""))
        if amount > 0:
            return "anoris", amount
    return "external", raw[:500]


class GiveawayCreateModal(discord.ui.Modal, title="🎉 إنشاء Giveaway"):
    prize = discord.ui.TextInput(
        label="الجائزة",
        placeholder="مثال: ANORIS:500000 أو 5M ProBot Credits",
        max_length=500,
        required=True,
    )
    duration = discord.ui.TextInput(
        label="المدة",
        placeholder="مثال: 30m أو 2h أو 1d",
        max_length=10,
        required=True,
    )
    winners = discord.ui.TextInput(
        label="عدد الفائزين",
        placeholder="1",
        max_length=3,
        required=True,
    )

    def __init__(self, cog: "AdGiveaways"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        if not self.cog.is_admin(interaction):
            return await interaction.response.send_message("❌ Administrator فقط.", ephemeral=True)

        seconds = parse_duration(str(self.duration.value))
        if seconds is None:
            return await interaction.response.send_message(
                "❌ المدة غير صحيحة. استعمل مثلاً `30m` أو `2h` أو `1d` (من 10 ثوانٍ إلى 7 أيام).",
                ephemeral=True,
            )

        try:
            winner_count = int(str(self.winners.value).strip())
        except ValueError:
            winner_count = 0
        if not 1 <= winner_count <= 20:
            return await interaction.response.send_message("❌ عدد الفائزين يجب أن يكون بين 1 و20.", ephemeral=True)

        prize_type, prize_value = parse_prize(str(self.prize.value))
        if not prize_value:
            return await interaction.response.send_message("❌ الجائزة فارغة.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        ends_at = time.time() + seconds
        cur = await self.cog.db.execute(
            """INSERT INTO ad_giveaways_v2
               (guild_id,channel_id,message_id,prize_type,prize_value,ends_at,winners,ended,created_by,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                interaction.guild.id,
                interaction.channel.id,
                0,
                prize_type,
                str(prize_value),
                ends_at,
                winner_count,
                0,
                interaction.user.id,
                time.time(),
            ),
        )
        giveaway_id = int(cur.lastrowid)

        prize_text = f"**{int(prize_value):,} ANORIS**" if prize_type == "anoris" else f"**{discord.utils.escape_markdown(str(prize_value))}**"
        embed = discord.Embed(
            title="🎉 Giveaway",
            description=(
                f"🎁 **الجائزة:** {prize_text}\n"
                f"👑 **الفائزون:** {winner_count}\n"
                f"⏰ **ينتهي:** <t:{int(ends_at)}:R>\n\n"
                f"اضغط على {EMOJI} للمشاركة."
            ),
            colour=discord.Colour.gold(),
        )
        embed.set_footer(text=f"Ader Giveaway • #{giveaway_id}")
        message = await interaction.channel.send(embed=embed)
        await message.add_reaction(EMOJI)
        await self.cog.db.execute("UPDATE ad_giveaways_v2 SET message_id=? WHERE id=?", (message.id, giveaway_id))

        await interaction.followup.send(f"✅ تم إنشاء Giveaway **#{giveaway_id}** بنجاح.", ephemeral=True)


class GiveawayView(discord.ui.View):
    def __init__(self, cog: "AdGiveaways"):
        super().__init__(timeout=300)
        self.cog = cog

    @discord.ui.button(label="إنشاء Giveaway", emoji="🎉", style=discord.ButtonStyle.success, custom_id="ader:adsettings:create_giveaway")
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.cog.is_admin(interaction):
            return await interaction.response.send_message("❌ Administrator فقط.", ephemeral=True)
        await interaction.response.send_modal(GiveawayCreateModal(self.cog))


class AdGiveaways(commands.Cog):
    """Persistent multi-giveaway engine for Ader. ANORIS rewards are paid automatically."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db
        self.expiry_worker.start()

    async def cog_load(self):
        await self.db.execute(
            """CREATE TABLE IF NOT EXISTS ad_giveaways_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                prize_type TEXT NOT NULL,
                prize_value TEXT NOT NULL,
                ends_at REAL NOT NULL,
                winners INTEGER NOT NULL DEFAULT 1,
                ended INTEGER NOT NULL DEFAULT 0,
                created_by INTEGER NOT NULL,
                created_at REAL NOT NULL,
                ended_at REAL,
                winner_ids TEXT
            )"""
        )
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_giveaways_active ON ad_giveaways_v2(ended, ends_at)"
        )

    def cog_unload(self):
        self.expiry_worker.cancel()

    def is_admin(self, interaction: discord.Interaction) -> bool:
        return bool(interaction.guild and interaction.user.guild_permissions.administrator)

    @tasks.loop(seconds=15)
    async def expiry_worker(self):
        rows = await self.db.fetchall(
            "SELECT * FROM ad_giveaways_v2 WHERE ended=0 AND ends_at<=? ORDER BY id ASC LIMIT 25",
            (time.time(),),
        )
        for row in rows:
            try:
                await self.finish_giveaway(dict(row))
            except Exception as exc:
                self.bot.logger.error(f"Giveaway #{row['id']} failed to finish: {exc}", exc_info=True)

    @expiry_worker.before_loop
    async def before_expiry_worker(self):
        await self.bot.wait_until_ready()

    async def collect_participants(self, message: discord.Message) -> list[discord.Member]:
        reaction = discord.utils.get(message.reactions, emoji=EMOJI)
        if reaction is None:
            return []
        users = [user async for user in reaction.users()]
        result = []
        for user in users:
            if user.bot:
                continue
            member = message.guild.get_member(user.id)
            if member is not None:
                result.append(member)
        return list({member.id: member for member in result}.values())

    async def finish_giveaway(self, row: dict):
        giveaway_id = int(row["id"])
        claimed = await self.db.execute(
            "UPDATE ad_giveaways_v2 SET ended=2 WHERE id=? AND ended=0",
            (giveaway_id,),
        )
        if claimed.rowcount != 1:
            return

        channel = self.bot.get_channel(int(row["channel_id"]))
        if channel is None:
            await self.db.execute("UPDATE ad_giveaways_v2 SET ended=1,ended_at=? WHERE id=?", (time.time(), giveaway_id))
            return

        try:
            message = await channel.fetch_message(int(row["message_id"]))
        except discord.HTTPException:
            await self.db.execute("UPDATE ad_giveaways_v2 SET ended=1,ended_at=? WHERE id=?", (time.time(), giveaway_id))
            return

        participants = await self.collect_participants(message)
        winner_count = min(int(row["winners"]), len(participants))
        winners = random.sample(participants, winner_count) if winner_count else []
        winner_ids = [member.id for member in winners]

        if row["prize_type"] == "anoris" and winners:
            amount = int(row["prize_value"])
            for member in winners:
                await self.db.update_global_balance(member.id, amount)

        await self.db.execute(
            "UPDATE ad_giveaways_v2 SET ended=1,ended_at=?,winner_ids=? WHERE id=?",
            (time.time(), ",".join(map(str, winner_ids)), giveaway_id),
        )

        if winners:
            mentions = ", ".join(member.mention for member in winners)
            if row["prize_type"] == "anoris":
                prize = f"**{int(row['prize_value']):,} ANORIS لكل فائز**"
                delivery = "\n💰 تم تحويل ANORIS تلقائياً إلى رصيد الفائزين."
            else:
                prize = f"**{discord.utils.escape_markdown(str(row['prize_value']))}**"
                delivery = "\n⚠️ هذه جائزة خارجية؛ تسليمها يتم يدوياً."
            text = f"🎉 **انتهى Giveaway #{giveaway_id}!**\n🏆 الفائز: {mentions}\n🎁 الجائزة: {prize}{delivery}"
        else:
            text = f"🎉 **انتهى Giveaway #{giveaway_id}!**\n❌ لم يشارك أي عضو."

        try:
            await channel.send(text)
        except discord.HTTPException:
            pass

    @commands.hybrid_command(name="ad-giveaway", description="إنشاء Giveaway بعملة ANORIS أو جائزة خارجية")
    async def ad_giveaway(self, ctx: commands.Context):
        if not ctx.guild or not ctx.author.guild_permissions.administrator:
            return await ctx.send("❌ هذا الأمر مخصص للـAdministrator فقط.", ephemeral=True)
        await ctx.send("⚙️ **إدارة Giveaway**\nيمكنك إنشاء أكثر من Giveaway في نفس الوقت.", view=GiveawayView(self), ephemeral=True)

    @discord.app_commands.command(name="ad-giveaways", description="عرض Giveaways النشطة")
    async def ad_giveaways(self, interaction: discord.Interaction):
        if not self.is_admin(interaction):
            return await interaction.response.send_message("❌ Administrator فقط.", ephemeral=True)
        rows = await self.db.fetchall(
            "SELECT * FROM ad_giveaways_v2 WHERE guild_id=? AND ended=0 ORDER BY ends_at ASC",
            (interaction.guild.id,),
        )
        if not rows:
            return await interaction.response.send_message("📭 لا توجد Giveaways نشطة.", ephemeral=True)
        lines = []
        for row in rows[:20]:
            prize = f"{int(row['prize_value']):,} ANORIS" if row['prize_type'] == 'anoris' else str(row['prize_value'])
            lines.append(f"🎉 **#{row['id']}** — {prize} — <t:{int(row['ends_at'])}:R> — {row['winners']} فائز")
        embed = discord.Embed(title="🎉 Giveaways النشطة", description="\n".join(lines), colour=discord.Colour.gold())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.app_commands.command(name="ad-giveaway-end", description="إنهاء Giveaway واختيار الفائزين الآن")
    @discord.app_commands.describe(giveaway_id="رقم الـGiveaway")
    async def ad_giveaway_end(self, interaction: discord.Interaction, giveaway_id: int):
        if not self.is_admin(interaction):
            return await interaction.response.send_message("❌ Administrator فقط.", ephemeral=True)
        row = await self.db.fetchone(
            "SELECT * FROM ad_giveaways_v2 WHERE id=? AND guild_id=? AND ended=0",
            (giveaway_id, interaction.guild.id),
        )
        if not row:
            return await interaction.response.send_message("❌ الـGiveaway غير موجودة أو منتهية.", ephemeral=True)
        await interaction.response.send_message(f"⏳ جاري إنهاء Giveaway **#{giveaway_id}**...", ephemeral=True)
        await self.finish_giveaway(dict(row))


async def setup(bot: commands.Bot):
    await bot.add_cog(AdGiveaways(bot))
