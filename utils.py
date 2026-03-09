import asyncio
import logging
from typing import Any, Callable

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram import Bot

from database import Database

db = Database()

logger = logging.getLogger("homework_handlers")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    _handler = logging.StreamHandler()
    _formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    _handler.setFormatter(_formatter)
    logger.addHandler(_handler)

MAX_DELETE_CONCURRENCY = 12
MAX_NOTIFY_CONCURRENCY = 20
MEDIA_GROUP_DEBOUNCE_SEC = 0.8
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
SOLUTIONS_INDEX_TTL_SEC = 120

SUBJECTS_WITH_SOLUTIONS = {"Алгебра", "Геометрия"}

async def safe_edit_or_answer(message: Message, text: str, reply_markup=None, parse_mode=None):
    try:
        return await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        err = str(e).lower()
        if any(kw in err for kw in (
            "message is not modified",
            "message to edit not found",
            "message can't be edited",
            "there is no text in the message",
            "message_id_invalid",
        )):
            return await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
        raise

async def db_call(func: Callable[..., Any], *args, **kwargs) -> Any:
    return await asyncio.to_thread(func, *args, **kwargs)

async def delete_messages_batch(bot: Bot, chat_id: int, message_ids: list[int], error_prefix: str):
    unique_ids = [mid for mid in dict.fromkeys(message_ids) if isinstance(mid, int)]
    if not unique_ids:
        return
    semaphore = asyncio.Semaphore(MAX_DELETE_CONCURRENCY)
    async def _delete(message_id: int):
        async with semaphore:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception as e:
                print(f"❌ {error_prefix} {message_id}: {e}")
    await asyncio.gather(*(_delete(mid) for mid in unique_ids), return_exceptions=True)

async def clear_last_homework_photos(query: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    message_ids = data.get("last_homework_message_ids", []) or data.get("last_homework_photo_ids", [])
    if not message_ids:
        return
    await delete_messages_batch(
        bot=query.bot,
        chat_id=query.message.chat.id,
        message_ids=message_ids,
        error_prefix="Не удалось удалить сообщение ДЗ"
    )
    await state.update_data(last_homework_message_ids=[], last_homework_photo_ids=[])

async def clear_last_solution_messages(query: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    solution_message_ids = list(data.get("last_solution_message_ids", []))
    prompt_message_id = data.get("solution_prompt_message_id")
    cancel_message_id = data.get("solution_cancel_message_id")
    user_task_message_id = data.get("solution_user_task_message_id")

    if prompt_message_id: solution_message_ids.append(prompt_message_id)
    if cancel_message_id: solution_message_ids.append(cancel_message_id)
    if user_task_message_id: solution_message_ids.append(user_task_message_id)

    if not solution_message_ids:
        return
    await delete_messages_batch(
        bot=query.bot,
        chat_id=query.message.chat.id,
        message_ids=solution_message_ids,
        error_prefix="Не удалось удалить сообщение решения"
    )
    await state.update_data(
        last_solution_message_ids=[],
        solution_prompt_message_id=None,
        solution_cancel_message_id=None,
        solution_user_task_message_id=None
    )

_pending_add_media_groups: dict[str, asyncio.Task] = {}
_pending_edit_media_groups: dict[str, asyncio.Task] = {}

def _media_group_task_key(message: Message) -> str:
    return f"{message.chat.id}:{message.media_group_id}"

def schedule_add_media_group_confirmation(message: Message):
    from keyboards import build_add_content_keyboard
    if not message.media_group_id: return
    key = _media_group_task_key(message)
    task = _pending_add_media_groups.get(key)
    if task: task.cancel()
    async def _send_confirmation():
        try:
            await asyncio.sleep(MEDIA_GROUP_DEBOUNCE_SEC)
            await message.answer("✅ Фото добавлено!\nПродолжайте:", reply_markup=build_add_content_keyboard())
        except asyncio.CancelledError:
            return
        finally:
            _pending_add_media_groups.pop(key, None)
    _pending_add_media_groups[key] = asyncio.create_task(_send_confirmation())

def schedule_edit_media_group_confirmation(message: Message, state: FSMContext):
    from keyboards import build_edit_content_keyboard
    if not message.media_group_id: return
    key = _media_group_task_key(message)
    task = _pending_edit_media_groups.get(key)
    if task: task.cancel()
    async def _send_confirmation():
        try:
            await asyncio.sleep(MEDIA_GROUP_DEBOUNCE_SEC)
            data = await state.get_data()
            photos = data.get("photos", [])
            await state.update_data(waiting_for_photo=False)
            await message.answer(
                f"✅ Фото добавлено! ({len(photos)} шт.) Выберите действие:",
                reply_markup=build_edit_content_keyboard(),
            )
        except asyncio.CancelledError:
            return
        finally:
            _pending_edit_media_groups.pop(key, None)
    _pending_edit_media_groups[key] = asyncio.create_task(_send_confirmation())

async def send_notifications_to_users(bot: Bot, date: str, subject: str):
    from keyboards import format_date_with_weekday
    users = await db_call(db.get_all_users)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 Открыть ДЗ", callback_data=f"view_subject_{date}_{subject}")],
        [InlineKeyboardButton(text="📅 Все ДЗ", callback_data="student_view")],
    ])
    notification_text = (
        f"🔔 Новое домашнее задание!\n\n📚 Предмет: {subject}\n"
        f"📅 Дата: {format_date_with_weekday(date)}\n\nНажмите кнопку ниже, чтобы открыть задание."
    )
    semaphore = asyncio.Semaphore(MAX_NOTIFY_CONCURRENCY)
    async def _send(user_id: int):
        async with semaphore:
            try:
                await bot.send_message(chat_id=user_id, text=notification_text, reply_markup=keyboard)
            except Exception as e:
                pass
    await asyncio.gather(*[_send(user_id) for user_id in users], return_exceptions=True)
