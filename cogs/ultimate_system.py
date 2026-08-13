"""Ader Ultimate: security, moderation, tickets, community and utility systems."""
from __future__ import annotations
import asyncio, json, random, re, time
from collections import defaultdict, deque
from pathlib import Path
import discord
from discord import app_commands
from discord.ext import commands, tasks

URL_RE=re.compile(r'https?://\S+',re.I)
INVITE_RE=re.compile(r'(discord\.gg/|discord\.com/invite/)',re.I)

class TicketView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None); self.cog=cog
    @discord.ui.button(label='Open Ticket',style=discord.ButtonStyle.primary,emoji='🎫',custom_id='ader:ticket:open')
    async def open_ticket(self,interaction:discord.Interaction,button:discord.ui.Button):
        await self.cog.open_ticket(interaction)

class TicketControls(discord.ui.View):
    def __init__(self,cog):
        super().__init__(timeout=None); self.cog=cog
    @discord.ui.button(label='Claim',style=discord.ButtonStyle.success,emoji='🙋',custom_id='ader:ticket:claim')
    async def claim(self,interaction:discord.Interaction,button:discord.ui.Button): await self.cog.claim_ticket(interaction)
    @discord.ui.button(label='Close',style=discord.ButtonStyle.secondary,emoji='🔒',custom_id='ader:ticket:close')
    async def close(self,interaction:discord.Interaction,button:discord.ui.Button): await self.cog.close_ticket(interaction)
    @discord.ui.button(label='Delete',style=discord.ButtonStyle.danger,emoji='🗑️',custom_id='ader:ticket:delete')
    async def delete(self,interaction:discord.Interaction,button:discord.ui.Button): await self.cog.delete_ticket(interaction)

