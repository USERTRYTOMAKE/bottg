import asyncio
import logging
import os

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, LinkPreviewOptions
import database
import content

router = Router()

_FAST_FORWARD_USER_IDS_CACHE = None

def get_fast_forward_user_ids() -> set[int]:
    global _FAST_FORWARD_USER_IDS_CACHE

    raw_value = os.getenv("FAST_FORWARD_USER_IDS", "")
    if (
        _FAST_FORWARD_USER_IDS_CACHE
        and _FAST_FORWARD_USER_IDS_CACHE[0] == raw_value
    ):
        return _FAST_FORWARD_USER_IDS_CACHE[1]

    user_ids = set()
    for value in raw_value.replace(";", ",").split(","):
        value = value.strip()
        if not value:
            continue
        try:
            user_ids.add(int(value))
        except ValueError:
            logging.warning("Ignoring invalid FAST_FORWARD_USER_IDS value: %s", value)

    _FAST_FORWARD_USER_IDS_CACHE = (raw_value, user_ids)
    return user_ids

async def remove_inline_keyboard(callback: CallbackQuery):
    if not callback.message:
        return

    try:
        await callback.bot.edit_message_reply_markup(
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            reply_markup=None,
        )
    except TelegramBadRequest as e:
        error_text = str(e).lower()
        if "message is not modified" in error_text:
            return
        logging.warning(
            "Failed to remove inline keyboard from message %s for user %s: %s",
            callback.message.message_id,
            callback.from_user.id,
            e,
        )
    except Exception as e:
        logging.exception(
            "Unexpected error while removing inline keyboard from message %s for user %s: %s",
            callback.message.message_id,
            callback.from_user.id,
            e,
        )

async def send_step(bot, chat_id, day, step):
    day_content = content.DAYS_CONTENT.get(day)
    if not day_content:
        return
        
    link_opts = LinkPreviewOptions(is_disabled=True)
    
    if step == 0:
        text = day_content['intro']
        btn = day_content.get('intro_btn')
        if btn:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=btn, callback_data="next_step")]
            ])
            await bot.send_message(chat_id, text, reply_markup=kb, link_preview_options=link_opts)
        else:
            await bot.send_message(chat_id, text, link_preview_options=link_opts)
            await database.update_user_state(chat_id, current_step=1)
            await asyncio.sleep(3)
            await send_step(bot, chat_id, day, 1)
            
    elif step in [1, 2, 3, 4]:
        text = day_content[f'step_{step}']
        btn = day_content.get(f'step_{step}_btn')
        
        if btn:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=btn, callback_data="next_step")]
            ])
            await bot.send_message(chat_id, text, reply_markup=kb, link_preview_options=link_opts)
        else:
            await bot.send_message(chat_id, text, link_preview_options=link_opts)
            await database.update_user_state(chat_id, current_step=step+1)
            await asyncio.sleep(3)
            await send_step(bot, chat_id, day, step+1)
            return
        
    elif step == 5:
        text = day_content['step_5']
        btn = day_content['step_5_btn']
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=btn, callback_data="finish_day")],
            [InlineKeyboardButton(text="Вернуться к материалам", callback_data="return_to_materials")]
        ])
        await bot.send_message(chat_id, text, reply_markup=kb, link_preview_options=link_opts)

@router.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username
    
    await database.add_user(user_id, username)
    await database.update_user_state(
        user_id,
        current_day=0,
        current_step=0,
        status='onboarding',
        reminder_count=0,
        clear_next_reminder=True,
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Погнали", callback_data="start_day_1")]
    ])
    link_opts = LinkPreviewOptions(is_disabled=True)
    await message.answer(content.ONBOARDING_2, reply_markup=kb, parse_mode="HTML", link_preview_options=link_opts)

