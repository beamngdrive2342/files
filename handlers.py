"""
Обработчики команд и логика бота v2.0
Полностью переработанная версия с выбором по кнопкам
"""

import logging
from pathlib import Path
import asyncio
import calendar
from time import monotonic
from typing import Any, Callable

from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    FSInputFile,
    ErrorEvent,
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta
from config import ADMIN_ID, ADMIN_PASSWORD, SUBJECTS, DAYS_TO_SHOW, SCHEDULE, get_unique_subjects_for_weekday
from database import Database

# Инициализация
router = Router()
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

# Предметы, для которых есть решения из учебника
SUBJECTS_WITH_SOLUTIONS = {"Алгебра", "Геометрия"}

_solution_index: dict[str, dict[str, list[Path]]] = {"algebra": {}, "geometry": {}}
_solution_index_expires_at = 0.0
_solution_index_lock = asyncio.Lock()


async def safe_edit_or_answer(
    message,
    text: str,
    reply_markup=None,
    parse_mode=None,
):
    """
    Пытается отредактировать сообщение (edit_text).
    Если не удаётся — отправляет новое.
    Возвращает итоговый Message.
    """
    try:
        return await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        err = str(e).lower()
        # Если сообщение нельзя редактировать (удалено, не изменено, медиа) — шлём новое
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
    """Выполняет синхронный вызов БД в отдельном потоке, не блокируя event loop."""
    return await asyncio.to_thread(func, *args, **kwargs)


def build_add_content_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Добавить текст", callback_data="add_text")],
        [InlineKeyboardButton(text="📸 Добавить фото", callback_data="add_photo"),
         InlineKeyboardButton(text="📋 Добавить PDF", callback_data="add_pdf")],
        [InlineKeyboardButton(text="✅ Завершить", callback_data="finish_add")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="add_back_subject")],
    ])


def build_edit_content_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить текст", callback_data="edit_text")],
        [InlineKeyboardButton(text="📸 Добавить ещё фото", callback_data="edit_photo")],
        [InlineKeyboardButton(text="✅ Сохранить изменения", callback_data="finish_edit")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="edit_back_subject")],
    ])


_pending_add_media_groups: dict[str, asyncio.Task] = {}
_pending_edit_media_groups: dict[str, asyncio.Task] = {}


def _media_group_task_key(message: Message) -> str:
    return f"{message.chat.id}:{message.media_group_id}"


def schedule_add_media_group_confirmation(message: Message):
    if not message.media_group_id:
        return

    key = _media_group_task_key(message)
    task = _pending_add_media_groups.get(key)
    if task:
        task.cancel()

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
    if not message.media_group_id:
        return

    key = _media_group_task_key(message)
    task = _pending_edit_media_groups.get(key)
    if task:
        task.cancel()

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


async def delete_messages_batch(bot: Bot, chat_id: int, message_ids: list[int], error_prefix: str):
    """Параллельное удаление сообщений с ограничением конкуренции."""
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


def build_solutions_index() -> dict[str, dict[str, list[Path]]]:
    """Строит индекс файлов решений: предмет -> номер задания -> список изображений."""
    roots = {
        "algebra": Path("solutions") / "algebra",
        "geometry": Path("solutions") / "Geometry",
    }
    index: dict[str, dict[str, list[Path]]] = {"algebra": {}, "geometry": {}}

    for subject_key, root in roots.items():
        if not root.is_dir():
            continue

        for task_dir in sorted(root.iterdir(), key=lambda p: p.name):
            if not task_dir.is_dir():
                continue

            task_number = task_dir.name.lstrip("0") or "0"
            files = sorted(
                (p for p in task_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS),
                key=lambda p: p.name
            )

            existing = index[subject_key].get(task_number)
            if existing is None or (not existing and files):
                index[subject_key][task_number] = files

    return index


async def get_solution_files(subject_key: str, task_number: str) -> tuple[bool, list[Path]]:
    """
    Возвращает (задание_существует, файлы_решения) из кэша индекса.
    Индекс обновляется в фоне не чаще одного раза за TTL.
    """
    global _solution_index, _solution_index_expires_at

    now = monotonic()
    if now >= _solution_index_expires_at:
        async with _solution_index_lock:
            now = monotonic()
            if now >= _solution_index_expires_at:
                _solution_index = await asyncio.to_thread(build_solutions_index)
                _solution_index_expires_at = now + SOLUTIONS_INDEX_TTL_SEC

    subject_map = _solution_index.get(subject_key, {})
    if task_number not in subject_map:
        return False, []

    return True, list(subject_map[task_number])



# ==================== HELPER ФУНКЦИИ ====================
def get_dates_list(days: int = DAYS_TO_SHOW, past_days: int = 0):
    """Получение списка дат на N дней вперед"""
    dates = []
    for i in range(-past_days, days):
        date = datetime.now() + timedelta(days=i)
        dates.append(date.strftime("%d.%m.%Y"))
    return dates


def format_date_with_weekday(date_str: str, mark_today: bool = False) -> str:
    """Форматирует дату как ДД.ММ.ГГГГ (полный_день_недели)."""
    try:
        dt = datetime.strptime(date_str, "%d.%m.%Y")
        weekday_names = {
            "Mon": "понедельник",
            "Tue": "вторник",
            "Wed": "среда",
            "Thu": "четверг",
            "Fri": "пятница",
            "Sat": "суббота",
            "Sun": "воскресенье",
        }
        day_name = weekday_names.get(dt.strftime("%a"), dt.strftime("%A"))
        formatted = f"{date_str} ({day_name})"
        if mark_today and date_str == datetime.now().strftime("%d.%m.%Y"):
            formatted += " [сегодня]"
        return formatted
    except Exception:
        return date_str


