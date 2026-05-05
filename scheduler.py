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
FIRST_REMINDER_DELAY = timedelta(hours=1)
SECOND_REMINDER_DELAY = timedelta(hours=1)
FOLLOW_UP_REMINDER_DELAY = timedelta(hours=12)

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
        
        if next_day > 4:
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

async def check_day_completion(bot: Bot, user_id: int, day: int):
    # Проверка: завершил ли пользователь день
    user = await database.get_user(user_id)
    if not user:
        cancel_day_check(user_id, day)
        return
    # Если день совпадает, но статус не completed_day_X (step < 6)
    if user['current_day'] != day or user['current_step'] >= 6:
        cancel_day_check(user_id, day)
        await database.clear_user_reminders(user_id)
        return

    # Отправляем напоминалку и планируем следующий запуск по цепочке 1ч, 1ч, 12ч...
    import content
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Продолжить выполнение", callback_data="continue_execution")]
    ])
    reminder_count = user.get('reminder_count') or 0
    try:
        await bot.send_message(user_id, content.REMINDER_TEXT, reply_markup=kb)
        reminder_count += 1
        logging.info(
            f"Sent reminder #{reminder_count} to user {user_id}, day {day}, current_step {user['current_step']}"
        )
    except Exception as e:
        logging.error(f"Failed to send reminder to {user_id}: {e}")

    next_run = datetime.now(MOSCOW_TZ) + get_next_reminder_delay(reminder_count)
    await database.update_reminder_state(user_id, reminder_count, next_run.isoformat())
    schedule_day_check(bot, user_id, day, next_run)

def get_day_check_job_id(user_id: int, day: int) -> str:
    return f"day_check_{user_id}_{day}"

def cancel_day_check(user_id: int, day: int):
    job_id = get_day_check_job_id(user_id, day)
    existing = scheduler.get_job(job_id)
    if existing:
        existing.remove()
        logging.info(f"Cancelled day check for user {user_id}, day {day}")

def get_next_reminder_delay(reminder_count: int) -> timedelta:
    if reminder_count <= 0:
        return FIRST_REMINDER_DELAY
    if reminder_count == 1:
        return SECOND_REMINDER_DELAY
    return FOLLOW_UP_REMINDER_DELAY

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
    job_id = get_day_check_job_id(user_id, day)
    # Удаляем старый джоб если есть, чтобы не было дублей
    existing = scheduler.get_job(job_id)
    if existing:
        existing.remove()
    run_date = parse_moscow_datetime(run_date) or datetime.now(MOSCOW_TZ) + FIRST_REMINDER_DELAY
    scheduler.add_job(
        check_day_completion,
        'date',
        run_date=run_date,
        args=[bot, user_id, day],
        id=job_id,
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=int(FOLLOW_UP_REMINDER_DELAY.total_seconds()),
    )
    logging.info(f"Scheduled day check for user {user_id}, day {day}, next run at {run_date}")

async def restore_day_checks(bot: Bot):
    users = await database.get_unfinished_started_users()
    if not users:
        logging.info("No unfinished started users found for reminder restoration.")
        return

    now = datetime.now(MOSCOW_TZ)
    for user in users:
        reminder_count = user.get('reminder_count') or 0
        next_run = parse_moscow_datetime(user.get('next_reminder_at'))
        if not next_run:
            next_run = now + get_next_reminder_delay(reminder_count)
            await database.update_reminder_state(user['user_id'], reminder_count, next_run.isoformat())
        elif next_run <= now:
            next_run = now + timedelta(seconds=5)
            await database.update_reminder_state(user['user_id'], reminder_count, next_run.isoformat())

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