@router.callback_query(F.data == "start_day_1")
async def start_day_1(callback: CallbackQuery):
    user_id = callback.from_user.id
    await remove_inline_keyboard(callback)
    
    from scheduler import FIRST_REMINDER_DELAY, schedule_day_check
    next_reminder_at = await database.start_user_day(
        user_id,
        day=1,
        current_step=1,
        reminder_delay=FIRST_REMINDER_DELAY,
    )
    schedule_day_check(callback.bot, user_id, 1, next_reminder_at)
    await send_step(callback.bot, callback.message.chat.id, 1, 1)
    await callback.answer()

@router.callback_query(F.data == "next_step")
async def process_next_step(callback: CallbackQuery):
    user_id = callback.from_user.id
    await remove_inline_keyboard(callback)
    
    user = await database.get_user(user_id)
    if not user:
        await callback.answer("Ошибка")
        return
        
    day = user['current_day']
    current_step = user['current_step']
    next_step_num = current_step + 1
    
    if next_step_num <= 5:
        await database.update_user_state(user_id, current_step=next_step_num)
        await send_step(callback.bot, callback.message.chat.id, day, next_step_num)
    
    await callback.answer()

@router.callback_query(F.data == "return_to_materials")
async def return_to_materials(callback: CallbackQuery):
    user_id = callback.from_user.id
    await remove_inline_keyboard(callback)
    
    user = await database.get_user(user_id)
    if not user:
        await callback.answer()
        return
        
    day = user['current_day']
    await database.update_user_state(user_id, current_step=1)
    
    await callback.message.answer("Возвращаемся к началу материалов сегодняшнего дня:")
    await send_step(callback.bot, callback.message.chat.id, day, 1)
    await callback.answer()

@router.callback_query(F.data == "finish_day")
async def finish_day(callback: CallbackQuery):
    user_id = callback.from_user.id
    await remove_inline_keyboard(callback)
    
    user = await database.get_user(user_id)
    if not user:
        await callback.answer()
        return
        
    day = user['current_day']
    
    await database.update_user_state(user_id, current_step=6, status=f'completed_day_{day}', set_completed_date=True)
    await database.clear_user_reminders(user_id)
    from scheduler import FIRST_REMINDER_DELAY, MAX_DAYS, cancel_day_check, schedule_day_check
    cancel_day_check(user_id, day)
    
    day_content = content.DAYS_CONTENT[day]
    await callback.message.answer(day_content['finish'])
    await callback.answer()

    if user_id in get_fast_forward_user_ids():
        next_day = day + 1
        if next_day <= MAX_DAYS:
            next_reminder_at = await database.start_user_day(
                user_id,
                next_day,
                current_step=0,
                reminder_delay=FIRST_REMINDER_DELAY,
            )
            schedule_day_check(callback.bot, user_id, next_day, next_reminder_at)
            await send_step(callback.bot, callback.message.chat.id, next_day, 0)
        else:
            logging.info("Fast-forward user %s completed final day %s", user_id, day)

@router.callback_query(F.data == "continue_execution")
async def continue_execution(callback: CallbackQuery):
    """Возврат на текущий день (напоминание 2ч)."""
    user_id = callback.from_user.id
    await remove_inline_keyboard(callback)

    user = await database.get_user(user_id)
    if not user:
        await callback.answer()
        return

    day = user['current_day']
    current_step = user['current_step']

    await send_step(callback.bot, callback.message.chat.id, day, current_step)
    await callback.answer()

@router.callback_query(F.data == "continue_execution_yesterday")
async def continue_execution_yesterday(callback: CallbackQuery):
    """Возврат на вчерашний день (напоминание 24ч)."""
    user_id = callback.from_user.id
    await remove_inline_keyboard(callback)

    user = await database.get_user(user_id)
    if not user:
        await callback.answer()
        return

    day = user['current_day']
    current_step = user['current_step']

    await callback.message.answer("Возвращаемся к началу вчерашнего дня:")
    await send_step(callback.bot, callback.message.chat.id, day, 1)
    await callback.answer()
