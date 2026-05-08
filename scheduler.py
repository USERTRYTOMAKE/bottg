import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot
import database

MOSCOW_TZ = ZoneInfo("Europe/Moscow")
DAILY_JOB_ID = "send_next_day_daily"
STARTUP_CATCH_UP_JOB_ID = "send_next_day_startup_catch_up"
RESTORE_DAY_CHECKS_JOB_ID = "restore_day_checks_on_startup"
DAILY_SEND_TIME = time(hour=9, minute=0)
FIRST_REMINDER_DELAY = timedelta(hours=2)
SECOND_REMINDER_DELAY = timedelta(hours=24)
MAX_DAYS = 15

scheduler = AsyncIOScheduler(timezone=MOSCOW_TZ)

async def send_next_day(bot: Bot):
    logging.info("Running scheduler check for next day materials at %s", datetime.now(MOSCOW_TZ).isoformat())
    users = await database.get_users_for_next_day()
    if not users:
        logging.info("No users found for next day transition.")
        return
        
    # Импортируем внутри функции, чтобы избежать циклического импорта
    from handlers import send_step
    
    for user in users:
        user_id = user['user_id']
        current_day = user['current_day']
        next_day = current_day + 1
        
        if next_day > MAX_DAYS:
            continue
            
        logging.info(f"Transitioning user {user_id} to day {next_day}")
        next_reminder_at = await database.start_user_day(
            user_id,
            next_day,
            current_step=0,
            reminder_delay=FIRST_REMINDER_DELAY,
        )
        schedule_day_check(bot, user_id, next_day, next_reminder_at)
        
        try:
            await send_step(bot, user_id, next_day, 0)
        except Exception as e:
            logging.error(f"Failed to send next day to user {user_id}: {e}")

async def check_day_completion_2h(bot: Bot, user_id: int, day: int):
    """Проверка через 2 часа — напоминание с возвратом на текущий день."""
    user = await database.get_user(user_id)
    if not user:
        return
    if user['current_day'] != day or user['current_step'] >= 6:
        await database.clear_user_reminders(user_id)
        return

    import content
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Продолжить выполнение", callback_data="continue_execution")]
    ])
    try:
        await bot.send_message(user_id, content.REMINDER_TEXT_2H, reply_markup=kb)
        logging.info(f"Sent 2h reminder to user {user_id}, day {day}")
    except Exception as e:
        logging.error(f"Failed to send 2h reminder to {user_id}: {e}")


async def check_day_completion_24h(bot: Bot, user_id: int, day: int):
    """Проверка через 24 часа — напоминание с возвратом на вчерашний день."""
    user = await database.get_user(user_id)
    if not user:
        return
    if user['current_day'] != day or user['current_step'] >= 6:
        await database.clear_user_reminders(user_id)
        return

    import content
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Продолжить выполнение", callback_data="continue_execution_yesterday")]
    ])
    try:
        await bot.send_message(user_id, content.REMINDER_TEXT_24H, reply_markup=kb)
        logging.info(f"Sent 24h reminder to user {user_id}, day {day}")
    except Exception as e:
        logging.error(f"Failed to send 24h reminder to {user_id}: {e}")

def get_day_check_job_id(user_id: int, day: int, suffix: str) -> str:
    return f"day_check_{user_id}_{day}_{suffix}"

def cancel_day_check(user_id: int, day: int):
    for suffix in ("2h", "24h"):
        job_id = get_day_check_job_id(user_id, day, suffix)
        existing = scheduler.get_job(job_id)
        if existing:
            existing.remove()
            logging.info(f"Cancelled {suffix} day check for user {user_id}, day {day}")

def parse_moscow_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        parsed = datetime.fromisoformat(value)
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=MOSCOW_TZ)
    return parsed.astimezone(MOSCOW_TZ)

def schedule_day_check(bot: Bot, user_id: int, day: int, run_date=None):
    """Планирует оба таймера: 2ч и 24ч после открытия дня."""
    now = datetime.now(MOSCOW_TZ)

    run_2h = parse_moscow_datetime(run_date) or now + FIRST_REMINDER_DELAY
    job_id_2h = get_day_check_job_id(user_id, day, "2h")
    existing = scheduler.get_job(job_id_2h)
    if existing:
        existing.remove()
    scheduler.add_job(
        check_day_completion_2h,
        'date',
        run_date=run_2h,
        args=[bot, user_id, day],
        id=job_id_2h,
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=int(SECOND_REMINDER_DELAY.total_seconds()),
    )

    run_24h = now + SECOND_REMINDER_DELAY
    job_id_24h = get_day_check_job_id(user_id, day, "24h")
    existing = scheduler.get_job(job_id_24h)
    if existing:
        existing.remove()
    scheduler.add_job(
        check_day_completion_24h,
        'date',
        run_date=run_24h,
        args=[bot, user_id, day],
        id=job_id_24h,
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=int(SECOND_REMINDER_DELAY.total_seconds()),
    )
    logging.info(f"Scheduled 2h check at {run_2h} and 24h check at {run_24h} for user {user_id}, day {day}")

async def restore_day_checks(bot: Bot):
    users = await database.get_unfinished_started_users()
    if not users:
        logging.info("No unfinished started users found for reminder restoration.")
        return

    now = datetime.now(MOSCOW_TZ)
    for user in users:
        next_run = parse_moscow_datetime(user.get('next_reminder_at'))
        if not next_run or next_run <= now:
            next_run = now + timedelta(seconds=5)
            await database.update_reminder_state(user['user_id'], user.get('reminder_count') or 0, next_run.isoformat())

        schedule_day_check(bot, user['user_id'], user['current_day'], next_run)
    logging.info(f"Restored day checks for {len(users)} unfinished users.")

def should_run_startup_catch_up(now: datetime | None = None) -> bool:
    now = now or datetime.now(MOSCOW_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=MOSCOW_TZ)
    else:
        now = now.astimezone(MOSCOW_TZ)
    daily_run_at = datetime.combine(now.date(), DAILY_SEND_TIME, tzinfo=MOSCOW_TZ)
    return now >= daily_run_at

def setup_scheduler(bot: Bot):
    # Запуск каждый день в 09:00 по Москве.
    scheduler.add_job(
        send_next_day,
        CronTrigger(hour=DAILY_SEND_TIME.hour, minute=DAILY_SEND_TIME.minute, timezone=MOSCOW_TZ),
        args=[bot],
        id=DAILY_JOB_ID,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=6 * 60 * 60,
    )

    if should_run_startup_catch_up():
        run_date = datetime.now(MOSCOW_TZ) + timedelta(seconds=5)
        scheduler.add_job(
            send_next_day,
            'date',
            run_date=run_date,
            args=[bot],
            id=STARTUP_CATCH_UP_JOB_ID,
            replace_existing=True,
        )
        logging.info("Scheduled startup catch-up for next day materials at %s", run_date.isoformat())

    restore_run_date = datetime.now(MOSCOW_TZ) + timedelta(seconds=10)
    scheduler.add_job(
        restore_day_checks,
        'date',
        run_date=restore_run_date,
        args=[bot],
        id=RESTORE_DAY_CHECKS_JOB_ID,
        replace_existing=True,
    )
    logging.info("Scheduled reminder restoration at %s", restore_run_date.isoformat())

    if not scheduler.running:
        scheduler.start()

    daily_job = scheduler.get_job(DAILY_JOB_ID)
    logging.info("Daily next day job next run time: %s", daily_job.next_run_time if daily_job else None)
    return scheduler