def create_date_buttons(
    dates: list,
    prefix: str = "select_date_",
    back_callback: str = "back_to_menu",
    mark_today: bool = False
) -> InlineKeyboardMarkup:
    """Создание кнопок для выбора дат"""
    buttons = []
    for date in dates:
        label = format_date_with_weekday(date, mark_today=mark_today)
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"{prefix}{date}")])
    
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def create_subject_buttons(prefix: str = "subject_", back_callback: str = "back_to_menu") -> InlineKeyboardMarkup:
    """Создание кнопок для выбора предмета"""
    buttons = []
    for subject in SUBJECTS:
        buttons.append([InlineKeyboardButton(text=subject, callback_data=f"{prefix}{subject}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_weekday_from_date(date_str: str) -> int | None:
    """Возвращает номер дня недели (0=Пн) по строке даты ДД.ММ.ГГГГ."""
    try:
        dt = datetime.strptime(date_str, "%d.%m.%Y")
        return dt.weekday()
    except Exception:
        return None


def create_schedule_subject_buttons(
    date_str: str,
    prefix: str = "subject_",
    back_callback: str = "back_to_menu",
    homework_dict: dict | None = None,
) -> InlineKeyboardMarkup:
    """
    Создание кнопок предметов по расписанию для данного дня.
    Если homework_dict предоставлен, рядом с предметами с ДЗ ставится ✅.
    Для выходных показывает все предметы.
    """
    weekday = get_weekday_from_date(date_str)
    if weekday is not None and weekday in SCHEDULE:
        subjects = get_unique_subjects_for_weekday(weekday)
    else:
        subjects = SUBJECTS

    buttons = []
    for idx, subject in enumerate(subjects, start=1):
        if homework_dict is not None and subject in homework_dict:
            label = f"{idx}. ✅ {subject}"
        else:
            label = f"{idx}. {subject}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"{prefix}{subject}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def create_month_calendar_keyboard(
    year: int,
    month: int,
    back_callback: str = "student_view",
    date_callback_prefix: str = "view_date_",
    nav_callback_prefix: str = "view_calendar_",
) -> InlineKeyboardMarkup:
    """Календарь месяца (7 колонок, недели строками) для выбора даты."""
    month_names = {
        1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
        5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
        9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
    }
    weekday_headers = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    today = datetime.now()

    cal = calendar.Calendar(firstweekday=0)  # понедельник
    weeks = cal.monthdayscalendar(year, month)

    buttons = []
    buttons.append([InlineKeyboardButton(text=f"{month_names.get(month, str(month))} {year}", callback_data="noop")])
    buttons.append([InlineKeyboardButton(text=wd, callback_data="noop") for wd in weekday_headers])

    for week in weeks:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text="   ", callback_data="noop"))
                continue

            date_str = f"{day:02d}.{month:02d}.{year}"
            day_label = f"{day:02d}"
            if day == today.day and month == today.month and year == today.year:
                label = f"[{day_label}]"
            else:
                label = f" {day_label} "
            row.append(InlineKeyboardButton(text=label, callback_data=f"{date_callback_prefix}{date_str}"))
        buttons.append(row)

    prev_month = month - 1
    prev_year = year
    if prev_month == 0:
        prev_month = 12
        prev_year -= 1

    next_month = month + 1
    next_year = year
    if next_month == 13:
        next_month = 1
        next_year += 1

    buttons.append([
        InlineKeyboardButton(text="◀️", callback_data=f"{nav_callback_prefix}{prev_year}_{prev_month:02d}"),
        InlineKeyboardButton(text="➡️", callback_data=f"{nav_callback_prefix}{next_year}_{next_month:02d}"),
    ])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)





async def clear_last_homework_photos(query: CallbackQuery, state: FSMContext):
    """Удаляет ранее отправленные сообщения ДЗ из чата (фото и текстовые задания)."""
    data = await state.get_data()
    # Поддержка обоих ключей для обратной совместимости
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
    """Удаляет ранее отправленные сообщения с решениями (если есть)."""
    data = await state.get_data()
    solution_message_ids = list(data.get("last_solution_message_ids", []))
    prompt_message_id = data.get("solution_prompt_message_id")
    cancel_message_id = data.get("solution_cancel_message_id")
    user_task_message_id = data.get("solution_user_task_message_id")

    if prompt_message_id:
        solution_message_ids.append(prompt_message_id)
    if cancel_message_id:
        solution_message_ids.append(cancel_message_id)
    if user_task_message_id:
        solution_message_ids.append(user_task_message_id)

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


async def send_notifications_to_users(bot: Bot, date: str, subject: str):
    """
    Отправка уведомлений всем зарегистрированным пользователям о новом ДЗ
    
    Args:
        bot: Экземпляр бота
        date: Дата ДЗ
        subject: Предмет
    """
    users = await db_call(db.get_all_users)
    
    # Создаем кнопку для быстрого открытия ДЗ
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📚 Открыть ДЗ",
            callback_data=f"view_subject_{date}_{subject}"
        )],
        [InlineKeyboardButton(text="📅 Все ДЗ", callback_data="student_view")],
    ])
    
    notification_text = (
        f"🔔 Новое домашнее задание!\n\n"
        f"📚 Предмет: {subject}\n"
        f"📅 Дата: {format_date_with_weekday(date)}\n\n"
        f"Нажмите кнопку ниже, чтобы открыть задание."
    )
    
    semaphore = asyncio.Semaphore(MAX_NOTIFY_CONCURRENCY)

    async def _send(user_id: int):
        async with semaphore:
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=notification_text,
                    reply_markup=keyboard
                )
            except Exception as e:
                # Игнорируем ошибки (пользователь заблокировал бота и т.д.)
                print(f"❌ Не удалось отправить уведомление пользователю {user_id}: {e}")

    await asyncio.gather(*[_send(user_id) for user_id in users], return_exceptions=True)


# ==================== FSM СОСТОЯНИЯ ====================
class AdminAuthStates(StatesGroup):
    """Аутентификация админа"""
    waiting_for_password = State()


class AddHomeworkStates(StatesGroup):
    """Добавление ДЗ"""
    waiting_for_date = State()
    waiting_for_subject = State()
    waiting_for_source_type = State()  # Из учебника или нет
    waiting_for_content = State()


class EditHomeworkStates(StatesGroup):
    """Редактирование ДЗ"""
    waiting_for_date = State()
    waiting_for_subject = State()
    waiting_for_content = State()


class DeleteHomeworkStates(StatesGroup):
    """Удаление ДЗ"""
    waiting_for_date = State()
    waiting_for_subject = State()

class SolutionSearchStates(StatesGroup):
    """Поиск решений (Алгебра/Геометрия)"""
    waiting_for_number = State()


