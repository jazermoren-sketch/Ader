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

def base(n): return {'title':f'تقديم {n}','questions':[dict(x) for x in DEFAULT],'button_label':f'تقديم {n}','button_emoji':None,'button_style':'primary','image':None,'results':None,'accept_role':None,'reject_role':None}

class App(commands.Cog):
 def __init__(self,bot): self.bot=bot; self.sessions={}
 async def cog_load(self):
  await self.bot.db.execute('CREATE TABLE IF NOT EXISTS applications (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER,user_id INTEGER,panel INTEGER,status TEXT,answers TEXT,created_at REAL,reviewer_id INTEGER,reason TEXT)')
 async def get(self,g,n):
  r=await self.bot.db.fetchone('SELECT value FROM settings WHERE guild_id=? AND key=?',(g,f'app_panel_{n}'))
  if not r:return base(n)
  try:
   p=base(n);p.update(json.loads(r['value']));return p
  except:return base(n)
 async def save(self,g,n,p): await self.bot.db.execute('INSERT OR REPLACE INTO settings(guild_id,key,value) VALUES(?,?,?)',(g,f'app_panel_{n}',json.dumps(p,ensure_ascii=False)))
 async def home(self,i): await i.response.edit_message(embed=discord.Embed(description=f'** اختر التقديم الذي تود التعديل عليه {GGG}**'),view=Pick(self))
 async def settings(self,i,n): await i.response.edit_message(embed=discord.Embed(description=f'** اعدادات تقديم {n} {SET2} من هنا **'),view=Settings(self,n))
 @app_commands.command(name='تقديم',description='إعداد تقديمات الإدارة')
 @app_commands.describe(اخفاء='إخفاء رسالة الإعدادات')
 @app_commands.checks.has_permissions(administrator=True)
 async def تقديم(self,i:discord.Interaction,اخفاء:bool=False): await i.response.send_message(embed=discord.Embed(description=f'** اختر التقديم الذي تود التعديل عليه {GGG}**'),view=Pick(self),ephemeral=اخفاء)
 async def action(self,i,n,a):
  p=await self.get(i.guild.id,n)
  if a=='title': return await i.response.send_modal(EditText(self,n,'title','تعديل عنوان Panel','عنوان Panel',p['title']))
  if a=='button_name': return await i.response.send_modal(EditText(self,n,'button_label','اسم زر التقديم','اسم الزر',p['button_label']))
  if a=='emoji': return await i.response.send_modal(EditText(self,n,'button_emoji','إيموجي التقديم','Unicode أو <:name:id>',p.get('button_emoji') or ''))
  if a=='button': return await i.response.edit_message(embed=discord.Embed(description=f'** اعدادات تقديم {n} {SET2} من هنا **'),view=ButtonSettings(self,n))
  if a=='color': return await i.response.edit_message(embed=discord.Embed(description='اختر لون الزر'),view=Colors(self,n))
  if a=='questions': return await i.response.edit_message(embed=discord.Embed(description=f'** اعدادات تقديم {n} {SET2} من هنا **'),view=Questions(self,n))
  if a=='results': return await i.response.edit_message(embed=discord.Embed(description='اختر الروم الذي ستصل إليه نتائج التقديم'),view=ChannelChoice(self,n,'results'))
  if a=='roles': return await i.response.edit_message(embed=discord.Embed(description=f'** اعدادات تقديم {n} {SET2} من هنا **'),view=Roles(self,n))
  if a=='image':
   await i.response.send_message('أرسل الصورة هنا خلال **5 دقائق**.',ephemeral=True)
   try:
    m=await self.bot.wait_for('message',timeout=300,check=lambda x:x.author.id==i.user.id and x.channel.id==i.channel.id and x.attachments)
    a=m.attachments[0]
    if not (a.content_type or '').startswith('image/'): return await i.followup.send('❌ الملف ليس صورة.',ephemeral=True)
    p['image']=a.url;await self.save(i.guild.id,n,p);await i.followup.send('**تم تحديد صورة لي التقديم**',ephemeral=True)
   except asyncio.TimeoutError: await i.followup.send('⌛ انتهت مهلة 5 دقائق.',ephemeral=True)
   return
  if a=='back': return await self.home(i)
  if a=='send': return await i.response.edit_message(embed=discord.Embed(description='**اختر الروم لي حاب تشوف سجل التقديمات فيه**'),view=ChannelChoice(self,n,'publish'))
  if a in ('remove_accept','remove_reject'):
   p['accept_role' if a=='remove_accept' else 'reject_role']=None;await self.save(i.guild.id,n,p);return await self.settings(i,n)
 async def set_channel(self,i,n,key):
  p=await self.get(i.guild.id,n);p['results']=i.values[0].id if key=='results' else p.get('results');await self.save(i.guild.id,n,p)
  if key=='publish':
   ch=i.guild.get_channel(i.values[0].id);e=discord.Embed(title=p['title'],description='اضغط الزر لبدء التقديم.');
   if p.get('image'):e.set_image(url=p['image'])
   await ch.send(embed=e,view=Published(self,n));return await i.response.send_message('✅ تم إرسال التقديم.',ephemeral=True)
  await self.settings(i,n)
 async def set_role(self,i,n,key):
  p=await self.get(i.guild.id,n);p[key]=i.values[0].id;await self.save(i.guild.id,n,p);await self.settings(i,n)
 async def submit(self,i,n,vals):
  p=await self.get(i.guild.id,n);key=(i.guild.id,i.user.id,n);old=self.sessions.get(key,[])+vals;self.sessions[key]=old
  if len(old)<len(p['questions']):return await i.response.send_modal(Form(self,n,len(old),p['questions']))
  self.sessions.pop(key,None);cur=await self.bot.db.execute('INSERT INTO applications(guild_id,user_id,panel,status,answers,created_at) VALUES(?,?,?,?,?,?)',(i.guild.id,i.user.id,n,'pending',json.dumps(old,ensure_ascii=False),time.time()));aid=cur.lastrowid
  ch=i.guild.get_channel(p.get('results') or 0)
  if ch:
   e=discord.Embed(title=f'تقديم {n}',description=f'المتقدم: {i.user.mention}\nالحالة: **قيد المراجعة**')
   for q,a in zip(p['questions'],old):e.add_field(name=q['label'],value=a[:1024] or '—',inline=False)
   await ch.send(embed=e,view=Review(self,aid))
  await i.response.send_message('✅ تم إرسال التقديم بنجاح.',ephemeral=True)
 async def review(self,i,aid,ok,reason=None):
  r=await self.bot.db.fetchone('SELECT * FROM applications WHERE id=?',(aid,))
  if not r or r['status']!='pending':return await i.response.send_message('❌ تمت مراجعة هذا التقديم.',ephemeral=True)
  p=await self.get(i.guild.id,r['panel']);status='accepted' if ok else 'rejected';await self.bot.db.execute('UPDATE applications SET status=?,reviewer_id=?,reason=? WHERE id=?',(status,i.user.id,reason,aid))
  rid=p['accept_role'] if ok else p['reject_role'];m=i.guild.get_member(r['user_id'])
  if m and rid:
   role=i.guild.get_role(rid)
   if role:
    try:await m.add_roles(role,reason='Ader application review')
    except discord.HTTPException:pass
  await i.response.edit_message(embed=discord.Embed(title=f'تقديم {r["panel"]}',description=f'الحالة: **{status}**\nالمراجع: {i.user.mention}'+(f'\nالسبب: {reason}' if reason else '')),view=None)

