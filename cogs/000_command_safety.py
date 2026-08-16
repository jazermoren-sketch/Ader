"""Global command/UI safety, SQLite repair and duplicate protection."""
from __future__ import annotations
import discord
from discord import app_commands
from discord.ext import commands

async def _view_error(view, interaction: discord.Interaction, error: Exception, item):
    print(f"UI error [{type(item).__name__}]: {error!r}")
    try:
        text="❌ وقع خطأ أثناء تنفيذ العملية. حاول مرة أخرى."
        if interaction.response.is_done(): await interaction.followup.send(text,ephemeral=True)
        else: await interaction.response.send_message(text,ephemeral=True)
    except Exception: pass

async def _modal_error(modal, interaction: discord.Interaction, error: Exception):
    print(f"Modal error [{type(modal).__name__}]: {error!r}")
    try:
        text="❌ وقع خطأ أثناء حفظ البيانات. تم تسجيل الخطأ في Log البوت."
        if interaction.response.is_done(): await interaction.followup.send(text,ephemeral=True)
        else: await interaction.response.send_message(text,ephemeral=True)
    except Exception: pass

class CommandSafety(commands.Cog):
    def __init__(self,bot):self.bot=bot;self.installed=False
    async def cog_load(self):
        if self.installed:return
        original=self.bot.tree.add_command
        def safe_add(command,*,guild=None,guilds=None,override=False):
            try:return original(command,guild=guild,guilds=guilds,override=override)
            except app_commands.CommandAlreadyRegistered:return original(command,guild=guild,guilds=guilds,override=True)
        self.bot.tree.add_command=safe_add;discord.ui.View.on_error=_view_error;discord.ui.Modal.on_error=_modal_error;self.bot.tree.on_error=self.tree_error
        await self.repair();self.installed=True
    async def tree_error(self,interaction,error):
        print(f"Application command error: {getattr(error,'original',error)!r}")
        try:
            text="❌ وقع خطأ أثناء تنفيذ الأمر. حاول مرة أخرى."
            if interaction.response.is_done():await interaction.followup.send(text,ephemeral=True)
            else:await interaction.response.send_message(text,ephemeral=True)
        except Exception:pass
    async def repair(self):
        db=self.bot.db
        await db.execute("CREATE TABLE IF NOT EXISTS processed_commands (command_key TEXT PRIMARY KEY, created_at REAL NOT NULL)")
        rows=await db.fetchall("PRAGMA table_info(ticket_panels)");existing={r[1] for r in rows}
        cols={"channel_id":"INTEGER","message_id":"INTEGER","title":"TEXT NOT NULL DEFAULT '🎫 الدعم الفني'","description":"TEXT NOT NULL DEFAULT 'اختار القسم المناسب لفتح تذكرة.'","image_url":"TEXT","mode":"TEXT NOT NULL DEFAULT 'buttons'","button_label":"TEXT NOT NULL DEFAULT 'فتح تذكرة'","button_emoji":"TEXT NOT NULL DEFAULT '🎫'","category_id":"INTEGER","support_role_id":"INTEGER","ticket_description":"TEXT NOT NULL DEFAULT 'شرح لينا المشكل ديالك بالتفصيل.'","options":"TEXT NOT NULL DEFAULT '[]'","created_at":"REAL NOT NULL DEFAULT 0"}
        for n,d in cols.items():
            if n not in existing: await db.execute(f"ALTER TABLE ticket_panels ADD COLUMN {n} {d}")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ticket_open_user ON tickets(guild_id,user_id,status)")
        try: await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_ticket_open_user ON tickets(guild_id,user_id) WHERE status='open'")
        except Exception as e: print(f"Ticket unique-index migration skipped: {e!r}")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ticket_panels_guild ON ticket_panels(guild_id)")
async def setup(bot):await bot.add_cog(CommandSafety(bot))
