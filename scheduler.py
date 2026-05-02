import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot
import database

scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

async def send_next_day(bot: Bot):
    logging.info("Running scheduler check for next day materials...")
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
        await database.update_user_state(user_id, current_day=next_day, current_step=0, status=f'day_{next_day}_started')
        schedule_day_check(bot, user_id, next_day)
        
        try:
            await send_step(bot, user_id, next_day, 0)
        except Exception as e:
            logging.error(f"Failed to send next day to user {user_id}: {e}")

async def check_day_completion(bot: Bot, user_id: int, day: int):
    # Проверка: завершил ли пользователь день
    user = await database.get_user(user_id)
    if not user:
        return
    # Если день совпадает, но статус не completed_day_X (step < 6)
    if user['current_day'] == day and user['current_step'] < 6:
        # Отправляем напоминалку
        import content
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Продолжить выполнение", callback_data="continue_execution")]
        ])
        try:
            await bot.send_message(user_id, content.REMINDER_TEXT, reply_markup=kb)
        except Exception as e:
            logging.error(f"Failed to send reminder to {user_id}: {e}")

def schedule_day_check(bot: Bot, user_id: int, day: int):
    from datetime import datetime, timedelta
    job_id = f"day_check_{user_id}_{day}"
    # Удаляем старый джоб если есть, чтобы не было дублей
    existing = scheduler.get_job(job_id)
    if existing:
        existing.remove()
    run_date = datetime.now() + timedelta(hours=2)
    scheduler.add_job(check_day_completion, 'date', run_date=run_date, args=[bot, user_id, day], id=job_id)
    logging.info(f"Scheduled day check for user {user_id}, day {day} at {run_date}")

def setup_scheduler(bot: Bot):
    # Запуск каждый день в 08:00
    scheduler.add_job(send_next_day, CronTrigger(hour=9, minute=0), args=[bot])
    
    scheduler.start()
    return scheduler
