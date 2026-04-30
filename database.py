import aiosqlite
import datetime

import os

DB_DIR = "data"
os.makedirs(DB_DIR, exist_ok=True)
DB_NAME = os.path.join(DB_DIR, "bot_database.db")

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                current_day INTEGER DEFAULT 0,
                current_step INTEGER DEFAULT 0,
                status TEXT DEFAULT 'registered',
                registration_date TEXT,
                last_completed_date TEXT
            )
        ''')
        try:
            await db.execute('ALTER TABLE users ADD COLUMN current_step INTEGER DEFAULT 0')
        except:
            pass
        try:
            await db.execute('ALTER TABLE users ADD COLUMN status TEXT DEFAULT "registered"')
        except:
            pass
        await db.commit()

async def add_user(user_id: int, username: str):
    async with aiosqlite.connect(DB_NAME) as db:
        now = datetime.datetime.now().isoformat()
        await db.execute('''
            INSERT OR IGNORE INTO users (user_id, username, registration_date)
            VALUES (?, ?, ?)
        ''', (user_id, username, now))
        await db.commit()

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)) as cursor:
            columns = [col[0] for col in cursor.description]
            row = await cursor.fetchone()
            if row:
                return dict(zip(columns, row))
            return None

async def update_user_state(user_id: int, current_day: int = None, current_step: int = None, status: str = None, set_completed_date: bool = False):
    async with aiosqlite.connect(DB_NAME) as db:
        query = "UPDATE users SET "
        params = []
        updates = []
        if current_day is not None:
            updates.append("current_day = ?")
            params.append(current_day)
        if current_step is not None:
            updates.append("current_step = ?")
            params.append(current_step)
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if set_completed_date:
            updates.append("last_completed_date = ?")
            params.append(datetime.date.today().isoformat())
            
        if not updates:
            return
            
        query += ", ".join(updates) + " WHERE user_id = ?"
        params.append(user_id)
        
        await db.execute(query, tuple(params))
        await db.commit()
        
async def get_users_for_next_day():
    async with aiosqlite.connect(DB_NAME) as db:
        today = datetime.date.today().isoformat()
        async with db.execute('''
            SELECT * FROM users 
            WHERE status LIKE 'completed_day_%' 
            AND last_completed_date < ?
        ''', (today,)) as cursor:
            columns = [col[0] for col in cursor.description]
            rows = await cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]
