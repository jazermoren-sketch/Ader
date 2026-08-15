"""نظام سحوبات احترافي بالكامل بالعربية - Ader Ultimate."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from database.db_manager import DatabaseManager
from utils.embeds import EmbedFactory, EmbedColor
from utils.permissions import is_admin
from utils.converters import TimeConverter

logger = logging.getLogger(__name__)


class GiveawayView(discord.ui.View):
    """واجهة دائمة للسحب: مشاركة + عرض العدد، وتعمل بعد إعادة تشغيل البوت."""

    def __init__(self, giveaway_id: int, cog: 'Giveaways'):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
        self.cog = cog

    @discord.ui.button(
        label='المشاركة في السحب',
        style=discord.ButtonStyle.success,
        emoji='🎉',
        custom_id='ader:giveaway:enter',
    )
    async def enter_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        giveaway = await self.cog.db.fetchone(
            'SELECT * FROM giveaways WHERE id=?', (self.giveaway_id,)
        )
        if not giveaway or giveaway['ended']:
            return await interaction.response.send_message(
                embed=EmbedFactory.error('انتهى السحب', 'هذا السحب انتهى أو لم يعد موجوداً.'),
                ephemeral=True,
            )

        if giveaway['ends_at'] <= time.time():
            await self.cog.end_giveaway(dict(giveaway))
            return await interaction.response.send_message(
                embed=EmbedFactory.warning('انتهى السحب', 'انتهى وقت هذا السحب.'),
                ephemeral=True,
            )

        # منع الحسابات الآلية من التسجيل.
        if interaction.user.bot:
            return await interaction.response.send_message(
                '❌ الحسابات الآلية لا يمكنها المشاركة في السحوبات.', ephemeral=True
            )

        # شرط الرتبة الاختياري محفوظ داخل عمود data.
        required_role_id = await self.cog.get_required_role(self.giveaway_id)
        if required_role_id:
            role = interaction.guild.get_role(required_role_id)
            if role and role not in getattr(interaction.user, 'roles', []):
                return await interaction.response.send_message(
                    embed=EmbedFactory.warning(
                        'رتبة مطلوبة',
                        f'❌ يجب أن تمتلك رتبة {role.mention} للمشاركة في هذا السحب.',
                    ),
                    ephemeral=True,
                )

        existing = await self.cog.db.fetchone(
            'SELECT 1 FROM giveaway_entries WHERE giveaway_id=? AND user_id=?',
            (self.giveaway_id, interaction.user.id),
        )
        if existing:
            return await interaction.response.send_message(
                embed=EmbedFactory.warning(
                    'أنت مشارك بالفعل',
                    'لقد سجلت مشاركتك في هذا السحب من قبل.',
                ),
                ephemeral=True,
            )

        await self.cog.db.execute(
            'INSERT INTO giveaway_entries(giveaway_id,user_id,created_at) VALUES(?,?,?)',
            (self.giveaway_id, interaction.user.id, time.time()),
        )
        count = await self.cog.entry_count(self.giveaway_id)
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                'تم تسجيل مشاركتك 🎉',
                f'تمت إضافتك إلى السحب على **{giveaway["prize"]}**.\n\n'
                f'👥 عدد المشاركين الآن: **{count}**',
            ),
            ephemeral=True,
        )
        await self.cog.refresh_message(dict(giveaway), count=count)


class Giveaways(commands.Cog):
    """سحوبات SQLite احترافية مع مشاركة، شروط رتبة، إنهاء، وإعادة سحب."""

    def __init__(self, bot: commands.Bot, db: DatabaseManager, config: dict):
        self.bot = bot
        self.db = db
        self.config = config
        self.module_config = config.get('modules', {}).get('giveaways', {})
        self.giveaway_task = self.bot.loop.create_task(self.check_giveaways())

    async def ensure_tables(self):
        await self.db.execute('''
            CREATE TABLE IF NOT EXISTS giveaway_entries (
                giveaway_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY(giveaway_id, user_id)
            )
        ''')
        # إضافة أعمدة اختيارية بدون كسر قواعد البيانات القديمة.
        for statement in (
            "ALTER TABLE giveaways ADD COLUMN data TEXT NOT NULL DEFAULT '{}'",
            "ALTER TABLE giveaways ADD COLUMN required_role_id INTEGER",
        ):
            try:
                await self.db.execute(statement)
            except Exception:
                pass

    def cog_unload(self):
        self.giveaway_task.cancel()

    async def check_giveaways(self):
        await self.bot.wait_until_ready()
        await self.ensure_tables()
        while not self.bot.is_closed():
            try:
                rows = await self.db.fetchall(
                    'SELECT * FROM giveaways WHERE ended=0 AND ends_at<=? LIMIT 100',
                    (time.time(),),
                )
                for giveaway in rows:
                    await self.end_giveaway(dict(giveaway))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error('خطأ في فحص السحوبات: %s', exc, exc_info=True)
            await asyncio.sleep(15)

    async def _participants(self, giveaway_id: int):
        rows = await self.db.fetchall(
            'SELECT user_id FROM giveaway_entries WHERE giveaway_id=?',
            (giveaway_id,),
        )
        return [int(row['user_id']) for row in rows]

    async def entry_count(self, giveaway_id: int) -> int:
        row = await self.db.fetchone(
            'SELECT COUNT(*) AS count FROM giveaway_entries WHERE giveaway_id=?',
            (giveaway_id,),
        )
        return int(row['count']) if row else 0

    async def get_required_role(self, giveaway_id: int) -> Optional[int]:
        try:
            row = await self.db.fetchone(
                'SELECT required_role_id FROM giveaways WHERE id=?', (giveaway_id,)
            )
            return int(row['required_role_id']) if row and row['required_role_id'] else None
        except Exception:
            return None

    async def get_giveaway_message(self, giveaway: dict):
        guild = self.bot.get_guild(int(giveaway['guild_id']))
        if not guild:
            return None, None
        channel = guild.get_channel(int(giveaway['channel_id']))
        if not channel:
            return guild, None
        try:
            message = await channel.fetch_message(int(giveaway['message_id'])) if giveaway['message_id'] else None
        except discord.HTTPException:
            message = None
        return guild, message

    def build_embed(self, giveaway: dict, participant_count: int) -> discord.Embed:
        ends = int(giveaway['ends_at'])
        winners = int(giveaway.get('winners', 1))
        description = (
            f'🎁 **الجائزة**\n> {giveaway["prize"]}\n\n'
            f'🏆 **عدد الفائزين:** {winners}\n'
            f'👥 **المشاركون:** {participant_count}\n'
            f'👤 **صاحب السحب:** <@{giveaway.get("host_id", 0)}>\n'
            f'⏰ **ينتهي:** <t:{ends}:R>\n'
        )
        role_id = giveaway.get('required_role_id')
        if role_id:
            description += f'\n🔐 **شرط المشاركة:** امتلاك <@&{int(role_id)}>'
        description += '\n\nاضغط على زر **المشاركة في السحب** للدخول.'

        embed = discord.Embed(
            title='🎉 سحب جديد 🎉',
            description=description,
            color=getattr(EmbedColor, 'SUCCESS', discord.Color.green()),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text='Ader • نظام السحوبات')
        return embed

    async def refresh_message(self, giveaway: dict, count: Optional[int] = None):
        if giveaway.get('ended'):
            return
        if count is None:
            count = await self.entry_count(int(giveaway['id']))
        _, message = await self.get_giveaway_message(giveaway)
        if not message:
            return
        try:
            await message.edit(
                embed=self.build_embed(giveaway, count),
                view=GiveawayView(int(giveaway['id']), self),
            )
        except discord.HTTPException:
            pass

    async def end_giveaway(self, giveaway: dict):
        # قفل العملية أولاً حتى لا يتم اختيار الفائزين مرتين عند تزامن الـtask.
        current = await self.db.fetchone('SELECT ended FROM giveaways WHERE id=?', (giveaway['id'],))
        if not current or current['ended']:
            return
        await self.db.execute('UPDATE giveaways SET ended=1 WHERE id=?', (giveaway['id'],))

        try:
            guild = self.bot.get_guild(int(giveaway['guild_id']))
            channel = guild.get_channel(int(giveaway['channel_id'])) if guild else None
            participants = await self._participants(int(giveaway['id']))
            winners_count = max(1, int(giveaway.get('winners', 1)))

            if not channel:
                return

            if not participants:
                embed = EmbedFactory.warning(
                    '🎉 انتهى السحب',
                    f'🎁 **الجائزة:** {giveaway["prize"]}\n\n'
                    '😔 لم يشارك أي شخص في هذا السحب.',
                )
                await channel.send(embed=embed)
                return

            winners = random.sample(participants, min(winners_count, len(participants)))
            mentions = ' '.join(f'<@{uid}>' for uid in winners)
            winner_text = '\n'.join(f'🏆 <@{uid}>' for uid in winners)
            embed = discord.Embed(
                title='🎉 انتهى السحب! 🎉',
                description=(
                    f'🎁 **الجائزة:** {giveaway["prize"]}\n\n'
                    f'🏆 **الفائزون:**\n{winner_text}\n\n'
                    'مبروك للفائزين! 🎊'
                ),
                color=discord.Color.gold(),
                timestamp=discord.utils.utcnow(),
            )
            embed.set_footer(text='Ader • نظام السحوبات')
            await channel.send(content=mentions, embed=embed)
            logger.info('تم إنهاء السحب %s', giveaway['id'])
        except Exception as exc:
            logger.error('خطأ أثناء إنهاء السحب %s: %s', giveaway['id'], exc, exc_info=True)

    @app_commands.command(name='giveaway', description='إنشاء سحب احترافي')
    @app_commands.describe(
        prize='الجائزة التي سيتم السحب عليها',
        duration='مدة السحب: 10m أو 2h أو 1d',
        winners='عدد الفائزين (من 1 إلى 20)',
        required_role='رتبة اختيارية مطلوبة للمشاركة',
    )
    @is_admin()
    async def start_giveaway(
        self,
        interaction: discord.Interaction,
        prize: str,
        duration: str,
        winners: int = 1,
        required_role: Optional[discord.Role] = None,
    ):
        if winners < 1 or winners > 20:
            return await interaction.response.send_message(
                embed=EmbedFactory.error('عدد الفائزين غير صالح', 'اختر عدداً بين 1 و20.'),
                ephemeral=True,
            )
        seconds = TimeConverter.parse(duration)
        if not seconds or seconds < 60 or seconds > 2592000:
            return await interaction.response.send_message(
                embed=EmbedFactory.error(
                    'المدة غير صالحة',
                    'المدة يجب أن تكون بين دقيقة واحدة و30 يوماً. مثال: `30m` أو `2h` أو `7d`.',
                ),
                ephemeral=True,
            )
        if not prize.strip():
            return await interaction.response.send_message('❌ يجب كتابة الجائزة.', ephemeral=True)

        await self.ensure_tables()
        ends_at = time.time() + seconds
        cur = await self.db.execute(
            'INSERT INTO giveaways(guild_id,channel_id,message_id,prize,ends_at,winners,ended,required_role_id) VALUES(?,?,?,?,?,?,0,?)',
            (
                interaction.guild.id,
                interaction.channel.id,
                0,
                prize.strip(),
                ends_at,
                winners,
                required_role.id if required_role else None,
            ),
        )
        giveaway_id = int(cur.lastrowid)
        end_timestamp = int(ends_at)
        giveaway = {
            'id': giveaway_id,
            'guild_id': interaction.guild.id,
            'channel_id': interaction.channel.id,
            'message_id': 0,
            'prize': prize.strip(),
            'ends_at': ends_at,
            'winners': winners,
            'ended': 0,
            'required_role_id': required_role.id if required_role else None,
            'host_id': interaction.user.id,
        }

        # نحفظ صاحب السحب داخل data بدون الحاجة إلى كسر قواعد البيانات القديمة.
        try:
            await self.db.execute(
                "UPDATE giveaways SET data=? WHERE id=?",
                (f'{{"host_id": {interaction.user.id}}}', giveaway_id),
            )
        except Exception:
            pass

        embed = self.build_embed(giveaway, 0)
        msg = await interaction.channel.send(embed=embed, view=GiveawayView(giveaway_id, self))
        await self.db.execute(
            'UPDATE giveaways SET message_id=? WHERE id=?', (msg.id, giveaway_id)
        )
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                'تم إنشاء السحب 🎉',
                f'تم نشر السحب بنجاح في {interaction.channel.mention}.\n'
                f'🆔 رقم السحب: `{giveaway_id}`\n'
                f'⏰ ينتهي <t:{end_timestamp}:R>.',
            ),
            ephemeral=True,
        )

    @app_commands.command(name='gend', description='إنهاء سحب قبل موعده')
    @app_commands.describe(message_id='معرّف رسالة السحب')
    @is_admin()
    async def end_giveaway_early(self, interaction: discord.Interaction, message_id: str):
        try:
            msg_id = int(message_id)
        except ValueError:
            return await interaction.response.send_message(
                embed=EmbedFactory.error('المعرّف غير صالح', 'أرسل Message ID صحيحاً.'),
                ephemeral=True,
            )
        row = await self.db.fetchone(
            'SELECT * FROM giveaways WHERE guild_id=? AND message_id=? AND ended=0',
            (interaction.guild.id, msg_id),
        )
        if not row:
            return await interaction.response.send_message(
                embed=EmbedFactory.error('لم يتم العثور على السحب', 'تأكد من Message ID وأن السحب ما زال نشطاً.'),
                ephemeral=True,
            )
        await self.end_giveaway(dict(row))
        await interaction.response.send_message(
            embed=EmbedFactory.success('تم إنهاء السحب', 'تم إنهاء السحب واختيار الفائزين.'),
            ephemeral=True,
        )

    @app_commands.command(name='greroll', description='إعادة سحب فائزين لسحب منتهٍ')
    @app_commands.describe(message_id='معرّف رسالة السحب')
    @is_admin()
    async def reroll_giveaway(self, interaction: discord.Interaction, message_id: str):
        try:
            msg_id = int(message_id)
        except ValueError:
            return await interaction.response.send_message(
                embed=EmbedFactory.error('المعرّف غير صالح', 'أرسل Message ID صحيحاً.'),
                ephemeral=True,
            )
        row = await self.db.fetchone(
            'SELECT * FROM giveaways WHERE guild_id=? AND message_id=? AND ended=1',
            (interaction.guild.id, msg_id),
        )
        if not row:
            return await interaction.response.send_message(
                embed=EmbedFactory.error('لم يتم العثور على السحب', 'لا يوجد سحب منتهٍ بهذه الرسالة.'),
                ephemeral=True,
            )
        participants = await self._participants(int(row['id']))
        if not participants:
            return await interaction.response.send_message(
                embed=EmbedFactory.error('لا يوجد مشاركون', 'هذا السحب لم يشارك فيه أي شخص.'),
                ephemeral=True,
            )
        winners = random.sample(participants, min(int(row['winners']), len(participants)))
        winner_text = '\n'.join(f'🏆 <@{uid}>' for uid in winners)
        mentions = ' '.join(f'<@{uid}>' for uid in winners)
        embed = discord.Embed(
            title='🎉 إعادة سحب الفائزين',
            description=(
                f'🎁 **الجائزة:** {row["prize"]}\n\n'
                f'🏆 **الفائزون الجدد:**\n{winner_text}'
            ),
            color=discord.Color.gold(),
        )
        embed.set_footer(text='Ader • نظام السحوبات')
        await interaction.response.send_message(content=mentions, embed=embed)


async def setup(bot: commands.Bot):
    cog = Giveaways(bot, bot.db, bot.config)
    await cog.ensure_tables()
    await bot.add_cog(cog)
