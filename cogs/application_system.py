from __future__ import annotations
import asyncio, json, time
import discord
from discord import app_commands
from discord.ext import commands

SET2='<:set2:1521929996787257556>'
GGG='<:ggg:1519567521857015928>'
DEFAULT=[
 {'label':'ما اسمك','required':True,'paragraph':False},
 {'label':'كم عمرك','required':True,'paragraph':False},
 {'label':'كم ساعة ناشط باليوم','required':True,'paragraph':False},
 {'label':'كيف ستفيد السيرفر','required':True,'paragraph':False},
 {'label':'اكتب خبراتك في الديسكورد','required':True,'paragraph':True},
]
def panel(n): return {'title':f'تقديم {n}','questions':[dict(x) for x in DEFAULT],'button_label':f'تقديم {n}','button_emoji':None,'button_style':'primary','image':None,'results':None,'accept_role':None,'reject_role':None}

def style(v): return getattr(discord.ButtonStyle,v,discord.ButtonStyle.primary)

class App(commands.Cog):
 def __init__(self,bot): self.bot=bot
 async def cog_load(self):
  await self.bot.db.execute('CREATE TABLE IF NOT EXISTS applications (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER,user_id INTEGER,panel INTEGER,status TEXT,answers TEXT,created_at REAL,reviewer_id INTEGER,reason TEXT)')
 async def get(self,g,n):
  r=await self.bot.db.fetchone('SELECT value FROM settings WHERE guild_id=? AND key=?',(g,f'app_panel_{n}'))
  if r:
   try:return json.loads(r['value'])
   except:pass
  return panel(n)
 async def put(self,g,n,p): await self.bot.db.execute('INSERT OR REPLACE INTO settings(guild_id,key,value) VALUES(?,?,?)',(g,f'app_panel_{n}',json.dumps(p,ensure_ascii=False)))
 async def edit(self,i,n): await i.response.edit_message(embed=discord.Embed(description=f'** اعدادات تقديم {n} {SET2} من هنا **'),view=Settings(self,n))

 @app_commands.command(name='تقديم',description='إعداد تقديمات الإدارة')
 @app_commands.describe(اخفاء='إخفاء رسالة الإعدادات')
 @app_commands.checks.has_permissions(administrator=True)
 async def تقديم(self,i:discord.Interaction,اخفاء:bool=False):
  await i.response.send_message(embed=discord.Embed(description=f'** اختر التقديم الذي تود التعديل عليه {GGG}**'),view=Pick(self),ephemeral=اخفاء)

 async def config(self,i,n,a):
  p=await self.get(i.guild.id,n)
  if a=='title': return await i.response.send_modal(Title(self,n,p['title']))
  if a=='questions': return await i.response.edit_message(embed=discord.Embed(description=f'** اعدادات تقديم {n} {SET2} من هنا **'),view=Questions(self,n))
  if a=='button': return await i.response.edit_message(embed=discord.Embed(description=f'** اعدادات تقديم {n} {SET2} من هنا **'),view=Buttons(self,n))
  if a=='emoji': return await i.response.send_modal(Emoji(self,n,p.get('button_emoji')))
  if a=='name': return await i.response.send_modal(Name(self,n,p['button_label']))
  if a=='color': return await i.response.edit_message(embed=discord.Embed(description='اختر لون الزر'),view=Colors(self,n))
  if a=='image':
   await i.response.send_message('أرسل الصورة هنا خلال **5 دقائق**.',ephemeral=True)
   try:
    m=await self.bot.wait_for('message',timeout=300,check=lambda m:m.author.id==i.user.id and m.channel.id==i.channel.id and bool(m.attachments))
    a=m.attachments[0]
    if not (a.content_type or '').startswith('image/'): return await i.followup.send('❌ الملف ليس صورة.',ephemeral=True)
    p['image']=a.url; await self.put(i.guild.id,n,p); return await i.followup.send('**تم تحديد صورة لي التقديم**',ephemeral=True)
   except asyncio.TimeoutError:return await i.followup.send('⌛ انتهت مهلة 5 دقائق.',ephemeral=True)
  if a=='results': return await i.response.edit_message(embed=discord.Embed(description='اختر الروم الذي ستصل إليه نتائج التقديم'),view=Result(self,n))
  if a=='roles': return await i.response.edit_message(embed=discord.Embed(description=f'** اعدادات تقديم {n} {SET2} من هنا **'),view=Roles(self,n))
  if a=='back': return await i.response.edit_message(embed=discord.Embed(description=f'** اختر التقديم الذي تود التعديل عليه {GGG}**'),view=Pick(self))
  if a=='send': return await i.response.edit_message(embed=discord.Embed(description='**اختر الروم لي حاب تشوف سجل التقديمات فيه**'),view=Send(self,n))
  if a=='remove_accept':p['accept_role']=None
  if a=='remove_reject':p['reject_role']=None
  await self.put(i.guild.id,n,p); await self.edit(i,n)

 async def questions(self,i,n):
  p=await self.get(i.guild.id,n); await i.response.edit_message(embed=discord.Embed(description=f'** اعدادات تقديم {n} {SET2} من هنا **'),view=Questions(self,n,len(p['questions'])))
 async def submit(self,i,n,p,vals):
  row=await self.bot.db.fetchone('SELECT COALESCE(MAX(id),0)+1 AS id FROM applications'); aid=row['id']
  await self.bot.db.execute('INSERT INTO applications(id,guild_id,user_id,panel,status,answers,created_at) VALUES(?,?,?,?,?,?,?)',(aid,i.guild.id,i.user.id,n,'pending',json.dumps(vals,ensure_ascii=False),time.time()))
  ch=i.guild.get_channel(p.get('results') or 0)
  if ch:
   e=discord.Embed(title=f'تقديم {n}',description=f'المتقدم: {i.user.mention}\nالحالة: **قيد المراجعة**')
   for q,a in zip(p['questions'],vals):e.add_field(name=q['label'],value=a[:1024] or '—',inline=False)
   await ch.send(embed=e,view=Review(self,aid))
  await i.response.send_message('✅ تم إرسال التقديم بنجاح.',ephemeral=True)
 async def review(self,i,aid,ok,reason=None):
  r=await self.bot.db.fetchone('SELECT * FROM applications WHERE id=?',(aid,))
  if not r or r['status']!='pending':return await i.response.send_message('❌ هذا التقديم تمت مراجعته.',ephemeral=True)
  p=await self.get(i.guild.id,r['panel']); status='accepted' if ok else 'rejected'
  await self.bot.db.execute('UPDATE applications SET status=?,reviewer_id=?,reason=? WHERE id=?',(status,i.user.id,reason,aid))
  rid=p.get('accept_role') if ok else p.get('reject_role'); member=i.guild.get_member(r['user_id'])
  if member and rid:
   role=i.guild.get_role(rid)
   if role:
    try: await member.add_roles(role,reason='Application review')
    except discord.HTTPException: pass
  text='تم قبول التقديم' if ok else f'تم رفض التقديم\nالسبب: {reason or "بدون سبب"}'
  try:await i.message.edit(embed=discord.Embed(title=f'تقديم {r["panel"]}',description=f'{text}\nبواسطة: {i.user.mention}'),view=None)
  except discord.HTTPException:pass
  await i.response.send_message(f'✅ {text}',ephemeral=True)

