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
from config import ADMIN_ID, ADMIN_PASSWORD, SUBJECTS, DAYS_TO_SHOW
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
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
SOLUTIONS_INDEX_TTL_SEC = 120

_solution_index: dict[str, dict[str, list[Path]]] = {"algebra": {}, "geometry": {}}
_solution_index_expires_at = 0.0
_solution_index_lock = asyncio.Lock()


async def db_call(func: Callable[..., Any], *args, **kwargs) -> Any:
    """Выполняет синхронный вызов БД в отдельном потоке, не блокируя event loop."""
    return await asyncio.to_thread(func, *args, **kwargs)


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


def create_password_keypad(entered_digits: str = "") -> InlineKeyboardMarkup:
    """
    Создание клавиатуры для ввода пароля (цифровая клавиатура)
    
    Args:
        entered_digits: Уже введенные цифры пароля
        
    Returns:
        InlineKeyboardMarkup с цифровой клавиатурой
    """
    # Цифровая клавиатура 3x3 + 0 внизу
    buttons = [
        [
            InlineKeyboardButton(text="1", callback_data="pwd_1"),
            InlineKeyboardButton(text="2", callback_data="pwd_2"),
            InlineKeyboardButton(text="3", callback_data="pwd_3"),
        ],
        [
            InlineKeyboardButton(text="4", callback_data="pwd_4"),
            InlineKeyboardButton(text="5", callback_data="pwd_5"),
            InlineKeyboardButton(text="6", callback_data="pwd_6"),
        ],
        [
            InlineKeyboardButton(text="7", callback_data="pwd_7"),
            InlineKeyboardButton(text="8", callback_data="pwd_8"),
            InlineKeyboardButton(text="9", callback_data="pwd_9"),
        ],
        [
            InlineKeyboardButton(text="⌫ Удалить", callback_data="pwd_backspace"),
            InlineKeyboardButton(text="0", callback_data="pwd_0"),
            InlineKeyboardButton(text="🗑 Очистить", callback_data="pwd_clear"),
        ],
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data="pwd_confirm"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="pwd_cancel"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def clear_last_homework_photos(query: CallbackQuery, state: FSMContext):
    """Удаляет ранее отправленные фото ДЗ из чата (если есть)."""
    data = await state.get_data()
    photo_message_ids = data.get("last_homework_photo_ids", [])

    if not photo_message_ids:
        return

    await delete_messages_batch(
        bot=query.bot,
        chat_id=query.message.chat.id,
        message_ids=photo_message_ids,
        error_prefix="Не удалось удалить фото сообщение"
    )

    await state.update_data(last_homework_photo_ids=[])


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
    await state.update_data(entered_password="")
    
    keyboard = create_password_keypad()
    await query.message.edit_text(
        "🔐 Введите пароль для входа в админ панель:\n\n"
        "Пароль: ••••",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data.startswith("pwd_"), AdminAuthStates.waiting_for_password)
async def handle_password_input(query: CallbackQuery, state: FSMContext):
    """Обработка ввода пароля через кнопки"""
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ У вас нет доступа!", show_alert=True)
        return
    
    data = await state.get_data()
    entered_password = data.get("entered_password", "")
    action = query.data.replace("pwd_", "")
    
    if action == "backspace":
        await query.answer()
        # Удалить последний символ
        entered_password = entered_password[:-1] if entered_password else ""
    elif action == "clear":
        await query.answer()
        # Очистить весь пароль
        entered_password = ""
    elif action == "confirm":
        # Проверить пароль
        if entered_password == ADMIN_PASSWORD:
            await query.answer("✅ Пароль верный!")
            await state.clear()
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить ДЗ", callback_data="add_hw")],
                [InlineKeyboardButton(text="✏️ Редактировать", callback_data="edit_hw")],
                [InlineKeyboardButton(text="🗑 Удалить", callback_data="delete_hw")],
                [InlineKeyboardButton(text="📋 Все ДЗ", callback_data="view_all_hw")],
                [InlineKeyboardButton(text="◀️ Выход в меню", callback_data="back_to_menu")],
            ])
            
            await query.message.edit_text(
                "✅ Добро пожаловать, администратор!\n\n"
                "Выберите действие:",
                reply_markup=keyboard
            )
        else:
            await query.answer("❌ Неправильный пароль!", show_alert=True)
            # Сбрасываем пароль и показываем клавиатуру снова
            entered_password = ""
            await state.update_data(entered_password="")
            keyboard = create_password_keypad()
            await query.message.edit_text(
                "🔐 Введите пароль для входа в админ панель:\n\n"
                "Пароль: ••••\n\n"
                "❌ Неправильный пароль! Попробуйте снова.",
                reply_markup=keyboard
            )
        return
    elif action == "cancel":
        await query.answer()
        # Отмена ввода пароля
        await state.clear()
        await query.message.edit_text("❌ Ввод пароля отменен.")
        return
    else:
        await query.answer()
        # Добавить цифру (0-9)
        if len(entered_password) < 10:  # Ограничение на длину пароля
            entered_password += action
    
    # Обновляем состояние
    await state.update_data(entered_password=entered_password)
    
    # Показываем маскированный пароль
    masked_password = "•" * len(entered_password) if entered_password else "••••"
    display_text = (
        f"🔐 Введите пароль для входа в админ панель:\n\n"
        f"Пароль: {masked_password}"
    )
    
    keyboard = create_password_keypad(entered_password)
    await query.message.edit_text(display_text, reply_markup=keyboard)


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
    
    keyboard = create_subject_buttons("add_subject_", "add_hw")
    
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
    
    if db.homework_exists(date, subject):
        await query.answer(
            f"⚠️ ДЗ по {subject} на {format_date_with_weekday(date)} уже существует!",
            show_alert=True
        )
        return
    
    await state.update_data(subject=subject, photos=[], text_parts=[])
    await state.set_state(AddHomeworkStates.waiting_for_content)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Добавить текст", callback_data="add_text")],
        [InlineKeyboardButton(text="📸 Добавить фото", callback_data="add_photo")],
        [InlineKeyboardButton(text="✅ Завершить", callback_data="finish_add")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="add_back_subject")],
    ])
    
    await query.message.edit_text(
        f"📝 Добавление ДЗ:\n"
        f"Дата: {format_date_with_weekday(date)}\n"
        f"Предмет: {subject}\n\n"
        f"Добавляйте текст и фото в любом порядке:",
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
    """Обработка текста/фото при добавлении"""
    data = await state.get_data()
    
    if data.get("waiting_for_text"):
        # Добавляем текст
        text_parts = data.get("text_parts", [])
        text_parts.append(message.text)
        
        await state.update_data(text_parts=text_parts, waiting_for_text=False)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Добавить еще текст", callback_data="add_text")],
            [InlineKeyboardButton(text="📸 Добавить фото", callback_data="add_photo")],
            [InlineKeyboardButton(text="✅ Завершить", callback_data="finish_add")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="add_back_subject")],
        ])
        
        await message.answer(
            "✅ Текст добавлен!\n\n"
            "Продолжайте добавлять содержимое:",
            reply_markup=keyboard
        )
    elif message.photo:
        # Это обработка фото
        photo_id = message.photo[-1].file_id
        photos = data.get("photos", [])
        photos.append(photo_id)
        
        await state.update_data(photos=photos)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Добавить текст", callback_data="add_text")],
            [InlineKeyboardButton(text="📸 Добавить еще фото", callback_data="add_photo")],
            [InlineKeyboardButton(text="✅ Завершить", callback_data="finish_add")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="add_back_subject")],
        ])
        
        await message.answer(
            f"✅ Фото добавлено! ({len(photos)} шт.)\n\n"
            "Продолжайте добавлять содержимое:",
            reply_markup=keyboard
        )