class Pick(discord.ui.View):
 def __init__(self,c):super().__init__(timeout=600);[self.add_item(PickBtn(c,n)) for n in range(1,4)]
class PickBtn(discord.ui.Button):
 def __init__(self,c,n):super().__init__(label=f'تقديم {n}',style=discord.ButtonStyle.primary,emoji=SET2);self.c,self.n=c,n
 async def callback(self,i):await self.c.settings(i,self.n)
class Settings(discord.ui.View):
 def __init__(self,c,n):
  super().__init__(timeout=600);self.c,self.n=c,n
  for x,a in [('تعديل عنوان Panel','title'),('تعديل الأسئلة','questions'),('تعديل الزر','button'),('تحديد صورة لي التقديم','image'),('تحديد مكان نتائج التقديم','results'),('إعدادات رتب التقديم','roles')]:self.add_item(Action(c,n,x,a))
  self.add_item(Action(c,n,'رجوع','back',discord.ButtonStyle.secondary));self.add_item(Action(c,n,'إرسال','send',discord.ButtonStyle.success))
class Action(discord.ui.Button):
 def __init__(self,c,n,label,a,style=discord.ButtonStyle.primary):super().__init__(label=label,style=style);self.c,self.n,self.a=c,n,a
 async def callback(self,i):await self.c.action(i,self.n,self.a)
class EditText(discord.ui.Modal):
 def __init__(self,c,n,key,title,label,value):super().__init__(title=title);self.c,self.n,self.key=c,n,key;self.x=discord.ui.TextInput(label=label,default=value,max_length=100);self.add_item(self.x)
 async def on_submit(self,i):p=await self.c.get(i.guild.id,self.n);p[self.key]=str(self.x.value) or None;await self.c.save(i.guild.id,self.n,p);await self.c.settings(i,self.n)
class ButtonSettings(discord.ui.View):
 def __init__(self,c,n):super().__init__(timeout=300);self.add_item(Action(c,n,'اسم زر التقديم','button_name'));self.add_item(Action(c,n,'إيموجي التقديم','emoji'));self.add_item(Action(c,n,'ألوان الزر','color'));self.add_item(Action(c,n,'رجوع','back',discord.ButtonStyle.secondary))
class Colors(discord.ui.View):
 def __init__(self,c,n):
  super().__init__(timeout=180)
  for label,val in [('أزرق','primary'),('أخضر','success'),('رمادي','secondary'),('أحمر','danger')]:self.add_item(Color(c,n,label,val))