class Pick(discord.ui.View):
 def __init__(self,c):
  super().__init__(timeout=600)
  for n in range(1,4):self.add_item(B(c,n))
class B(discord.ui.Button):
 def __init__(self,c,n):super().__init__(label=f'تقديم {n}',style=discord.ButtonStyle.primary,emoji=SET2,custom_id=f'ader:app:pick:{n}');self.c,self.n=c,n
 async def callback(self,i):await self.c.edit(i,self.n)
class Settings(discord.ui.View):
 def __init__(self,c,n):
  super().__init__(timeout=600);self.c,self.n=c,n
  for label,a in [('تعديل عنوان Panel','title'),('تعديل الأسئلة','questions'),('تعديل الزر','button'),('تحديد صورة لي التقديم','image'),('تحديد مكان نتائج التقديم','results'),('إعدادات رتب التقديم','roles')]:self.add_item(C(c,n,label,a))
  self.add_item(C(c,n,'رجوع','back',discord.ButtonStyle.secondary));self.add_item(C(c,n,'إرسال','send',discord.ButtonStyle.success))
class C(discord.ui.Button):
 def __init__(self,c,n,label,a,st=discord.ButtonStyle.primary):super().__init__(label=label,style=st,custom_id=f'ader:app:{n}:{a}');self.c,self.n,self.a=c,n,a
 async def callback(self,i):await self.c.config(i,self.n,self.a)

class Title(discord.ui.Modal,title='تعديل عنوان Panel'):
 x=discord.ui.TextInput(label='عنوان Panel',max_length=100)
 def __init__(self,c,n,v):super().__init__();self.c,self.n=c,n;self.x.default=v
 async def on_submit(self,i):p=await self.c.get(i.guild.id,self.n);p['title']=str(self.x.value);await self.c.put(i.guild.id,self.n,p);await self.c.edit(i,self.n)
class Name(discord.ui.Modal,title='اسم زر التقديم'):
 x=discord.ui.TextInput(label='اسم الزر',max_length=80)
 def __init__(self,c,n,v):super().__init__();self.c,self.n=c,n;self.x.default=v
 async def on_submit(self,i):p=await self.c.get(i.guild.id,self.n);p['button_label']=str(self.x.value);await self.c.put(i.guild.id,self.n,p);await i.response.edit_message(embed=discord.Embed(description=f'** اعدادات تقديم {self.n} {SET2} من هنا **'),view=Buttons(self.c,self.n))