@router.callback_query(F.data == "add_photo", AddHomeworkStates.waiting_for_content)
async def add_photo_input(query: CallbackQuery):
    """Запрос фото для ДЗ"""
    await query.message.edit_text("📸 Отправьте фотографию задания (или несколько по одной):")
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
    keyboard = create_subject_buttons("add_subject_", "add_hw")
    await query.message.edit_text(f"📚 Выберите предмет для даты {date}:", reply_markup=keyboard)
    await query.answer()


@router.callback_query(F.data == "finish_add", AddHomeworkStates.waiting_for_content)
async def finish_add_hw(query: CallbackQuery, state: FSMContext):
    """Завершение добавления ДЗ"""
    data = await state.get_data()
    date = data.get("date")
    subject = data.get("subject")
    text_parts = data.get("text_parts", [])
    photos = data.get("photos", [])
    
    if not text_parts:
        await query.answer("❌ Добавьте хотя бы текст!", show_alert=True)
        return
    
    full_text = "\n\n".join(text_parts)
    
    if await db_call(db.add_homework, date, subject, full_text, photos):
        await query.message.edit_text(
            f"✅ ДЗ успешно добавлено!\n\n"
            f"📅 Дата: {format_date_with_weekday(date)}\n"
            f"📚 Предмет: {subject}\n"
            f"📝 Текст: добавлено\n"
            f"📸 Фото: {len(photos)} шт.\n\n"
            f"🔔 Уведомления отправлены всем пользователям!"
        )
        await state.clear()
        
        # Отправляем уведомления всем пользователям
        await send_notifications_to_users(query.bot, date, subject)
    else:
        await query.message.edit_text("❌ Ошибка при добавлении!")
    
    await query.answer()


