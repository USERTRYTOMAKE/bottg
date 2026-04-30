import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot
import database

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
        
        if next_day > 3:
            continue
            
        logging.info(f"Transitioning user {user_id} to day {next_day}")
        await database.update_user_state(user_id, current_day=next_day, current_step=0, status=f'day_{next_day}_started')
        
        try:
            await send_step(bot, user_id, next_day, 0)
        except Exception as e:
            logging.error(f"Failed to send next day to user {user_id}: {e}")

def setup_scheduler(bot: Bot):
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    
    # Запуск каждый день в 08:00
    scheduler.add_job(send_next_day, CronTrigger(hour=9, minute=0), args=[bot])
    
    # Для отладки можно расскомментировать следующую строку (будет запускаться раз в минуту):
    # scheduler.add_job(send_next_day, 'interval', minutes=1, args=[bot])
    
    scheduler.start()
    return scheduler
