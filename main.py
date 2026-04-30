import asyncio
import logging
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from handlers import router
from database import init_db
from scheduler import setup_scheduler

# Загружаем переменные окружения из файла .env
load_dotenv()

# Включаем логирование
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")

async def main():
    if not BOT_TOKEN:
        print("Ошибка: Токен бота не установлен!")
        return

    # Инициализация БД
    await init_db()
    
    # Инициализация бота и диспетчера
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    
    # Подключаем роутер с хэндлерами
    dp.include_router(router)
    
    # Инициализация и запуск планировщика (APScheduler)
    scheduler = setup_scheduler(bot)
    
    # Удаляем вебхук на всякий случай и запускаем поллинг
    await bot.delete_webhook(drop_pending_updates=True)
    
    print("Бот и планировщик успешно запущены! Ожидаем команды /start...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
