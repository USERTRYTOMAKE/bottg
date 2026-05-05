import aiosqlite
import datetime
import os
from zoneinfo import ZoneInfo

DB_DIR = "data"
os.makedirs(DB_DIR, exist_ok=True)
DB_NAME = os.path.join(DB_DIR, "bot_database.db")
MOSCOW_TZ = ZoneInfo("Europe/Moscow")

def get_moscow_now():
    return datetime.datetime.now(MOSCOW_TZ)

def get_moscow_date_iso():
    return get_moscow_now().date().isoformat()

def get_next_reminder_at_iso(delay: datetime.timedelta):
    return (get_moscow_now() + delay).isoformat()

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
                last_completed_date TEXT,
                reminder_count INTEGER DEFAULT 0,
                next_reminder_at TEXT
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
        try:
            await db.execute('ALTER TABLE users ADD COLUMN reminder_count INTEGER DEFAULT 0')
        except:
            pass
        try:
            await db.execute('ALTER TABLE users ADD COLUMN next_reminder_at TEXT')
        except:
            pass
        await db.commit()

async def add_user(user_id: int, username: str):
    async with aiosqlite.connect(DB_NAME) as db:
        now = get_moscow_now().isoformat()
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

async def update_user_state(
    user_id: int,
    current_day: int = None,
    current_step: int = None,
    status: str = None,
    set_completed_date: bool = False,
    reminder_count: int = None,
    next_reminder_at: str = None,
    clear_next_reminder: bool = False,
):
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
            params.append(get_moscow_date_iso())
        if reminder_count is not None:
            updates.append("reminder_count = ?")
            params.append(reminder_count)
        if next_reminder_at is not None:
            updates.append("next_reminder_at = ?")
            params.append(next_reminder_at)
        if clear_next_reminder:
            updates.append("next_reminder_at = NULL")
            
        if not updates:
            return
            
        query += ", ".join(updates) + " WHERE user_id = ?"
        params.append(user_id)
        
        await db.execute(query, tuple(params))
        await db.commit()

async def start_user_day(user_id: int, day: int, current_step: int, reminder_delay: datetime.timedelta):
    next_reminder_at = get_next_reminder_at_iso(reminder_delay)
    await update_user_state(
        user_id,
        current_day=day,
        current_step=current_step,
        status=f'day_{day}_started',
        reminder_count=0,
        next_reminder_at=next_reminder_at,
    )
    return next_reminder_at

async def update_reminder_state(user_id: int, reminder_count: int, next_reminder_at: str):
    await update_user_state(
        user_id,
        reminder_count=reminder_count,
        next_reminder_at=next_reminder_at,
    )

async def clear_user_reminders(user_id: int):
    await update_user_state(user_id, reminder_count=0, clear_next_reminder=True)
        
async def get_users_for_next_day():
    async with aiosqlite.connect(DB_NAME) as db:
        today = get_moscow_date_iso()
        async with db.execute('''
            SELECT * FROM users 
            WHERE status LIKE 'completed_day_%' 
            AND (last_completed_date IS NULL OR last_completed_date < ?)
        ''', (today,)) as cursor:
            columns = [col[0] for col in cursor.description]
            rows = await cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]

async def get_unfinished_started_users():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('''
            SELECT * FROM users
            WHERE status LIKE 'day_%_started'
            AND current_day BETWEEN 1 AND 4
            AND current_step < 6
        ''') as cursor:
            columns = [col[0] for col in cursor.description]
            rows = await cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]