class UltimateSystem(commands.Cog):
    def __init__(self,bot):
        self.bot=bot
        self.spam=defaultdict(deque); self.mod_actions=defaultdict(deque)
        self.xp_cooldown={}; self.reminder_loop.start()
    def cog_unload(self): self.reminder_loop.cancel()
    async def ensure(self,guild_id): await self.bot.db.create_guild(guild_id); return await self.bot.db.get_guild(guild_id)
    async def log(self,guild,kind,**data):
        try: await self.bot.db.log_event(kind,{'guild_id':guild.id,**data})
        except Exception: pass
    async def send_log(self,guild,title,description):
        cfg=await self.ensure(guild.id); channel_id=(cfg.get('modules') or {}).get('log_channel')
        if channel_id:
            ch=guild.get_channel(int(channel_id))
            if ch:
                try: await ch.send(embed=discord.Embed(title=title,description=description,timestamp=discord.utils.utcnow()))
                except discord.HTTPException: pass
    async def audit_guard(self,guild,user_id,action,limit=5,window=10):
        now=time.time(); q=self.mod_actions[(guild.id,user_id,action)]
        while q and now-q[0]>window:q.popleft()
        q.append(now)
        if len(q)>limit:
            member=guild.get_member(user_id)
            if member and guild.me and member.top_role<guild.me.top_role:
                try: await member.timeout(discord.utils.utcnow()+discord.timedelta(minutes=10),reason='Ader Anti-Nuke')
                except Exception: pass
            await self.send_log(guild,'🚨 Anti-Nuke',f'{member.mention if member else user_id} exceeded `{action}` threshold.')
            return True
        return False

    @app_commands.command(name='setup',description='Initialize Ader systems for this server')
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(self,interaction):
        await self.ensure(interaction.guild.id)
        await interaction.response.send_message('✅ Ader Ultimate database and server configuration initialized.',ephemeral=True)

    @app_commands.command(name='set-log-channel',description='Set the moderation/event log channel')
    @app_commands.checks.has_permissions(administrator=True)
    async def set_log(self,interaction,channel:discord.TextChannel):
        cfg=await self.ensure(interaction.guild.id); modules=cfg.get('modules') or {}; modules['log_channel']=channel.id
        await self.bot.db.update_guild(interaction.guild.id,{'log_channel':channel.id}); await interaction.response.send_message(f'✅ Logs: {channel.mention}',ephemeral=True)

    @app_commands.command(name='warn',description='Warn a member')
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(self,interaction,member:discord.Member,reason:str='No reason provided'):
        await self.bot.db.create_user(member.id,interaction.guild.id); await self.bot.db.add_warning(member.id,interaction.guild.id,{'moderator_id':interaction.user.id,'reason':reason})
        await self.log(interaction.guild,'warn',user_id=member.id,moderator_id=interaction.user.id,reason=reason)
        await interaction.response.send_message(f'⚠️ {member.mention} warned: **{reason}**')

    @app_commands.command(name='warnings',description='View active warnings')
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warnings(self,interaction,member:discord.Member):
        rows=await self.bot.db.get_warnings(member.id,interaction.guild.id)
        text='\n'.join(f'`#{r["id"]}` <@{r["moderator_id"]}> — {r["reason"]}' for r in rows[:15]) or 'No active warnings.'
        await interaction.response.send_message(embed=discord.Embed(title=f'Warnings • {member}',description=text),ephemeral=True)

    @app_commands.command(name='clear-warnings',description='Clear a member warnings')
    @app_commands.checks.has_permissions(administrator=True)
    async def clear_warnings(self,interaction,member:discord.Member):
        await self.bot.db.execute('UPDATE warnings SET active=0 WHERE guild_id=? AND user_id=?',(interaction.guild.id,member.id))
        await self.bot.db.update_user(member.id,interaction.guild.id,{'warnings':[]})
        await interaction.response.send_message(f'✅ Cleared warnings for {member.mention}.',ephemeral=True)

    @app_commands.command(name='ban',description='Ban a member')
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(self,interaction,member:discord.Member,reason:str='No reason provided'):
        if member==interaction.user or member.top_role>=interaction.user.top_role: return await interaction.response.send_message('❌ You cannot ban that member.',ephemeral=True)
        await member.ban(reason=reason); await self.log(interaction.guild,'ban',user_id=member.id,moderator_id=interaction.user.id,reason=reason)
        await interaction.response.send_message(f'🔨 Banned {member}.')

    @app_commands.command(name='kick',description='Kick a member')
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self,interaction,member:discord.Member,reason:str='No reason provided'):
        if member.top_role>=interaction.user.top_role:return await interaction.response.send_message('❌ You cannot kick that member.',ephemeral=True)
        await member.kick(reason=reason); await self.log(interaction.guild,'kick',user_id=member.id,moderator_id=interaction.user.id,reason=reason); await interaction.response.send_message(f'👢 Kicked {member}.')

    @app_commands.command(name='timeout',description='Timeout a member')
    @app_commands.checks.has_permissions(moderate_members=True)
    async def timeout(self,interaction,member:discord.Member,minutes:int=10,reason:str='No reason provided'):
        minutes=max(1,min(minutes,40320)); await member.timeout(discord.utils.utcnow()+discord.timedelta(minutes=minutes),reason=reason); await interaction.response.send_message(f'⏱️ Timed out {member.mention} for {minutes} minutes.')

    @app_commands.command(name='purge',description='Delete recent messages')
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge(self,interaction,amount:int):
        amount=max(1,min(amount,100)); await interaction.response.defer(ephemeral=True); deleted=await interaction.channel.purge(limit=amount); await interaction.followup.send(f'🧹 Deleted {len(deleted)} messages.',ephemeral=True)

    @app_commands.command(name='lock',description='Lock the current channel')
    @app_commands.checks.has_permissions(manage_channels=True)
    async def lock(self,interaction):
        await interaction.channel.set_permissions(interaction.guild.default_role,send_messages=False); await interaction.response.send_message('🔒 Channel locked.')

    @app_commands.command(name='unlock',description='Unlock the current channel')
    @app_commands.checks.has_permissions(manage_channels=True)
    async def unlock(self,interaction):
        await interaction.channel.set_permissions(interaction.guild.default_role,send_messages=None); await interaction.response.send_message('🔓 Channel unlocked.')

    @app_commands.command(name='slowmode',description='Set channel slowmode')
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slowmode(self,interaction,seconds:int):
        seconds=max(0,min(seconds,21600)); await interaction.channel.edit(slowmode_delay=seconds); await interaction.response.send_message(f'🐢 Slowmode: {seconds}s.')

    @app_commands.command(name='ticket-panel',description='Send the ticket panel')
    @app_commands.checks.has_permissions(administrator=True)
    async def ticket_panel(self,interaction):
        embed=discord.Embed(title='🎫 Support Tickets',description='Click **Open Ticket** to create a private support channel.')
        await interaction.channel.send(embed=embed,view=TicketView(self)); await interaction.response.send_message('✅ Ticket panel sent.',ephemeral=True)

    async def open_ticket(self,interaction):
        guild=interaction.guild
        existing=await self.bot.db.fetchone('SELECT * FROM tickets WHERE guild_id=? AND user_id=? AND status="open"',(guild.id,interaction.user.id))
        if existing:return await interaction.response.send_message(f'❌ You already have an open ticket: <#{existing["channel_id"]}>',ephemeral=True)
        category=discord.utils.get(guild.categories,name='Tickets') or await guild.create_category('Tickets',reason='Ader ticket system')
        overwrites={guild.default_role:discord.PermissionOverwrite(view_channel=False),interaction.user:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True),guild.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,manage_channels=True)}
        channel=await guild.create_text_channel(f'ticket-{interaction.user.name}',category=category,overwrites=overwrites)
        tid=await self.bot.db.create_ticket({'guild_id':guild.id,'channel_id':channel.id,'user_id':interaction.user.id,'status':'open'})
        await channel.send(f'{interaction.user.mention} 🎫 Your ticket has been created.\nTicket ID: `{tid}`',view=TicketControls(self)); await interaction.response.send_message(f'✅ Created {channel.mention}',ephemeral=True)

    async def get_channel_ticket(self,interaction): return await self.bot.db.fetchone('SELECT * FROM tickets WHERE channel_id=? AND status IN ("open","closed")',(interaction.channel.id,))
    async def claim_ticket(self,interaction):
        row=await self.get_channel_ticket(interaction)
        if not row:return await interaction.response.send_message('❌ This is not a ticket.',ephemeral=True)
        if row['user_id']==interaction.user.id:return await interaction.response.send_message('❌ Ticket owner cannot claim their own ticket.',ephemeral=True)
        await self.bot.db.update_ticket(str(row['id']),{'claimed_by':interaction.user.id}); await interaction.response.send_message(f'🙋 Claimed by {interaction.user.mention}.')
    async def close_ticket(self,interaction):
        row=await self.get_channel_ticket(interaction)
        if not row:return await interaction.response.send_message('❌ Not a ticket.',ephemeral=True)
        await self.bot.db.update_ticket(str(row['id']),{'status':'closed','closed_at':time.time()}); await interaction.channel.set_permissions(interaction.guild.default_role,view_channel=False); await interaction.response.send_message('🔒 Ticket closed. Staff can delete it when finished.')
    async def delete_ticket(self,interaction):
        if not interaction.user.guild_permissions.manage_channels:return await interaction.response.send_message('❌ Staff only.',ephemeral=True)
        await interaction.response.send_message('🗑️ Deleting ticket...'); await asyncio.sleep(1); await interaction.channel.delete(reason='Ader ticket delete')

    @app_commands.command(name='suggest',description='Submit a server suggestion')
    async def suggest(self,interaction,text:str):
        row=await self.bot.db.fetchone('SELECT id FROM suggestions WHERE guild_id=? ORDER BY id DESC LIMIT 1',(interaction.guild.id,)); sid=(row['id']+1 if row else 1)
        await self.bot.db.execute('INSERT INTO suggestions(guild_id,user_id,content,created_at) VALUES(?,?,?,?)',(interaction.guild.id,interaction.user.id,text,time.time()))
        embed=discord.Embed(title=f'💡 Suggestion #{sid}',description=text); embed.set_footer(text=f'By {interaction.user}')
        msg=await interaction.channel.send(embed=embed); await msg.add_reaction('👍'); await msg.add_reaction('👎'); await self.bot.db.execute('UPDATE suggestions SET channel_id=?,message_id=? WHERE id=?',(interaction.channel.id,msg.id,sid)); await interaction.response.send_message(f'✅ Suggestion #{sid} submitted.',ephemeral=True)

    @app_commands.command(name='poll',description='Create a yes/no poll')
    @app_commands.checks.has_permissions(manage_messages=True)
    async def poll(self,interaction,question:str):
        embed=discord.Embed(title='📊 Poll',description=question); msg=await interaction.channel.send(embed=embed); await msg.add_reaction('👍'); await msg.add_reaction('👎'); await interaction.response.send_message('✅ Poll created.',ephemeral=True)

    @app_commands.command(name='serverinfo',description='Show server information')
    async def serverinfo(self,interaction):
        g=interaction.guild; embed=discord.Embed(title=g.name); embed.add_field(name='Members',value=str(g.member_count)); embed.add_field(name='Channels',value=str(len(g.channels))); embed.add_field(name='Roles',value=str(len(g.roles))); embed.add_field(name='Created',value=discord.utils.format_dt(g.created_at,'R')); await interaction.response.send_message(embed=embed)

    @app_commands.command(name='balance',description='Show your balance')
    async def balance(self,interaction,member:discord.Member=None):
        member=member or interaction.user; u=await self.bot.db.create_user(member.id,interaction.guild.id); await interaction.response.send_message(f'💎 {member.mention}: **{u["balance"]}**')

    @app_commands.command(name='daily',description='Claim your daily reward')
    async def daily(self,interaction):
        key=f'daily:{interaction.guild.id}:{interaction.user.id}'; row=await self.bot.db.fetchone('SELECT value FROM settings WHERE guild_id=? AND key=?',(interaction.guild.id,key)); now=time.time()
        if row and now-float(row['value'])<86400:return await interaction.response.send_message('⏳ You already claimed your daily reward.',ephemeral=True)
        await self.bot.db.create_user(interaction.user.id,interaction.guild.id); await self.bot.db.add_balance(interaction.user.id,interaction.guild.id,100); await self.bot.db.execute('INSERT OR REPLACE INTO settings(guild_id,key,value) VALUES(?,?,?)',(interaction.guild.id,key,str(now))); await interaction.response.send_message('🎁 Daily reward: **+100** 💎')

    @app_commands.command(name='remind',description='Create a reminder in seconds')
    async def remind(self,interaction,seconds:int,text:str):
        seconds=max(5,min(seconds,2592000)); await self.bot.db.create_reminder({'user_id':interaction.user.id,'guild_id':interaction.guild.id,'channel_id':interaction.channel.id,'remind_at':time.time()+seconds,'text':text}); await interaction.response.send_message(f'⏰ Reminder set for {seconds} seconds.',ephemeral=True)

    @tasks.loop(seconds=5)
    async def reminder_loop(self):
        if not self.bot.db.is_connected:return
        for r in await self.bot.db.get_due_reminders(time.time()):
            ch=self.bot.get_channel(r['channel_id'])
            if ch:
                data=json.loads(r.get('data') or '{}'); await ch.send(f'⏰ <@{r["user_id"]}> reminder: **{data.get("text","Reminder")}**')
            await self.bot.db.complete_reminder(str(r['id']))

    @reminder_loop.before_loop
    async def before_reminder(self): await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_member_join(self,member):
        await self.bot.db.create_user(member.id,member.guild.id)
        cfg=await self.ensure(member.guild.id); mods=cfg.get('modules') or {}; welcome=mods.get('welcome_channel'); role=mods.get('autorole')
        if role:
            r=member.guild.get_role(int(role))
            if r:
                try: await member.add_roles(r,reason='Ader Autorole')
                except discord.HTTPException: pass
        if welcome:
            ch=member.guild.get_channel(int(welcome))
            if ch:
                try: await ch.send(f'👋 Welcome {member.mention} to **{member.guild.name}**!')
                except discord.HTTPException: pass
        await self.log(member.guild,'member_join',user_id=member.id)

    @commands.Cog.listener()
    async def on_member_remove(self,member): await self.log(member.guild,'member_leave',user_id=member.id)

    @commands.Cog.listener()
    async def on_message(self,message):
        if message.author.bot or not message.guild:return
        now=time.time(); key=(message.guild.id,message.author.id); q=self.spam[key]
        while q and now-q[0]>8:q.popleft()
        q.append(now)
        if len(q)>=7:
            try: await message.author.timeout(discord.utils.utcnow()+discord.timedelta(minutes=2),reason='Ader Anti-Spam')
            except Exception: pass
            q.clear(); await self.log(message.guild,'anti_spam',user_id=message.author.id)
        mentions=len(message.mentions)
        if mentions>5 and message.author.guild_permissions.manage_messages is False:
            try: await message.delete(); await message.channel.send(f'⚠️ {message.author.mention} too many mentions.',delete_after=5)
            except discord.HTTPException: pass
        if INVITE_RE.search(message.content) and not message.author.guild_permissions.manage_messages:
            try: await message.delete(); await message.channel.send(f'🚫 {message.author.mention} invite links are blocked.',delete_after=5)
            except discord.HTTPException: pass
        # lightweight member leveling
        last=self.xp_cooldown.get(key,0)
        if now-last>=60:
            self.xp_cooldown[key]=now; user=await self.bot.db.create_user(message.author.id,message.guild.id); xp=user['xp']+10; level=max(0,int((xp//100))); await self.bot.db.update_user(message.author.id,message.guild.id,{'xp':xp,'level':level})
        await self.bot.process_commands(message)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self,channel):
        entry=getattr(channel,'guild',None)
        if entry:
            try:
                async for audit in entry.audit_logs(limit=1,action=discord.AuditLogAction.channel_delete):
                    if await self.audit_guard(entry,audit.user.id,'channel_delete',3,15): break
            except Exception: pass

    @commands.Cog.listener()
    async def on_guild_role_delete(self,role):
        try:
            async for audit in role.guild.audit_logs(limit=1,action=discord.AuditLogAction.role_delete):
                if await self.audit_guard(role.guild,audit.user.id,'role_delete',3,15): break
        except Exception: pass

async def setup(bot):
    await bot.add_cog(UltimateSystem(bot))