class Emoji(discord.ui.Modal,title='إيموجي التقديم'):
 x=discord.ui.TextInput(label='Unicode أو <:name:id>',required=False,max_length=100)
 def __init__(self,c,n,v):super().__init__();self.c,self.n=c,n;self.x.default=v or ''
 async def on_submit(self,i):p=await self.c.get(i.guild.id,self.n);p['button_emoji']=str(self.x.value).strip() or None;await self.c.put(i.guild.id,self.n,p);await i.response.edit_message(embed=discord.Embed(description=f'** اعدادات تقديم {self.n} {SET2} من هنا **'),view=Buttons(self.c,self.n))
class Buttons(discord.ui.View):
 def __init__(self,c,n):super().__init__(timeout=300);self.add_item(C(c,n,'اسم زر التقديم','name'));self.add_item(C(c,n,'إيموجي التقديم','emoji'));self.add_item(C(c,n,'ألوان الزر','color'));self.add_item(C(c,n,'رجوع','back',discord.ButtonStyle.secondary))
class Colors(discord.ui.View):
 def __init__(self,c,n):super().__init__(timeout=180)
  # colors are Discord button styles
  
class Questions(discord.ui.View):
 def __init__(self,c,n,count=None):
  super().__init__(timeout=300);self.c,self.n=c,n;self.count=count or 5
  for x in range(self.count):self.add_item(Q(c,n,x))
  self.add_item(C(c,n,'تغيير عدد الأسئلة','count'));self.add_item(C(c,n,'رجوع','back',discord.ButtonStyle.secondary))
class Q(discord.ui.Button):
 def __init__(self,c,n,x):super().__init__(label=f'السؤال {x+1}',style=discord.ButtonStyle.primary,custom_id=f'ader:app:q:{n}:{x}');self.c,self.n,self.x=c,n,x
 async def callback(self,i):
  p=await self.c.get(i.guild.id,self.n);await i.response.send_modal(QModal(self.c,self.n,self.x,p['questions'][self.x]))
class QModal(discord.ui.Modal,title='تعديل السؤال'):
 q=discord.ui.TextInput(label='السؤال',max_length=200);req=discord.ui.TextInput(label='مطلوب؟ نعم/لا',max_length=3);par=discord.ui.TextInput(label='فقرة؟ نعم/لا',max_length=3)
 def __init__(self,c,n,x,v):super().__init__();self.c,self.n,self.x=c,n,x;self.q.default=v['label'];self.req.default='نعم' if v['required'] else 'لا';self.par.default='نعم' if v['paragraph'] else 'لا'
 async def on_submit(self,i):p=await self.c.get(i.guild.id,self.n);p['questions'][self.x]={'label':str(self.q.value),'required':str(self.req.value).lower()=='نعم','paragraph':str(self.par.value).lower()=='نعم'};await self.c.put(i.guild.id,self.n,p);await self.c.questions(i,self.n)
class Result(discord.ui.View):
 def __init__(self,c,n):super().__init__(timeout=180);self.add_item(discord.ui.ChannelSelect(placeholder='اختر روم النتائج',channel_types=[discord.ChannelType.text],custom_id=f'ader:app:res:{n}'))
 async def interaction_check(self,i):return True
class Roles(discord.ui.View):
 def __init__(self,c,n):
  super().__init__(timeout=180);self.c,self.n=c,n;self.add_item(RS(c,n,'accept','اختر رتبة القبول'));self.add_item(RS(c,n,'reject','اختر رتبة الرفض'));self.add_item(C(c,n,'إزالة رتبة القبول','remove_accept',discord.ButtonStyle.danger));self.add_item(C(c,n,'إزالة رتبة الرفض','remove_reject',discord.ButtonStyle.danger));self.add_item(C(c,n,'رجوع','back',discord.ButtonStyle.secondary))
class RS(discord.ui.RoleSelect):
 def __init__(self,c,n,k,p):super().__init__(placeholder=p,min_values=1,max_values=1);self.c,self.n,self.k=c,n,k
 async def callback(self,i):p=await self.c.get(i.guild.id,self.n);p[f'{self.k}_role']=self.values[0].id;await self.c.put(i.guild.id,self.n,p);await self.c.edit(i,self.n)
class Send(discord.ui.View):
 def __init__(self,c,n):super().__init__(timeout=180);self.c,self.n=c,n;self.add_item(discord.ui.ChannelSelect(placeholder='اختر روم نشر التقديم',channel_types=[discord.ChannelType.text]))
 async def on_timeout(self):pass
class Review(discord.ui.View):
 def __init__(self,c,aid):super().__init__(timeout=None);self.c,self.aid=c,aid
 @discord.ui.button(label='قبول',style=discord.ButtonStyle.success)
 async def yes(self,i,b):await self.c.review(i,self.aid,True)
 @discord.ui.button(label='رفض',style=discord.ButtonStyle.danger)
 async def no(self,i,b):await i.response.send_modal(Reject(self.c,self.aid))
class Reject(discord.ui.Modal,title='سبب رفض التقديم'):
 x=discord.ui.TextInput(label='السبب',style=discord.TextStyle.paragraph,max_length=1000)
 def __init__(self,c,aid):super().__init__();self.c,self.aid=c,aid
 async def on_submit(self,i):await self.c.review(i,self.aid,False,str(self.x.value))

async def setup(bot):await bot.add_cog(App(bot))