# ==================== РЕДАКТИРОВАНИЕ ДЗ ====================
@router.callback_query(F.data == "edit_hw")
async def start_edit_hw(query: CallbackQuery, state: FSMContext):
    """Начало редактирования - выбор даты"""
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ У вас нет доступа!", show_alert=True)
        return
    await state.set_state(EditHomeworkStates.waiting_for_date)
    
    dates = get_dates_list()
    keyboard = create_date_buttons(dates, "edit_date_", "admin_panel")
    
    await query.message.edit_text(
        "📅 Выберите дату для редактирования:",
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
    
    buttons = [[InlineKeyboardButton(text=f"✏️ {subject}", callback_data=f"edit_subject_{subject}")] 
               for subject in homework_dict.keys()]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="edit_hw")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
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
    
    homework = db.get_homework(date, subject)
    if not homework:
        await query.answer("❌ ДЗ не найдено", show_alert=True)
        return
    
    text, photos = homework
    await state.update_data(subject=subject, text=text, photos=photos or [])
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
    buttons = [[InlineKeyboardButton(text=f"✏️ {subject}", callback_data=f"edit_subject_{subject}")] 
               for subject in homework_dict.keys()]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="edit_hw")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
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
        await state.update_data(photos=photos, waiting_for_photo=False)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить текст", callback_data="edit_text")],
            [InlineKeyboardButton(text="📸 Добавить ещё фото", callback_data="edit_photo")],
            [InlineKeyboardButton(text="✅ Сохранить изменения", callback_data="finish_edit")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="edit_back_subject")],
        ])
        await message.answer(f"✅ Фото добавлено! ({len(photos)} шт.) Выберите действие:", reply_markup=keyboard)


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
    
    await state.update_data(date=date)
    await state.set_state(DeleteHomeworkStates.waiting_for_subject)
    
    buttons = [[InlineKeyboardButton(text=f"🗑 {subject}", callback_data=f"del_subject_{subject}")] 
               for subject in homework_dict.keys()]
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
    subject = query.data.replace("del_subject_", "")
    data = await state.get_data()
    date = data.get("date")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_del_{date}_{subject}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"del_back_subject_{date}")],
    ])
    
    await query.message.edit_text(
        f"⚠️ Вы уверены что хотите удалить ДЗ по {subject} на {format_date_with_weekday(date)}?",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data.startswith("del_back_subject_"))
async def delete_back_to_subject(query: CallbackQuery, state: FSMContext):
    """Назад к выбору предмета при удалении"""
    if query.from_user.id != ADMIN_ID:
        return
    date = query.data.replace("del_back_subject_", "")
    await state.update_data(date=date)
    await state.set_state(DeleteHomeworkStates.waiting_for_subject)
    homework_dict = await db_call(db.get_homework_by_date, date)
    buttons = [[InlineKeyboardButton(text=f"🗑 {subject}", callback_data=f"del_subject_{subject}")] 
               for subject in homework_dict.keys()]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="delete_hw")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await query.message.edit_text(
        f"🗑 Выберите какой предмет удалить на {format_date_with_weekday(date)}:",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data.startswith("confirm_del_"))
