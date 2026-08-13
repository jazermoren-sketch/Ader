from __future__ import annotations
import asyncio, json, re, time
import discord
from discord import app_commands
from discord.ext import commands

TABLES = """
CREATE TABLE IF NOT EXISTS ticket_panels (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, name TEXT NOT NULL,
 description TEXT NOT NULL DEFAULT '', category_id INTEGER, staff_roles TEXT NOT NULL DEFAULT '[]',
 mention_roles TEXT NOT NULL DEFAULT '[]', button_label TEXT NOT NULL DEFAULT 'Open Ticket',
 button_emoji TEXT, button_style TEXT NOT NULL DEFAULT 'primary', image_url TEXT,
 log_channel_id INTEGER, max_open INTEGER NOT NULL DEFAULT 1, auto_close_minutes INTEGER NOT NULL DEFAULT 0,
 created_at REAL NOT NULL, UNIQUE(guild_id,name)
);
CREATE TABLE IF NOT EXISTS ticket_panel_messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, panel_id INTEGER NOT NULL,
 channel_id INTEGER NOT NULL, message_id INTEGER NOT NULL
);
"""

async def ensure_tables(db):
    await db.connection.executescript(TABLES)
    await db.connection.commit()

class PanelButton(discord.ui.Button):
    def __init__(self, cog, panel):
        style = getattr(discord.ButtonStyle, panel['button_style'], discord.ButtonStyle.primary)
        super().__init__(label=panel['button_label'][:80], style=style, emoji=panel['button_emoji'] or None,
                         custom_id=f"ader:ticket:panel:{panel['id']}")
        self.cog, self.panel = cog, panel
    async def callback(self, interaction):
        await self.cog.open_panel_ticket(interaction, self.panel['id'])

class PanelView(discord.ui.View):
    def __init__(self, cog, panels):
        super().__init__(timeout=None)
        for panel in panels[:25]:
            self.add_item(PanelButton(cog, panel))

class TicketView(discord.ui.View):
    def __init__(self, cog, ticket_id):
        super().__init__(timeout=None)
        self.cog, self.ticket_id = cog, ticket_id
        self.add_item(TicketAction(cog, ticket_id, 'claim', '🙋', 'Claim', discord.ButtonStyle.success))
        self.add_item(TicketAction(cog, ticket_id, 'close', '🔒', 'Close', discord.ButtonStyle.secondary))
        self.add_item(TicketAction(cog, ticket_id, 'delete', '🗑️', 'Delete', discord.ButtonStyle.danger))

class TicketAction(discord.ui.Button):
    def __init__(self, cog, ticket_id, action, emoji, label, style):
        super().__init__(label=label, emoji=emoji, style=style, custom_id=f"ader:ticket:{action}:{ticket_id}")
        self.cog, self.ticket_id, self.action = cog, ticket_id, action
    async def callback(self, interaction):
        await self.cog.ticket_action(interaction, self.ticket_id, self.action)

class TicketManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    async def cog_load(self):
        await ensure_tables(self.bot.db)
        rows = await self.bot.db.fetchall('SELECT * FROM ticket_panels')
        for p in rows:
            data = dict(p)
            self.bot.add_view(PanelView(self, [data]))
        rows = await self.bot.db.fetchall("SELECT id FROM tickets WHERE status IN ('open','closed')")
        for r in rows:
            self.bot.add_view(TicketView(self, r['id']))

    async def panel(self, guild_id, panel_id):
        r = await self.bot.db.fetchone('SELECT * FROM ticket_panels WHERE guild_id=? AND id=?', (guild_id, panel_id))
        if not r: return None
        p = dict(r)
        p['staff_roles'] = json.loads(p['staff_roles'] or '[]')
        p['mention_roles'] = json.loads(p['mention_roles'] or '[]')
        return p

    async def panels(self, guild_id):
        rows = await self.bot.db.fetchall('SELECT * FROM ticket_panels WHERE guild_id=? ORDER BY id', (guild_id,))
        out=[]
        for r in rows:
            p=dict(r); p['staff_roles']=json.loads(p['staff_roles'] or '[]'); p['mention_roles']=json.loads(p['mention_roles'] or '[]'); out.append(p)
        return out

    @app_commands.command(name='ticket-panels', description='Manage dynamic independent ticket panels')
    @app_commands.checks.has_permissions(administrator=True)
    async def ticket_panels(self, interaction: discord.Interaction):
        panels = await self.panels(interaction.guild.id)
        desc = '\n'.join(f"`#{p['id']}` **{p['name']}** — {len(p['mention_roles'])} mention roles" for p in panels) or 'No panels yet.'
        await interaction.response.send_message(embed=discord.Embed(title='🎫 Ticket Panels', description=desc), view=PanelAdmin(self), ephemeral=True)

    @app_commands.command(name='ticket-panel-create', description='Create an independent ticket panel')
    @app_commands.checks.has_permissions(administrator=True)
    async def create_panel(self, interaction: discord.Interaction, name: str, category: discord.CategoryChannel):
        try:
            cur=await self.bot.db.execute('INSERT INTO ticket_panels(guild_id,name,category_id,created_at) VALUES(?,?,?,?)',(interaction.guild.id,name,category.id,time.time()))
        except Exception:
            return await interaction.response.send_message('❌ A panel with this name already exists.', ephemeral=True)
        await interaction.response.send_message(f'✅ Created Panel `{cur.lastrowid}`: **{name}**. Use `/ticket-panels` to configure it.', ephemeral=True)

    @app_commands.command(name='ticket-panel-send', description='Send a configured ticket panel')
    @app_commands.checks.has_permissions(administrator=True)
    async def send_panel(self, interaction: discord.Interaction, panel_id: int):
        p=await self.panel(interaction.guild.id,panel_id)
        if not p:return await interaction.response.send_message('❌ Panel not found.',ephemeral=True)
        e=discord.Embed(title=p['name'],description=p['description'] or 'Choose the ticket type below.')
        if p['image_url']: e.set_image(url=p['image_url'])
        msg=await interaction.channel.send(embed=e,view=PanelView(self,[p]))
        await self.bot.db.execute('INSERT INTO ticket_panel_messages(guild_id,panel_id,channel_id,message_id) VALUES(?,?,?,?)',(interaction.guild.id,p['id'],interaction.channel.id,msg.id))
        await interaction.response.send_message('✅ Panel sent.',ephemeral=True)

    @app_commands.command(name='ticket-panel-mention-roles', description='Set the roles mentioned when this panel opens')
    @app_commands.checks.has_permissions(administrator=True)
    async def set_mentions(self, interaction: discord.Interaction, panel_id: int, roles: str):
        p=await self.panel(interaction.guild.id,panel_id)
        if not p:return await interaction.response.send_message('❌ Panel not found.',ephemeral=True)
        ids=[int(x) for x in re.findall(r'<@&([0-9]+)>', roles)]
        ids=[x for x in ids if interaction.guild.get_role(x)]
        await self.bot.db.execute('UPDATE ticket_panels SET mention_roles=? WHERE guild_id=? AND id=?',(json.dumps(ids),interaction.guild.id,panel_id))
        await interaction.response.send_message(f'✅ Panel `{panel_id}` mention roles updated: {len(ids)}.',ephemeral=True)

    async def open_panel_ticket(self, interaction, panel_id):
        p=await self.panel(interaction.guild.id,panel_id)
        if not p:return await interaction.response.send_message('❌ This panel no longer exists.',ephemeral=True)
        row=await self.bot.db.fetchone("SELECT COUNT(*) AS n FROM tickets WHERE guild_id=? AND user_id=? AND status='open' AND data LIKE ?",(interaction.guild.id,interaction.user.id,f'%"panel_id": {panel_id}%'))
        if row and row['n'] >= p['max_open']:
            return await interaction.response.send_message('❌ You already have the maximum number of open tickets for this panel.',ephemeral=True)
        category=interaction.guild.get_channel(p['category_id']) if p['category_id'] else None
        if not isinstance(category, discord.CategoryChannel):
            category=discord.utils.get(interaction.guild.categories,name='Tickets') or await interaction.guild.create_category('Tickets')
        overwrites={interaction.guild.default_role:discord.PermissionOverwrite(view_channel=False),interaction.user:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True),interaction.guild.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,manage_channels=True)}
        for rid in p['staff_roles']:
            role=interaction.guild.get_role(rid)
            if role: overwrites[role]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
        channel=await interaction.guild.create_text_channel(f"ticket-{interaction.user.name}"[:100],category=category,overwrites=overwrites)
        data={'panel_id':panel_id,'panel_name':p['name']}
        tid=await self.bot.db.create_ticket({'guild_id':interaction.guild.id,'channel_id':channel.id,'user_id':interaction.user.id,'status':'open','data':data})
        mentions=' '.join(f'<@&{rid}>' for rid in p['mention_roles'])
        allowed=discord.AllowedMentions(roles=True,users=True)
        text=(mentions+'\n' if mentions else '')+f'{interaction.user.mention} 🎫 Your ticket has been created.\nPanel: **{p["name"]}**'
        await channel.send(text,embed=discord.Embed(title='🎫 Ticket',description=p['description'] or 'Please wait for staff.'),view=TicketView(self,tid),allowed_mentions=allowed)
        await interaction.response.send_message(f'✅ Created {channel.mention}',ephemeral=True)

    async def ticket_action(self, interaction, ticket_id, action):
        row=await self.bot.db.fetchone('SELECT * FROM tickets WHERE id=?',(ticket_id,))
        if not row:return await interaction.response.send_message('❌ Ticket not found.',ephemeral=True)
        is_staff=interaction.user.guild_permissions.manage_channels or interaction.user.id==interaction.guild.owner_id
        if action=='claim':
            if row['user_id']==interaction.user.id:return await interaction.response.send_message('❌ The ticket owner cannot claim their own ticket.',ephemeral=True)
            if not is_staff:return await interaction.response.send_message('❌ Staff only.',ephemeral=True)
            await self.bot.db.update_ticket(str(ticket_id),{'claimed_by':interaction.user.id}); return await interaction.response.send_message(f'🙋 Claimed by {interaction.user.mention}')
        if not is_staff:return await interaction.response.send_message('❌ Staff only.',ephemeral=True)
        if action=='close':
            await self.bot.db.update_ticket(str(ticket_id),{'status':'closed','closed_at':time.time()})
            await interaction.channel.set_permissions(interaction.guild.default_role,view_channel=False)
            return await interaction.response.send_message('🔒 Ticket closed.')
        if action=='delete':
            await interaction.response.send_message('🗑️ Deleting ticket...'); await asyncio.sleep(1); await interaction.channel.delete(reason='Ader ticket delete')

class PanelAdmin(discord.ui.View):
    def __init__(self,cog):
        super().__init__(timeout=180); self.cog=cog
        self.add_item(discord.ui.Button(label='Use /ticket-panel-create',style=discord.ButtonStyle.secondary,disabled=True))

async def setup(bot):
    await bot.add_cog(TicketManager(bot))