# ==================== ОБЩИЕ КОМАНДЫ ====================
@router.message(Command("start"))
async def cmd_start(message: Message):
    """Главное меню"""
    # Регистрируем пользователя для получения уведомлений
    await db_call(
        db.register_user,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name
    )
    
    if message.from_user.id == ADMIN_ID:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👑 Админ панель", callback_data="admin_auth")],
            [InlineKeyboardButton(text="📚 Мои ДЗ", callback_data="student_view")],
        ])
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📚 Мои ДЗ", callback_data="student_view")],
        ])

    await message.answer(
        "👋 Привет! Я бот с домашними заданиями для 10А класса.\n\n"
        "Выбери действие:",
        reply_markup=keyboard
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Справка"""
    help_text = (
        "📚 Справка по командам:\n\n"
        "👨‍🎓 Для всех:\n"
        "• /start - главное меню\n"
        "• Выбор ДЗ по датам и предметам\n\n"
    )

    if message.from_user.id == ADMIN_ID:
        help_text += (
            "👑 Для администратора:\n"
            "• Админ панель с защитой паролем\n"
            "• Добавление ДЗ по предметам\n"
            "• Редактирование и удаление\n"
        )

    await message.answer(help_text)


# ==================== АУТЕНТИФИКАЦИЯ АДМИНА ====================
@router.callback_query(F.data == "admin_auth")
async def admin_auth(query: CallbackQuery, state: FSMContext):
    """Запрос пароля для входа в админ панель"""
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ У вас нет доступа!", show_alert=True)
        return

    await state.set_state(AdminAuthStates.waiting_for_password)

    cancel_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="pwd_cancel")],
    ])

    await query.message.edit_text(
        "🔐 Введите пароль администратора:",
        reply_markup=cancel_keyboard
    )
    # Сохраняем ID сообщения-приглашения, чтобы удалить его после ввода пароля
    await state.update_data(
        pwd_prompt_message_id=query.message.message_id,
        pwd_prompt_chat_id=query.message.chat.id
    )
    await query.answer()


@router.callback_query(F.data == "pwd_cancel", AdminAuthStates.waiting_for_password)
async def cancel_password_input(query: CallbackQuery, state: FSMContext):
    """Отмена ввода пароля"""
    if query.from_user.id != ADMIN_ID:
        return
    await state.clear()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👑 Админ панель", callback_data="admin_auth")],
        [InlineKeyboardButton(text="📚 Мои ДЗ", callback_data="student_view")],
    ])
    await query.message.edit_text(
        "👋 Главное меню\n\nВыбери действие:",
        reply_markup=keyboard
    )
    await query.answer()


@router.message(AdminAuthStates.waiting_for_password)
async def handle_password_input(message: Message, state: FSMContext):
    """Обработка ввода пароля текстом"""
    if message.from_user.id != ADMIN_ID:
        return

    entered = (message.text or "").strip()
    data = await state.get_data()

    # Удаляем сообщение пользователя с паролем
    try:
        await message.delete()
    except Exception:
        pass

    # Удаляем сообщение-приглашение «Введите пароль»
    prompt_message_id = data.get("pwd_prompt_message_id")
    prompt_chat_id = data.get("pwd_prompt_chat_id")
    if prompt_message_id and prompt_chat_id:
        try:
            await message.bot.delete_message(chat_id=prompt_chat_id, message_id=prompt_message_id)
        except Exception:
            pass

    if entered == ADMIN_PASSWORD:
        await state.clear()
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить ДЗ", callback_data="add_hw")],
            [InlineKeyboardButton(text="✏️ Редактировать", callback_data="edit_hw")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data="delete_hw")],
            [InlineKeyboardButton(text="📋 Все ДЗ", callback_data="view_all_hw")],
            [InlineKeyboardButton(text="◀️ Выход в меню", callback_data="back_to_menu")],
        ])
        await message.answer(
            "✅ Добро пожаловать, администратор!\n\n"
            "Выберите действие:",
            reply_markup=keyboard
        )
    else:
        # Неверный пароль — отправляем новое приглашение и сохраняем его ID
        cancel_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="pwd_cancel")],
        ])
        new_prompt = await message.answer(
            "🔐 Введите пароль администратора:\n\n"
            "❌ Неверный пароль, попробуйте ещё раз:",
            reply_markup=cancel_keyboard
        )
        await state.update_data(
            pwd_prompt_message_id=new_prompt.message_id,
            pwd_prompt_chat_id=new_prompt.chat.id
        )



# ==================== АДМИН ПАНЕЛЬ ====================
@router.callback_query(F.data == "admin_panel")
async def show_admin_panel(query: CallbackQuery, state: FSMContext):
    """Возврат в меню админ панели (без ввода пароля)"""
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ У вас нет доступа!", show_alert=True)
        return
    await state.clear()
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить ДЗ", callback_data="add_hw")],
        [InlineKeyboardButton(text="✏️ Редактировать", callback_data="edit_hw")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data="delete_hw")],
        [InlineKeyboardButton(text="📋 Все ДЗ", callback_data="view_all_hw")],
        [InlineKeyboardButton(text="◀️ Выход в меню", callback_data="back_to_menu")],
    ])
    
    await query.message.edit_text(
        "✅ Админ панель\n\nВыберите действие:",
        reply_markup=keyboard
    )
    await query.answer()


# ==================== ДОБАВЛЕНИЕ ДЗ ====================
@router.callback_query(F.data == "add_hw")
async def start_add_hw(query: CallbackQuery, state: FSMContext):
    """Начало добавления ДЗ - выбор даты"""
    await state.set_state(AddHomeworkStates.waiting_for_date)
    today = datetime.now()
    keyboard = create_month_calendar_keyboard(
        today.year,
        today.month,
        back_callback="admin_panel",
        date_callback_prefix="add_date_",
        nav_callback_prefix="add_calendar_",
    )
    
    await query.message.edit_text(
        "📅 Выберите дату в календаре:",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data.startswith("add_calendar_"), AddHomeworkStates.waiting_for_date)
async def add_calendar_month(query: CallbackQuery):
    """Переключение месяцев в календаре выбора даты для добавления ДЗ."""
    payload = query.data.replace("add_calendar_", "", 1)
    try:
        year_str, month_str = payload.split("_", 1)
        year = int(year_str)
        month = int(month_str)
        if month < 1 or month > 12:
            raise ValueError("invalid month")
    except Exception:
        await query.answer("❌ Ошибка календаря", show_alert=True)
        return

    keyboard = create_month_calendar_keyboard(
        year,
        month,
        back_callback="admin_panel",
        date_callback_prefix="add_date_",
        nav_callback_prefix="add_calendar_",
    )
    await query.message.edit_text(
        "📅 Выберите дату в календаре:",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data.startswith("add_date_"), AddHomeworkStates.waiting_for_date)
async def add_select_date(query: CallbackQuery, state: FSMContext):
    """Выбор даты для добавления"""
    date = query.data.replace("add_date_", "")
    
    await state.update_data(date=date)
    await state.set_state(AddHomeworkStates.waiting_for_subject)
    
    keyboard = create_schedule_subject_buttons(date, "add_subject_", "add_hw")
    
    await query.message.edit_text(
        f"📚 Выберите предмет для даты {format_date_with_weekday(date)}:",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data.startswith("add_subject_"), AddHomeworkStates.waiting_for_subject)
async def add_select_subject(query: CallbackQuery, state: FSMContext):
    """Выбор предмета для добавления"""
    subject = query.data.replace("add_subject_", "")
    data = await state.get_data()
    date = data.get("date")
    
    if await db_call(db.homework_exists, date, subject):
        await query.answer(
            f"⚠️ ДЗ по {subject} на {format_date_with_weekday(date)} уже существует!",
            show_alert=True
        )
        return
    
    await state.update_data(subject=subject, photos=[], text_parts=[])
    
    # Для предметов с решениями — спрашиваем тип задания
    if subject in SUBJECTS_WITH_SOLUTIONS:
        await state.set_state(AddHomeworkStates.waiting_for_source_type)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📘 Из учебника", callback_data="add_source_textbook")],
            [InlineKeyboardButton(text="📝 Другое (доска, карточка...)", callback_data="add_source_other")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="add_back_to_subject_from_source")],
        ])
        await query.message.edit_text(
            f"📚 {subject} — откуда задание?\n\n"
            f"📘 **Из учебника** — ученики смогут найти ответы\n"
            f"📝 **Другое** — без кнопки ответов",
            reply_markup=keyboard
        )
        await query.answer()
        return
    
    # Для остальных предметов — сразу к контенту
    await state.update_data(is_textbook=False)
    await state.set_state(AddHomeworkStates.waiting_for_content)
    
    keyboard = build_add_content_keyboard()
    
    await query.message.edit_text(
        f"📝 Добавление ДЗ:\n"
        f"Дата: {format_date_with_weekday(date)}\n"
        f"Предмет: {subject}\n\n"
        f"Добавляйте текст, фото и PDF в любом порядке:",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data == "add_back_to_subject_from_source", AddHomeworkStates.waiting_for_source_type)
async def add_back_to_subject_from_source(query: CallbackQuery, state: FSMContext):
    """Назад к выбору предмета из экрана выбора типа задания"""
    if query.from_user.id != ADMIN_ID:
        return
    data = await state.get_data()
    date = data.get("date")
    if not date:
        await query.answer("❌ Ошибка: дата не найдена", show_alert=True)
        return
    await state.update_data(subject=None, is_textbook=False)
    await state.set_state(AddHomeworkStates.waiting_for_subject)
    keyboard = create_schedule_subject_buttons(date, "add_subject_", "add_hw")
    await query.message.edit_text(
        f"📚 Выберите предмет для даты {format_date_with_weekday(date)}:",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data.startswith("add_source_"), AddHomeworkStates.waiting_for_source_type)
async def add_select_source_type(query: CallbackQuery, state: FSMContext):
    """Выбор типа задания: из учебника или другое"""
    source = query.data.replace("add_source_", "")
    is_textbook = (source == "textbook")
    data = await state.get_data()
    date = data.get("date")
    subject = data.get("subject")
    
    await state.update_data(is_textbook=is_textbook)
    await state.set_state(AddHomeworkStates.waiting_for_content)
    
    source_label = "📘 из учебника" if is_textbook else "📝 другое"
    
    keyboard = build_add_content_keyboard()
    
    await query.message.edit_text(
        f"📝 Добавление ДЗ:\n"
        f"Дата: {format_date_with_weekday(date)}\n"
        f"Предмет: {subject} ({source_label})\n\n"
        f"Добавляйте текст, фото и PDF в любом порядке:",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data == "add_text", AddHomeworkStates.waiting_for_content)
async def add_text_input(query: CallbackQuery, state: FSMContext):
    """Запрос текста для ДЗ"""
    await state.update_data(waiting_for_text=True)
    await query.message.edit_text("✍️ Отправьте текст задания:")
    await query.answer()


@router.message(AddHomeworkStates.waiting_for_content)
async def process_add_content(message: Message, state: FSMContext):
    """Обработка содержимого (текст/фото/PDF)"""
    data = await state.get_data()
    photos = data.get("photos", [])  # Здесь храним и фото, и PDF (с префиксом pdf:)

    # Удаляем старое сообщение-запрос (чтобы чат не засорялся)
    prompt_id = data.get("content_prompt_id")
    if prompt_id:
        try:
            await message.bot.delete_message(message.chat.id, prompt_id)
        except Exception:
            pass

    def _get_kb():
        return build_add_content_keyboard()

    if data.get("waiting_for_text"):
        text_parts = data.get("text_parts", [])
        text_parts.append(message.text or "")
        await state.update_data(text_parts=text_parts, waiting_for_text=False)
        await message.answer("✅ Текст добавлен!\nПродолжайте:", reply_markup=_get_kb())

    elif message.photo:
        photos.append(message.photo[-1].file_id)
        await state.update_data(photos=photos)
        if message.media_group_id:
            schedule_add_media_group_confirmation(message)
        else:
            await message.answer("✅ Фото добавлено!\nПродолжайте:", reply_markup=_get_kb())

    elif message.document and message.document.mime_type == "application/pdf":
        photos.append(f"pdf:{message.document.file_id}")
        await state.update_data(photos=photos)
        await message.answer(f"✅ PDF добавлен!\nПродолжайте:", reply_markup=_get_kb())
    else:
        await message.answer("⚠️ Неподдерживаемый формат. Используйте текст, фото или PDF.", reply_markup=_get_kb())


@router.callback_query(F.data == "add_photo", AddHomeworkStates.waiting_for_content)
async def add_photo_input(query: CallbackQuery, state: FSMContext):
    """Запрос фото для ДЗ"""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_content_input")]
    ])
    msg = await query.message.edit_text("📸 Отправьте фотографию задания (или несколько по одной):", reply_markup=kb)
    await state.update_data(content_prompt_id=msg.message_id)
    await query.answer()


@router.callback_query(F.data == "add_pdf", AddHomeworkStates.waiting_for_content)
async def add_pdf_input(query: CallbackQuery, state: FSMContext):
    """Запрос PDF для ДЗ"""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_content_input")]
    ])
    msg = await query.message.edit_text("📋 Отправьте PDF-файл (или несколько по одному):", reply_markup=kb)
    await state.update_data(content_prompt_id=msg.message_id)
    await query.answer()


@router.callback_query(F.data == "cancel_content_input", AddHomeworkStates.waiting_for_content)
async def cancel_content_input(query: CallbackQuery, state: FSMContext):
    """Отмена ввода фото/PDF/текста"""
    data = await state.get_data()
    date = data.get("date")
    subject = data.get("subject")
    
    await state.update_data(waiting_for_text=False)
    
    keyboard = build_add_content_keyboard()
    
    await query.message.edit_text(
        f"📝 Добавление ДЗ:\nДата: {date}\nПредмет: {subject}\n\nВыбирайте действие:",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data == "add_back_subject", AddHomeworkStates.waiting_for_content)
async def add_back_to_subject(query: CallbackQuery, state: FSMContext):
    """Назад к выбору предмета при добавлении"""
    if query.from_user.id != ADMIN_ID:
        return
    data = await state.get_data()
    date = data.get("date")
    if not date:
        await query.answer("❌ Ошибка: дата не найдена", show_alert=True)
        return
    await state.update_data(subject=None, text_parts=[], photos=[], waiting_for_text=False)
    await state.set_state(AddHomeworkStates.waiting_for_subject)
    keyboard = create_schedule_subject_buttons(date, "add_subject_", "add_hw")
    await query.message.edit_text(f"📚 Выберите предмет для даты {format_date_with_weekday(date)}:", reply_markup=keyboard)
    await query.answer()


@router.callback_query(F.data == "finish_add", AddHomeworkStates.waiting_for_content)
async def finish_add_hw(query: CallbackQuery, state: FSMContext):
    """Завершение добавления ДЗ"""
    data = await state.get_data()
    date = data.get("date")
    subject = data.get("subject")
    text_parts = data.get("text_parts", [])
    photos = data.get("photos", [])
    is_textbook = data.get("is_textbook", False)
    
    if not text_parts and not photos:
        await query.answer("❌ Добавьте текст, фото или PDF!", show_alert=True)
        return
    
    full_text = "\n\n".join(text_parts).strip()
    
    if await db_call(db.add_homework, date, subject, full_text, photos, is_textbook):
        source_label = " (📘 учебник)" if is_textbook else ""
        # Создаем клавиатуру с выбором действий
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить еще (на ту же дату)", callback_data=f"add_more_hw_{date}")],
            [InlineKeyboardButton(text="📊 В админ панель", callback_data="admin_panel")],
        ])
        
        await query.message.edit_text(
            f"✅ ДЗ по предмету **{subject}**{source_label} на **{format_date_with_weekday(date)}** успешно добавлено!\n\n"
            f"Что вы хотите сделать дальше?",
            reply_markup=keyboard
        )
        # Сбрасываем состояние
        await state.clear()
        
        # Уведомления отключены по просьбе пользователя
        # await send_notifications_to_users(query.bot, date, subject)
    else:
        await query.message.edit_text("❌ Ошибка при сохранении в базу данных!")
    
    await query.answer()


@router.callback_query(F.data.startswith("add_more_hw_"))
async def add_more_hw(query: CallbackQuery, state: FSMContext):
    """Быстрый переход к добавлению еще одного предмета на ту же дату"""
    if query.from_user.id != ADMIN_ID:
        return
        
    date = query.data.replace("add_more_hw_", "")
    
    # Сразу переходим к выбору предмета для этой даты
    await state.update_data(date=date)
    await state.set_state(AddHomeworkStates.waiting_for_subject)
    
    keyboard = create_schedule_subject_buttons(date, "add_subject_", "admin_panel")
    
    await query.message.edit_text(
        f"📚 Выберите следующий предмет для даты {format_date_with_weekday(date)}:",
        reply_markup=keyboard
    )
    await query.answer()


# ==================== РЕДАКТИРОВАНИЕ ДЗ ====================
@router.callback_query(F.data == "edit_hw")
async def start_edit_hw(query: CallbackQuery, state: FSMContext):
    """Начало редактирования - выбор даты"""
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ У вас нет доступа!", show_alert=True)
        return
    await state.set_state(EditHomeworkStates.waiting_for_date)

    today = datetime.now()
    keyboard = create_month_calendar_keyboard(
        today.year,
        today.month,
        back_callback="admin_panel",
        date_callback_prefix="edit_date_",
        nav_callback_prefix="edit_calendar_",
    )

    await query.message.edit_text(
        "📅 Выберите дату в календаре:",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data.startswith("edit_calendar_"), EditHomeworkStates.waiting_for_date)
async def edit_calendar_month(query: CallbackQuery):
    """Переключение месяцев в календаре выбора даты для редактирования ДЗ."""
    payload = query.data.replace("edit_calendar_", "", 1)
    try:
        year_str, month_str = payload.split("_", 1)
        year = int(year_str)
        month = int(month_str)
        if month < 1 or month > 12:
            raise ValueError()
    except Exception:
        await query.answer("❌ Ошибка календаря", show_alert=True)
        return

    keyboard = create_month_calendar_keyboard(
        year,
        month,
        back_callback="admin_panel",
        date_callback_prefix="edit_date_",
        nav_callback_prefix="edit_calendar_",
    )
    await query.message.edit_text(
        "📅 Выберите дату в календаре:",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data.startswith("edit_date_"), EditHomeworkStates.waiting_for_date)
async def edit_select_date(query: CallbackQuery, state: FSMContext):
    """Выбор даты для редактирования"""
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ У вас нет доступа!", show_alert=True)
        return
    date = query.data.replace("edit_date_", "")

    homework_dict = await db_call(db.get_homework_by_date, date)

    if not homework_dict:
        await query.answer(f"❌ На дату {format_date_with_weekday(date)} нет ДЗ", show_alert=True)
        return

    await state.update_data(date=date)
    await state.set_state(EditHomeworkStates.waiting_for_subject)

    keyboard = create_schedule_subject_buttons(
        date,
        prefix="edit_subject_",
        back_callback="edit_hw",
        homework_dict=homework_dict,
    )

    await query.message.edit_text(
        f"📚 Выберите предмет для редактирования на {format_date_with_weekday(date)}:",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data.startswith("edit_subject_"), EditHomeworkStates.waiting_for_subject)
async def edit_select_subject(query: CallbackQuery, state: FSMContext):
    """Выбор предмета - показ текущего ДЗ и опций редактирования"""
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ У вас нет доступа!", show_alert=True)
        return
    subject = query.data.replace("edit_subject_", "")
    data = await state.get_data()
    date = data.get("date")
    
    homework = await db_call(db.get_homework, date, subject)
    if not homework:
        await query.answer("❌ ДЗ не найдено", show_alert=True)
        return
    
    text, photos, is_textbook = homework
    await state.update_data(subject=subject, text=text, photos=photos or [], is_textbook=is_textbook)
    await state.set_state(EditHomeworkStates.waiting_for_content)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить текст", callback_data="edit_text")],
        [InlineKeyboardButton(text="📸 Добавить фото", callback_data="edit_photo")],
        [InlineKeyboardButton(text="✅ Сохранить изменения", callback_data="finish_edit")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="edit_back_subject")],
    ])
    
    preview = text[:200] + "..." if len(text) > 200 else text
    await query.message.edit_text(
        f"✏️ Редактирование ДЗ:\n\n"
        f"📅 Дата: {format_date_with_weekday(date)}\n"
        f"📚 Предмет: {subject}\n\n"
        f"📝 Текущий текст:\n{preview}\n\n"
        f"📸 Фото: {len(photos or [])} шт.\n\n"
        f"Выберите действие:",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data == "edit_text", EditHomeworkStates.waiting_for_content)
async def edit_text_input(query: CallbackQuery, state: FSMContext):
    """Запрос нового текста для ДЗ"""
    await state.update_data(waiting_for_text=True)
    await query.message.edit_text("✍️ Отправьте новый текст задания (текущий текст будет заменён):")
    await query.answer()


@router.callback_query(F.data == "edit_photo", EditHomeworkStates.waiting_for_content)
async def edit_photo_input(query: CallbackQuery, state: FSMContext):
    """Запрос фото для добавления к ДЗ"""
    await state.update_data(waiting_for_photo=True)
    await query.message.edit_text("📸 Отправьте фотографию (или несколько по одной) для добавления к заданию:")
    await query.answer()


@router.callback_query(F.data == "edit_back_subject", EditHomeworkStates.waiting_for_content)
async def edit_back_to_subject(query: CallbackQuery, state: FSMContext):
    """Назад к выбору предмета при редактировании"""
    if query.from_user.id != ADMIN_ID:
        return
    data = await state.get_data()
    date = data.get("date")
    if not date:
        await query.answer("❌ Ошибка: дата не найдена", show_alert=True)
        return
    await state.update_data(subject=None, text=None, photos=[], waiting_for_text=False, waiting_for_photo=False)
    await state.set_state(EditHomeworkStates.waiting_for_subject)
    homework_dict = await db_call(db.get_homework_by_date, date)
    keyboard = create_schedule_subject_buttons(
        date,
        prefix="edit_subject_",
        back_callback="edit_hw",
        homework_dict=homework_dict,
    )
    await query.message.edit_text(
        f"📚 Выберите предмет для редактирования на {format_date_with_weekday(date)}:",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data == "finish_edit", EditHomeworkStates.waiting_for_content)
async def finish_edit_hw(query: CallbackQuery, state: FSMContext):
    """Сохранение изменений при редактировании"""
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ У вас нет доступа!", show_alert=True)
        return
    data = await state.get_data()
    date = data.get("date")
    subject = data.get("subject")
    text = data.get("text", "")
    photos = data.get("photos", [])
    
    if not text:
        await query.answer("❌ Текст задания не может быть пустым!", show_alert=True)
        return
    
    if await db_call(db.update_homework, date, subject, text, photos):
        await query.message.edit_text(
            f"✅ ДЗ успешно обновлено!\n\n"
            f"📅 Дата: {format_date_with_weekday(date)}\n"
            f"📚 Предмет: {subject}"
        )
    else:
        await query.message.edit_text("❌ Ошибка при сохранении!")
    
    await state.clear()
    await query.answer()


@router.message(EditHomeworkStates.waiting_for_content)
async def process_edit_content(message: Message, state: FSMContext):
    """Обработка текста/фото при редактировании"""
    if message.from_user.id != ADMIN_ID:
        return
    data = await state.get_data()
    
    if data.get("waiting_for_text"):
        text = message.text or ""
        await state.update_data(text=text, waiting_for_text=False)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить текст", callback_data="edit_text")],
            [InlineKeyboardButton(text="📸 Добавить фото", callback_data="edit_photo")],
            [InlineKeyboardButton(text="✅ Сохранить изменения", callback_data="finish_edit")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="edit_back_subject")],
        ])
        await message.answer("✅ Текст обновлён! Выберите дальнейшее действие:", reply_markup=keyboard)
    elif data.get("waiting_for_photo") and message.photo:
        photo_id = message.photo[-1].file_id
        photos = data.get("photos", [])
        photos.append(photo_id)
        if message.media_group_id:
            await state.update_data(photos=photos, waiting_for_photo=True)
            schedule_edit_media_group_confirmation(message, state)
        else:
            await state.update_data(photos=photos, waiting_for_photo=False)
            await message.answer(
                f"✅ Фото добавлено! ({len(photos)} шт.) Выберите действие:",
                reply_markup=build_edit_content_keyboard(),
            )


# ==================== УДАЛЕНИЕ ДЗ ====================
@router.callback_query(F.data == "delete_hw")
async def start_delete_hw(query: CallbackQuery, state: FSMContext):
    """Начало удаления - выбор даты"""
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ У вас нет доступа!", show_alert=True)
        return
    await state.set_state(DeleteHomeworkStates.waiting_for_date)
    today = datetime.now()
    keyboard = create_month_calendar_keyboard(
        today.year,
        today.month,
        back_callback="admin_panel",
        date_callback_prefix="del_date_",
        nav_callback_prefix="del_calendar_",
    )
    
    await query.message.edit_text(
        "📅 Выберите дату в календаре:",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data.startswith("del_calendar_"), DeleteHomeworkStates.waiting_for_date)
async def delete_calendar_month(query: CallbackQuery):
    """Переключение месяцев в календаре выбора даты для удаления ДЗ."""
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ У вас нет доступа!", show_alert=True)
        return
    payload = query.data.replace("del_calendar_", "", 1)
    try:
        year_str, month_str = payload.split("_", 1)
        year = int(year_str)
        month = int(month_str)
        if month < 1 or month > 12:
            raise ValueError("invalid month")
    except Exception:
        await query.answer("❌ Ошибка календаря", show_alert=True)
        return

    keyboard = create_month_calendar_keyboard(
        year,
        month,
        back_callback="admin_panel",
        date_callback_prefix="del_date_",
        nav_callback_prefix="del_calendar_",
    )
    await query.message.edit_text(
        "📅 Выберите дату в календаре:",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data.startswith("del_date_"), DeleteHomeworkStates.waiting_for_date)
async def delete_select_date(query: CallbackQuery, state: FSMContext):
    """Выбор даты для удаления"""
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ У вас нет доступа!", show_alert=True)
        return
    date = query.data.replace("del_date_", "")
    
    homework_dict = await db_call(db.get_homework_by_date, date)
    
    if not homework_dict:
        await query.answer(f"❌ На дату {format_date_with_weekday(date)} нет ДЗ", show_alert=True)
        return
    
    subjects = list(homework_dict.keys())
    await state.update_data(date=date, delete_subjects=subjects)
    await state.set_state(DeleteHomeworkStates.waiting_for_subject)
    
    buttons = [
        [InlineKeyboardButton(text=f"🗑 {subject}", callback_data=f"del_subject_idx_{idx}")]
        for idx, subject in enumerate(subjects)
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="delete_hw")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    await query.message.edit_text(
        f"🗑 Выберите какой предмет удалить на {format_date_with_weekday(date)}:",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data.startswith("del_subject_"), DeleteHomeworkStates.waiting_for_subject)
async def delete_confirm(query: CallbackQuery, state: FSMContext):
    """Подтверждение удаления"""
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ У вас нет доступа!", show_alert=True)
        return
    data = await state.get_data()
    subject = None
    callback_data = query.data or ""
    if callback_data.startswith("del_subject_idx_"):
        try:
            idx = int(callback_data.replace("del_subject_idx_", "", 1))
            subjects = data.get("delete_subjects", [])
            if idx < 0 or idx >= len(subjects):
                raise ValueError("invalid subject idx")
            subject = subjects[idx]
        except Exception:
            await query.answer("❌ Ошибка выбора предмета", show_alert=True)
            return
    else:
        # Совместимость со старыми кнопками
        subject = callback_data.replace("del_subject_", "", 1)

    date = data.get("date")
    await state.update_data(subject=subject)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data="confirm_del")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="del_back_subject")],
    ])
    
    await query.message.edit_text(
        f"⚠️ Вы уверены что хотите удалить ДЗ по {subject} на {format_date_with_weekday(date)}?",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data.startswith("del_back_subject"))
async def delete_back_to_subject(query: CallbackQuery, state: FSMContext):
    """Назад к выбору предмета при удалении"""
    if query.from_user.id != ADMIN_ID:
        return
    callback_data = query.data or ""
    if callback_data == "del_back_subject":
        date = ""
    else:
        date = callback_data.replace("del_back_subject_", "", 1)
    if not date:
        data = await state.get_data()
        date = data.get("date")
    if not date:
        await query.answer("❌ Ошибка: дата не найдена", show_alert=True)
        return
    await state.update_data(date=date)
    await state.set_state(DeleteHomeworkStates.waiting_for_subject)
    homework_dict = await db_call(db.get_homework_by_date, date)
    subjects = list(homework_dict.keys())
    await state.update_data(delete_subjects=subjects)
    buttons = [
        [InlineKeyboardButton(text=f"🗑 {subject}", callback_data=f"del_subject_idx_{idx}")]
        for idx, subject in enumerate(subjects)
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="delete_hw")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await query.message.edit_text(
        f"🗑 Выберите какой предмет удалить на {format_date_with_weekday(date)}:",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data.startswith("confirm_del"))
async def confirm_delete(query: CallbackQuery, state: FSMContext):
    """Подтверждение удаления"""
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ У вас нет доступа!", show_alert=True)
        return
    data = await state.get_data()
    date = data.get("date")
    subject = data.get("subject")

    # Совместимость со старыми кнопками
    if not date or not subject:
        parts = query.data.replace("confirm_del_", "").rsplit("_", 1)
        if len(parts) != 2:
            await query.answer("❌ Ошибка удаления", show_alert=True)
            return
        date = parts[0]
        subject = parts[1]
    
    if await db_call(db.delete_homework, date, subject):
        await query.message.edit_text(f"✅ ДЗ по {subject} на {format_date_with_weekday(date)} удалено!")
    else:
        await query.message.edit_text("❌ Ошибка при удалении")
    
    await state.clear()
    await query.answer()


# ==================== ПРОСМОТР ВСЕХ ДЗ ====================
@router.callback_query(F.data == "view_all_hw")
async def view_all_hw(query: CallbackQuery):
    """Просмотр всех ДЗ"""
    all_hw = await db_call(db.get_all_homework)
    
    if not all_hw:
        await query.message.edit_text("📭 ДЗ еще не добавлено")
        await query.answer()
        return
    
    text = "📋 Все домашние задания:\n\n"
    for date, subject, hw_text, photos, is_tb in all_hw:
        photo_info = f" 📸({len(photos)})" if photos else ""
        tb_info = " 📘" if is_tb else ""
        text += f"📅 {format_date_with_weekday(date)} | 📚 {subject}{tb_info}{photo_info}\n"
        text += f"   {hw_text[:50]}{'...' if len(hw_text) > 50 else ''}\n\n"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")],
    ])
    
    await query.message.edit_text(text[:4000], reply_markup=keyboard)
    await query.answer()


# ==================== ПРОСМОТР УЧЕНИКАМИ ====================
@router.callback_query(F.data == "student_view")
async def student_view(query: CallbackQuery, state: FSMContext):
    """Главное меню просмотра ДЗ (ученики)"""
    await clear_last_solution_messages(query, state)
    await clear_last_homework_photos(query, state)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 На сегодня", callback_data="view_today")],
        [InlineKeyboardButton(text="📅 На завтра", callback_data="view_tomorrow")],
        [InlineKeyboardButton(text="📅 Выбрать дату", callback_data="view_select_date")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")],
    ])

    await safe_edit_or_answer(
        query.message,
        "📚 Просмотр домашних заданий для 10А класса\n\n"
        "Выберите действие:",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data == "view_select_date")
async def view_select_date(query: CallbackQuery):
    """Выбор даты для просмотра ДЗ"""
    today = datetime.now()
    keyboard = create_month_calendar_keyboard(today.year, today.month, "student_view")
    
    await query.message.edit_text(
        "📅 Выберите дату в календаре:",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data.startswith("view_calendar_"))
async def view_calendar_month(query: CallbackQuery):
    """Переключение месяцев в календаре выбора даты."""
    payload = query.data.replace("view_calendar_", "", 1)
    try:
        year_str, month_str = payload.split("_", 1)
        year = int(year_str)
        month = int(month_str)
        if month < 1 or month > 12:
            raise ValueError("invalid month")
    except Exception:
        await query.answer("❌ Ошибка календаря", show_alert=True)
        return

    keyboard = create_month_calendar_keyboard(year, month, "student_view")
    await query.message.edit_text(
        "📅 Выберите дату в календаре:",
        reply_markup=keyboard
    )
    await query.answer()


async def display_homework_for_date(query: CallbackQuery, state: FSMContext, date: str, date_label: str):
    """
    Показывает список предметов по расписанию на дату.
    Предметы, для которых есть ДЗ, отмечены ✅.
    Ученик нажимает на предмет — видит ДЗ.
    """
    # Проверка на выходной день (суббота=5, воскресенье=6)
    weekday = get_weekday_from_date(date)
    if weekday is not None and weekday >= 5:
        await query.answer("😴 В этот день уроков нет!", show_alert=True)
        return

    await clear_last_solution_messages(query, state)
    await clear_last_homework_photos(query, state)
    homework_dict = await db_call(db.get_homework_by_date, date)
    formatted_date = format_date_with_weekday(date, mark_today=True)

    # Сохраняем дату для навигации «Назад»
    await state.update_data(current_view_date=date)

    if date_label in ("Сегодня", "Завтра"):
        title = f"📚 Расписание на {date_label.lower()} ({formatted_date})"
    else:
        title = f"📚 Расписание на {formatted_date}"

    keyboard = create_schedule_subject_buttons(
        date,
        prefix=f"stview_{date}_",
        back_callback="student_view",
        homework_dict=homework_dict,
    )

    await safe_edit_or_answer(
        query.message,
        f"{title}\n\n"
        f"✅ — задание есть\n"
        f"Нажмите на предмет, чтобы посмотреть ДЗ:",
        reply_markup=keyboard,
    )
    await query.answer()


@router.callback_query(F.data == "view_today")
async def view_today_homework(query: CallbackQuery, state: FSMContext):
    """Просмотр ДЗ на сегодня"""
    today = datetime.now().strftime("%d.%m.%Y")
    await display_homework_for_date(query, state, today, "Сегодня")


@router.callback_query(F.data == "view_tomorrow")
async def view_tomorrow_homework(query: CallbackQuery, state: FSMContext):
    """Просмотр ДЗ на завтра"""
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d.%m.%Y")
    await display_homework_for_date(query, state, tomorrow, "Завтра")


@router.callback_query(F.data.startswith("view_date_"))
async def view_date_selected(query: CallbackQuery, state: FSMContext):
    """Просмотр ДЗ на выбранную дату"""
    date = query.data.replace("view_date_", "")
    await display_homework_for_date(query, state, date, date)


@router.callback_query(F.data.startswith("stview_"))
async def view_subject_from_schedule(query: CallbackQuery, state: FSMContext):
    """Просмотр ДЗ по предмету (из расписания)"""
    # callback_data = stview_{date}_{subject}
    payload = query.data.replace("stview_", "", 1)
    # Дата в формате ДД.ММ.ГГГГ (10 символов), затем _ и предмет
    date = payload[:10]
    subject = payload[11:]  # пропускаем _

    await clear_last_solution_messages(query, state)
    await clear_last_homework_photos(query, state)

    homework = await db_call(db.get_homework, date, subject)

    if not homework:
        await query.answer(f"📭 По предмету {subject} задание не задано", show_alert=True)
        return

    text, photos, is_textbook = homework

    # Кнопки внизу
    buttons = []
    if is_textbook:
        if subject == "Алгебра":
            buttons.append([InlineKeyboardButton(text="🔎 Найти решение", callback_data="find_solution_algebra")])
        elif subject == "Геометрия":
            buttons.append([InlineKeyboardButton(text="🔎 Найти решение", callback_data="find_solution_geometry")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"view_date_{date}")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    photo_message_ids = []

    if photos:
        # Заголовок в текущем сообщении
        header_msg = await safe_edit_or_answer(
            query.message,
            f"📚 {subject}\n"
            f"📅 {format_date_with_weekday(date, mark_today=True)}",
        )
        if header_msg:
            photo_message_ids.append(header_msg.message_id)
        # Отправляем фото
        for photo_id in photos:
            try:
                if isinstance(photo_id, str) and photo_id.startswith("pdf:"):
                    sent_message = await query.message.answer_document(photo_id[4:])
                else:
                    sent_message = await query.message.answer_photo(photo_id)
                photo_message_ids.append(sent_message.message_id)
            except Exception as e:
                print(f"❌ Ошибка при отправке фото: {e}")
        # Текст задания + кнопки внизу
        bottom_msg = await query.message.answer(
            f"📝 {text}",
            reply_markup=keyboard,
        )
        photo_message_ids.append(bottom_msg.message_id)
    else:
        # Без фото — всё в одном сообщении
        await safe_edit_or_answer(
            query.message,
            f"📚 {subject}\n"
            f"📅 {format_date_with_weekday(date, mark_today=True)}\n\n"
            f"📝 {text}",
            reply_markup=keyboard,
        )

    await state.update_data(last_homework_message_ids=photo_message_ids)
    await query.answer()


@router.callback_query(F.data.startswith("view_subject_"))
async def view_homework(query: CallbackQuery, state: FSMContext):
    """Просмотр ДЗ по предмету (из уведомлений, обратная совместимость)"""
    data_parts = query.data.replace("view_subject_", "").rsplit("_", 1)
    date = data_parts[0]
    subject = data_parts[1]

    await clear_last_solution_messages(query, state)
    await clear_last_homework_photos(query, state)
    homework = await db_call(db.get_homework, date, subject)

    if not homework:
        await query.answer("❌ ДЗ не найдено", show_alert=True)
        return

    text, photos, is_textbook = homework

    buttons = []
    if is_textbook:
        if subject == "Алгебра":
            buttons.append([InlineKeyboardButton(text="🔎 Найти решение", callback_data="find_solution_algebra")])
        elif subject == "Геометрия":
            buttons.append([InlineKeyboardButton(text="🔎 Найти решение", callback_data="find_solution_geometry")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"view_date_{date}")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    photo_message_ids = []

    if photos:
        header_msg = await safe_edit_or_answer(
            query.message,
            f"📚 {subject}\n"
            f"📅 {format_date_with_weekday(date, mark_today=True)}",
        )
        if header_msg:
            photo_message_ids.append(header_msg.message_id)
        for photo_id in photos:
            try:
                if isinstance(photo_id, str) and photo_id.startswith("pdf:"):
                    sent_message = await query.message.answer_document(photo_id[4:])
                else:
                    sent_message = await query.message.answer_photo(photo_id)
                photo_message_ids.append(sent_message.message_id)
            except Exception as e:
                print(f"❌ Ошибка при отправке фото: {e}")
        bottom_msg = await query.message.answer(
            f"📝 {text}",
            reply_markup=keyboard,
        )
        photo_message_ids.append(bottom_msg.message_id)
    else:
        await safe_edit_or_answer(
            query.message,
            f"📚 {subject}\n"
            f"📅 {format_date_with_weekday(date, mark_today=True)}\n\n"
            f"📝 {text}",
            reply_markup=keyboard,
        )

    await state.update_data(last_homework_message_ids=photo_message_ids)
    await query.answer()

@router.callback_query(F.data == "find_solution_algebra")
async def find_solution_algebra(query: CallbackQuery, state: FSMContext):
    """Запрос номера задания для решений по алгебре"""
    await clear_last_solution_messages(query, state)
    await state.set_state(SolutionSearchStates.waiting_for_number)
    prompt_message = await query.message.answer(
        "🔎 Введите номер задания по алгебре (например, 123):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="cancel_solution_search")],
        ])
    )
    await state.update_data(
        solution_subject="algebra",
        solution_prompt_message_id=prompt_message.message_id,
        solution_cancel_message_id=None,
        solution_user_task_message_id=None
    )
    await query.answer()


@router.callback_query(F.data == "find_solution_geometry")
async def find_solution_geometry(query: CallbackQuery, state: FSMContext):
    """Запрос номера задания для решений по геометрии"""
    await clear_last_solution_messages(query, state)
    await state.set_state(SolutionSearchStates.waiting_for_number)
    prompt_message = await query.message.answer(
        "🔎 Введите номер задания по геометрии (например, 123):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="cancel_solution_search")],
        ])
    )
    await state.update_data(
        solution_subject="geometry",
        solution_prompt_message_id=prompt_message.message_id,
        solution_cancel_message_id=None,
        solution_user_task_message_id=None
    )
    await query.answer()


@router.callback_query(F.data == "cancel_solution_search", SolutionSearchStates.waiting_for_number)
async def cancel_solution_search(query: CallbackQuery, state: FSMContext):
    """Отмена поиска решений"""
    await clear_last_solution_messages(query, state)
    await state.set_state(None)
    await query.answer("↩️ Возврат к ДЗ за день")


@router.message(SolutionSearchStates.waiting_for_number)
async def handle_solution_number(message: Message, state: FSMContext):
    """Получение номера задания и отправка решений"""
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("❌ Введите только номер задания (например, 123).")
        return

    data = await state.get_data()
    solution_subject = data.get("solution_subject", "algebra")
    if solution_subject == "geometry":
        subject_name = "геометрии"
    else:
        subject_name = "алгебре"

    task_number = raw.lstrip("0") or "0"
    task_exists, image_files = await get_solution_files(solution_subject, task_number)
    if not task_exists:
        await message.answer(f"❌ Решение по {subject_name} для №{task_number} не найдено.")
        await state.clear()
        return

    if not image_files:
        await message.answer(f"❌ В папке №{task_number} нет изображений.")
        await state.clear()
        return

    info_message = await message.answer(f"✅ Решение по {subject_name} для №{task_number}:")
    solution_message_ids = [info_message.message_id]
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="solution_back")],
    ])

    for idx, img_path in enumerate(image_files):
        try:
            sent_message = await message.answer_photo(
                FSInputFile(str(img_path)),
                reply_markup=back_keyboard if idx == len(image_files) - 1 else None
            )
            solution_message_ids.append(sent_message.message_id)
        except Exception as e:
            print(f"❌ Ошибка при отправке фото {img_path}: {e}")

    await state.update_data(
        last_solution_message_ids=solution_message_ids,
        solution_user_task_message_id=message.message_id
    )
    await state.set_state(None)


@router.callback_query(F.data == "solution_back")
async def solution_back(query: CallbackQuery, state: FSMContext):
    """Возврат после просмотра решения: удаляем сообщения с решением."""
    await clear_last_solution_messages(query, state)
    await state.set_state(None)
    await query.answer("↩️ Возврат к ДЗ за день")


@router.callback_query(F.data == "noop")
async def noop_callback(query: CallbackQuery):
    """Пустая кнопка (заголовки/пустые ячейки календаря)."""
    await query.answer()


# ==================== НАВИГАЦИЯ ====================
@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(query: CallbackQuery, state: FSMContext):
    """Возврат в главное меню"""
    await clear_last_solution_messages(query, state)
    await clear_last_homework_photos(query, state)
    await state.clear()
    
    if query.from_user.id == ADMIN_ID:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👑 Админ панель", callback_data="admin_auth")],
            [InlineKeyboardButton(text="📚 Мои ДЗ", callback_data="student_view")],
        ])
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📚 Мои ДЗ", callback_data="student_view")],
        ])

    await safe_edit_or_answer(
        query.message,
        "👋 Главное меню\n\n"
        "Выбери действие:",
        reply_markup=keyboard
    )
    await query.answer()


@router.errors()
async def handle_router_errors(event: ErrorEvent):
    """
    Глобальная обработка ошибок callback.
    Игнорируем только просроченные callback-query, чтобы не засорять логи.
    """
    exc = event.exception
    if isinstance(exc, TelegramBadRequest):
        text = str(exc).lower()
        if (
            "query is too old" in text
            or "query id is invalid" in text
            or "response timeout expired" in text
        ):
            logger.info("⚠️ Просроченный callback-query проигнорирован.")
            return True
    return False
