"""Support for the `-بوت <member>` shortcut.

The main bot already dispatches messages through Bot.process_commands().
This small patch adds the member-targeted form without changing the existing
Utility cog or any other command handlers.
"""

import discord
from discord.ext import commands


_original_process_commands = commands.Bot.process_commands


async def _process_commands_with_bot_target(self, message: discord.Message):
    if message.guild is not None and not message.author.bot:
        parts = message.content.strip().split()
        if parts and parts[0] == "-بوت" and len(parts) >= 2:
            target = None

            # Preferred form: -بوت @member
            if message.mentions:
                target = message.mentions[0]

            # Also support: -بوت 123456789012345678
            if target is None:
                token = parts[1].strip().strip("<@!>")
                if token.isdigit():
                    target = message.guild.get_member(int(token))
                    if target is None:
                        try:
                            target = await message.guild.fetch_member(int(token))
                        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                            target = None

            if target is None:
                await message.reply(
                    "❌ الاستعمال: `-بوت @العضو` أو `-بوت ID`",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return

            embed = discord.Embed(
                title="🤖 معلومات البوت",
                description=f"المستخدم المحدد: {target.mention}\n\nالحالة: **متصل** ✅",
                color=discord.Color.green(),
            )
            embed.add_field(name="👤 الاسم", value=f"`{target.display_name}`", inline=True)
            embed.add_field(name="🆔 ID", value=f"`{target.id}`", inline=True)
            embed.add_field(name="🤖 Bot", value="نعم" if target.bot else "لا", inline=True)
            embed.set_thumbnail(url=target.display_avatar.url)

            await message.reply(
                embed=embed,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

    await _original_process_commands(self, message)


# Install the wrapper once when the cogs package is imported.
if commands.Bot.process_commands is not _process_commands_with_bot_target:
    commands.Bot.process_commands = _process_commands_with_bot_target