async def confirm_delete(query: CallbackQuery, state: FSMContext):
    """Подтверждение удаления"""
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ У вас нет доступа!", show_alert=True)
        return
    parts = query.data.replace("confirm_del_", "").rsplit("_", 1)
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
    for date, subject, hw_text, photos in all_hw:
        photo_info = f" 📸({len(photos)})" if photos else ""
        text += f"📅 {format_date_with_weekday(date)} | 📚 {subject}{photo_info}\n"
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

    try:
        await query.message.delete()
    except Exception as e:
        print(f"❌ Не удалось удалить сообщение меню: {e}")

    await query.message.answer(
        "📚 Просмотр домашних задиний для 10А класса\n\n"
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
    Отображение всех ДЗ на указанную дату
    
    Args:
        query: CallbackQuery объект
        date: Дата в формате ДД.ММ.ГГГГ
        date_label: Текстовая метка даты (например, "Сегодня" или "Завтра")
    """
    await clear_last_solution_messages(query, state)
    await clear_last_homework_photos(query, state)
    homework_dict = await db_call(db.get_homework_by_date, date)
    formatted_date = format_date_with_weekday(date, mark_today=True)
    
    if not homework_dict:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="student_view")],
        ])
        if date_label in ("Сегодня", "Завтра"):
            no_hw_text = f"📭 Нет заданий на {date_label.lower()} ({formatted_date})"
        else:
            no_hw_text = f"📭 Нет заданий на {formatted_date}"
        await query.message.edit_text(
            no_hw_text,
            reply_markup=keyboard
        )
        await query.answer()
        return
    
    # Отправляем первое сообщение с меню
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="student_view")],
    ])
    
    if date_label in ("Сегодня", "Завтра"):
        title_text = f"📚 Домашние задания на {date_label.lower()} ({formatted_date}):\n\n"
    else:
        title_text = f"📚 Домашние задания на {formatted_date}:\n\n"

    await query.message.edit_text(
        f"{title_text}Найдено заданий: {len(homework_dict)}",
        reply_markup=keyboard
    )
    
    # Отправляем каждое задание отдельным сообщением
    photo_message_ids = []

    for subject, (text, photos) in homework_dict.items():
        homework_text = (
            f"📚 {subject}\n"
            f"📅 {formatted_date}\n\n"
            f"📝 Задание:\n{text}"
        )
        reply_markup = None
        if subject == "Алгебра":
            reply_markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔎 Найти решение", callback_data="find_solution_algebra")],
            ])
        elif subject == "Геометрия":
            reply_markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔎 Найти решение", callback_data="find_solution_geometry")],
            ])

        if photos:
            # Если есть фото, отправляем первое фото с текстом
            try:
                first_message = await query.message.answer_photo(
                    photo=photos[0],
                    caption=homework_text[:1024],  # Ограничение Telegram для caption
                    reply_markup=reply_markup
                )
                photo_message_ids.append(first_message.message_id)
                
                # Отправляем остальные фото отдельно
                for photo_id in photos[1:]:
                    try:
                        extra_message = await query.message.answer_photo(photo=photo_id)
                        photo_message_ids.append(extra_message.message_id)
                    except Exception as e:
                        print(f"❌ Ошибка при отправке фото: {e}")
            except Exception as e:
                # Если не удалось отправить фото, отправляем текст
                print(f"❌ Ошибка при отправке фото: {e}")
                await query.message.answer(homework_text)
        else:
            # Если фото нет, отправляем только текст
            await query.message.answer(homework_text, reply_markup=reply_markup)
    
    await state.update_data(last_homework_photo_ids=photo_message_ids)
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


@router.callback_query(F.data.startswith("view_subject_"))
async def view_homework(query: CallbackQuery, state: FSMContext):
    """Просмотр ДЗ по предмету"""
    data_parts = query.data.replace("view_subject_", "").rsplit("_", 1)
    date = data_parts[0]
    subject = data_parts[1]
    
    await clear_last_solution_messages(query, state)
    await clear_last_homework_photos(query, state)
    homework = await db_call(db.get_homework, date, subject)
    
    if not homework:
        await query.answer("❌ ДЗ не найдено", show_alert=True)
        return
    
    text, photos = homework
    
    buttons = []
    if subject == "Алгебра":
        buttons.append([InlineKeyboardButton(text="🔎 Найти решение", callback_data="find_solution_algebra")])
    elif subject == "Геометрия":
        buttons.append([InlineKeyboardButton(text="🔎 Найти решение", callback_data="find_solution_geometry")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"view_date_{date}")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    await query.message.edit_text(
        f"📚 {subject}\n"
        f"📅 {format_date_with_weekday(date, mark_today=True)}\n\n"
        f"📝 {text}",
        reply_markup=keyboard
    )
    
    photo_message_ids = []

    if photos:
        for photo_id in photos:
            try:
                sent_message = await query.message.answer_photo(photo_id)
                photo_message_ids.append(sent_message.message_id)
            except Exception as e:
                print(f"❌ Ошибка при отправке фото: {e}")

    await state.update_data(last_homework_photo_ids=photo_message_ids)
    
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

    try:
        await query.message.delete()
    except Exception as e:
        print(f"❌ Не удалось удалить сообщение меню: {e}")

    await query.message.answer(
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