class Color(discord.ui.Button):
 def __init__(self,c,n,label,val):super().__init__(label=label,style=getattr(discord.ButtonStyle,val));self.c,self.n,self.val=c,n,val
 async def callback(self,i):p=await self.c.get(i.guild.id,self.n);p['button_style']=self.val;await self.c.save(i.guild.id,self.n,p);await self.c.settings(i,self.n)
class Questions(discord.ui.View):
 def __init__(self,c,n):
  super().__init__(timeout=300);self.c,self.n=c,n;p=None
  for x in range(10):self.add_item(QBtn(c,n,x))
  self.add_item(Action(c,n,'تغيير عدد الأسئلة (5/7/10)','count'));self.add_item(Action(c,n,'رجوع','back',discord.ButtonStyle.secondary))
class QBtn(discord.ui.Button):
 def __init__(self,c,n,x):super().__init__(label=f'السؤال {x+1}',style=discord.ButtonStyle.primary);self.c,self.n,self.x=c,n,x
 async def callback(self,i):
  p=await self.c.get(i.guild.id,self.n)
  if self.x>=len(p['questions']):return await i.response.send_message('❌ هذا السؤال غير مفعل.',ephemeral=True)
  await i.response.send_modal(QModal(self.c,self.n,self.x,p['questions'][self.x]))
class QModal(discord.ui.Modal,title='تعديل السؤال'):
 q=discord.ui.TextInput(label='السؤال',max_length=200);required=discord.ui.TextInput(label='مطلوب؟ نعم/لا',max_length=3);paragraph=discord.ui.TextInput(label='فقرة؟ نعم/لا',max_length=3)
 def __init__(self,c,n,x,q):super().__init__();self.c,self.n,self.x=c,n,x;self.q.default=q['label'];self.required.default='نعم' if q['required'] else 'لا';self.paragraph.default='نعم' if q['paragraph'] else 'لا'
 async def on_submit(self,i):p=await self.c.get(i.guild.id,self.n);p['questions'][self.x]={'label':str(self.q.value),'required':str(self.required.value).lower()=='نعم','paragraph':str(self.paragraph.value).lower()=='نعم'};await self.c.save(i.guild.id,self.n,p);await self.c.settings(i,self.n)
class ChannelChoice(discord.ui.View):
 def __init__(self,c,n,key):super().__init__(timeout=180);self.add_item(Channel(c,n,key))
class Channel(discord.ui.ChannelSelect):
 def __init__(self,c,n,key):super().__init__(placeholder='اختر الروم',channel_types=[discord.ChannelType.text]);self.c,self.n,self.key=c,n,key
 async def callback(self,i):await self.c.set_channel(i,self.n,self.key)
class Roles(discord.ui.View):
 def __init__(self,c,n):
  super().__init__(timeout=180);self.add_item(Role(c,n,'accept_role','اختر رتبة القبول'));self.add_item(Role(c,n,'reject_role','اختر رتبة الرفض'));self.add_item(Action(c,n,'إزالة رتبة القبول','remove_accept',discord.ButtonStyle.danger));self.add_item(Action(c,n,'إزالة رتبة الرفض','remove_reject',discord.ButtonStyle.danger));self.add_item(Action(c,n,'رجوع','back',discord.ButtonStyle.secondary))
class Role(discord.ui.RoleSelect):
 def __init__(self,c,n,key,placeholder):super().__init__(placeholder=placeholder);self.c,self.n,self.key=c,n,key
 async def callback(self,i):await self.c.set_role(i,self.n,self.key)
class Published(discord.ui.View):
 def __init__(self,c,n):super().__init__(timeout=None);self.add_item(Open(c,n))
class Open(discord.ui.Button):
 def __init__(self,c,n):super().__init__(label=f'تقديم {n}',style=discord.ButtonStyle.primary,custom_id=f'ader:app:open:{n}');self.c,self.n=c,n
 async def callback(self,i):p=await self.c.get(i.guild.id,self.n);await i.response.send_modal(Form(self.c,self.n,0,p['questions']))
class Form(discord.ui.Modal):
 def __init__(self,c,n,start,questions):super().__init__(title=f'تقديم {n}');self.c,self.n=c,n
  
  # Discord modals support five inputs; additional questions are collected in subsequent modal pages.
  
class Review(discord.ui.View):
 def __init__(self,c,aid):super().__init__(timeout=None);self.c,self.aid=c,aid
 @discord.ui.button(label='قبول',style=discord.ButtonStyle.success)
 async def accept(self,i,b):await self.c.review(i,self.aid,True)
 @discord.ui.button(label='رفض',style=discord.ButtonStyle.danger)
 async def reject(self,i,b):await i.response.send_modal(Reject(self.c,self.aid))
class Reject(discord.ui.Modal,title='سبب الرفض'):
 reason=discord.ui.TextInput(label='سبب الرفض',style=discord.TextStyle.paragraph,max_length=1000)
 def __init__(self,c,aid):super().__init__();self.c,self.aid=c,aid
 async def on_submit(self,i):await self.c.review(i,self.aid,False,str(self.reason.value))
async def setup(bot):await bot.add_cog(App(bot))
