"""SQLite database manager for Ader Ultimate."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite


class DatabaseManager:
    def __init__(self, path: str = "data/ader.sqlite3", *args, **kwargs):
        self.path = Path(path)
        self.connection: Optional[aiosqlite.Connection] = None
        self._connected = False

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = await aiosqlite.connect(self.path)
        self.connection.row_factory = aiosqlite.Row
        await self.connection.execute("PRAGMA journal_mode=WAL")
        await self.connection.execute("PRAGMA foreign_keys=ON")
        await self._migrate()
        self._connected = True

    async def disconnect(self) -> None:
        if self.connection:
            await self.connection.close()
        self.connection = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def _migrate(self) -> None:
        await self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER NOT NULL, guild_id INTEGER NOT NULL,
            xp INTEGER NOT NULL DEFAULT 0, level INTEGER NOT NULL DEFAULT 0,
            balance INTEGER NOT NULL DEFAULT 1000, inventory TEXT NOT NULL DEFAULT '[]',
            warnings TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL,
            PRIMARY KEY(user_id, guild_id)
        );
        CREATE TABLE IF NOT EXISTS guilds (
            guild_id INTEGER PRIMARY KEY, config TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
            channel_id INTEGER, user_id INTEGER, status TEXT NOT NULL DEFAULT 'open',
            claimed_by INTEGER, created_at REAL NOT NULL, closed_at REAL, data TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS analytics (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, type TEXT NOT NULL,
            timestamp REAL NOT NULL, data TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, guild_id INTEGER,
            channel_id INTEGER, remind_at REAL NOT NULL, completed INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS shop (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
            name TEXT NOT NULL, price INTEGER NOT NULL, data TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            moderator_id INTEGER NOT NULL, reason TEXT NOT NULL, created_at REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS settings (
            guild_id INTEGER PRIMARY KEY, key TEXT NOT NULL, value TEXT
        );
        CREATE TABLE IF NOT EXISTS giveaways (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL, prize TEXT NOT NULL, ends_at REAL NOT NULL, winners INTEGER NOT NULL DEFAULT 1, ended INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER,
            message_id INTEGER, user_id INTEGER NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reminders_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL, remind_at REAL NOT NULL, text TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS custom_commands (
            guild_id INTEGER NOT NULL, name TEXT NOT NULL, response TEXT NOT NULL,
            PRIMARY KEY(guild_id, name)
        );
        CREATE TABLE IF NOT EXISTS anti_nuke (
            guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, action TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0, window_start REAL NOT NULL,
            PRIMARY KEY(guild_id, user_id, action)
        );
        """)
        await self.connection.commit()

    async def execute(self, sql: str, params: tuple = ()):
        cur = await self.connection.execute(sql, params)
        await self.connection.commit()
        return cur

    async def fetchone(self, sql: str, params: tuple = ()):
        cur = await self.connection.execute(sql, params)
        return await cur.fetchone()

    async def fetchall(self, sql: str, params: tuple = ()):
        cur = await self.connection.execute(sql, params)
        return await cur.fetchall()

    async def get_user(self, user_id: int, guild_id: int) -> Optional[Dict[str, Any]]:
        row = await self.fetchone("SELECT * FROM users WHERE user_id=? AND guild_id=?", (user_id, guild_id))
        if not row:
            return None
        d = dict(row)
        d['inventory'] = json.loads(d['inventory'] or '[]')
        d['warnings'] = json.loads(d['warnings'] or '[]')
        return d

    async def create_user(self, user_id: int, guild_id: int, data: Dict[str, Any] = None) -> Dict[str, Any]:
        data = data or {}
        await self.execute("INSERT OR IGNORE INTO users(user_id,guild_id,xp,level,balance,inventory,warnings,created_at) VALUES(?,?,?,?,?,?,?,?)",
                           (user_id,guild_id,data.get('xp',0),data.get('level',0),data.get('balance',1000),json.dumps(data.get('inventory',[])),json.dumps(data.get('warnings',[])),time.time()))
        return await self.get_user(user_id, guild_id)

    async def update_user(self, user_id: int, guild_id: int, data: Dict[str, Any]) -> bool:
        if not data: return False
        sets=[]; vals=[]
        for key,value in data.items():
            if key in ('inventory','warnings'): value=json.dumps(value)
            if key not in {'xp','level','balance','inventory','warnings'}: continue
            sets.append(f"{key}=?"); vals.append(value)
        if not sets: return False
        vals += [user_id,guild_id]
        cur = await self.execute(f"UPDATE users SET {', '.join(sets)} WHERE user_id=? AND guild_id=?", tuple(vals))
        return cur.rowcount > 0

    async def increment_user_field(self, user_id: int, guild_id: int, field: str, amount: int = 1) -> bool:
        if field not in {'xp','level','balance'}: return False
        await self.create_user(user_id,guild_id)
        cur = await self.execute(f"UPDATE users SET {field}={field}+? WHERE user_id=? AND guild_id=?", (amount,user_id,guild_id))
        return cur.rowcount > 0

    async def get_guild(self, guild_id: int) -> Optional[Dict[str, Any]]:
        row=await self.fetchone("SELECT * FROM guilds WHERE guild_id=?",(guild_id,))
        if not row: return None
        d=dict(row); d['modules']=json.loads(d.pop('config','{}') or '{}'); return d

    async def create_guild(self, guild_id: int, data: Dict[str, Any] = None) -> Dict[str, Any]:
        await self.execute("INSERT OR IGNORE INTO guilds(guild_id,config,created_at) VALUES(?,?,?)",(guild_id,json.dumps(data or {}),time.time()))
        return await self.get_guild(guild_id)

    async def update_guild(self, guild_id: int, data: Dict[str, Any]) -> bool:
        await self.create_guild(guild_id)
        current=await self.get_guild(guild_id); cfg=current.get('modules',{}) if current else {}
        cfg.update(data)
        cur=await self.execute("UPDATE guilds SET config=? WHERE guild_id=?",(json.dumps(cfg),guild_id)); return cur.rowcount>0

    async def get_leaderboard(self,guild_id:int,limit:int=10)->List[Dict[str,Any]]:
        rows=await self.fetchall("SELECT * FROM users WHERE guild_id=? ORDER BY xp DESC LIMIT ?",(guild_id,limit)); return [dict(r) for r in rows]

    async def add_balance(self,user_id:int,guild_id:int,amount:int)->bool:
        await self.create_user(user_id,guild_id); return await self.increment_user_field(user_id,guild_id,'balance',amount)

    async def remove_balance(self,user_id:int,guild_id:int,amount:int)->bool:
        u=await self.get_user(user_id,guild_id)
        if not u or u['balance']<amount:return False
        return await self.increment_user_field(user_id,guild_id,'balance',-amount)

    async def add_item(self,user_id:int,guild_id:int,item:Dict[str,Any])->bool:
        u=await self.create_user(user_id,guild_id); inv=u['inventory']; inv.append(item); return await self.update_user(user_id,guild_id,{'inventory':inv})

    async def add_warning(self,user_id:int,guild_id:int,warning:Dict[str,Any])->bool:
        u=await self.create_user(user_id,guild_id); warnings=u['warnings']; warnings.append(warning); await self.update_user(user_id,guild_id,{'warnings':warnings})
        await self.execute("INSERT INTO warnings(guild_id,user_id,moderator_id,reason,created_at) VALUES(?,?,?,?,?)",(guild_id,user_id,warning.get('moderator_id',0),warning.get('reason',''),time.time())); return True

    async def get_warnings(self,user_id:int,guild_id:int)->List[Dict[str,Any]]:
        rows=await self.fetchall("SELECT * FROM warnings WHERE user_id=? AND guild_id=? AND active=1 ORDER BY id DESC",(user_id,guild_id)); return [dict(r) for r in rows]

    async def create_ticket(self,ticket_data:Dict[str,Any])->str:
        cur=await self.execute("INSERT INTO tickets(guild_id,channel_id,user_id,status,claimed_by,created_at,data) VALUES(?,?,?,?,?,?,?)",(ticket_data.get('guild_id'),ticket_data.get('channel_id'),ticket_data.get('user_id'),ticket_data.get('status','open'),ticket_data.get('claimed_by'),time.time(),json.dumps(ticket_data))); return str(cur.lastrowid)

    async def get_ticket(self,ticket_id:str)->Optional[Dict[str,Any]]:
        r=await self.fetchone("SELECT * FROM tickets WHERE id=?",(int(ticket_id),)); return dict(r) if r else None

    async def update_ticket(self,ticket_id:str,data:Dict[str,Any])->bool:
        if not data:return False
        sets=[];vals=[]
        for k,v in data.items():
            if k in {'status','claimed_by','channel_id','user_id','closed_at'}:sets.append(f'{k}=?');vals.append(v)
        if not sets:return False
        vals.append(int(ticket_id)); cur=await self.execute(f"UPDATE tickets SET {','.join(sets)} WHERE id=?",tuple(vals));return cur.rowcount>0

    async def log_event(self,event_type:str,data:Dict[str,Any])->None:
        await self.execute("INSERT INTO analytics(guild_id,type,timestamp,data) VALUES(?,?,?,?)",(data.get('guild_id'),event_type,time.time(),json.dumps(data)))

    async def get_analytics(self,guild_id:int,event_type:Optional[str]=None,start_time:Optional[float]=None,end_time:Optional[float]=None)->List[Dict[str,Any]]:
        q='SELECT * FROM analytics WHERE guild_id=?';p=[guild_id]
        if event_type:q+=' AND type=?';p.append(event_type)
        if start_time:q+=' AND timestamp>=?';p.append(start_time)
        if end_time:q+=' AND timestamp<=?';p.append(end_time)
        q+=' ORDER BY timestamp DESC LIMIT 1000'; rows=await self.fetchall(q,tuple(p)); return [dict(r) for r in rows]

    async def create_reminder(self,reminder_data:Dict[str,Any])->str:
        cur=await self.execute("INSERT INTO reminders(user_id,guild_id,channel_id,remind_at,completed,data) VALUES(?,?,?,?,0,?)",(reminder_data.get('user_id'),reminder_data.get('guild_id'),reminder_data.get('channel_id'),reminder_data.get('remind_at'),json.dumps(reminder_data)));return str(cur.lastrowid)

    async def get_due_reminders(self,current_time:float)->List[Dict[str,Any]]:
        rows=await self.fetchall("SELECT * FROM reminders WHERE remind_at<=? AND completed=0",(current_time,));return [dict(r) for r in rows]

    async def complete_reminder(self,reminder_id:str)->bool:
        cur=await self.execute("UPDATE reminders SET completed=1 WHERE id=?",(int(reminder_id),));return cur.rowcount>0

    async def get_shop_items(self,guild_id:int)->List[Dict[str,Any]]:
        rows=await self.fetchall("SELECT * FROM shop WHERE guild_id=?",(guild_id,));return [dict(r) for r in rows]

    async def create_shop_item(self,item_data:Dict[str,Any])->str:
        cur=await self.execute("INSERT INTO shop(guild_id,name,price,data) VALUES(?,?,?,?)",(item_data['guild_id'],item_data['name'],item_data['price'],json.dumps(item_data)));return str(cur.lastrowid)
