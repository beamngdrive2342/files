import asyncio
import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ErrorEvent
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext

from config import ADMIN_ID, ADMIN_PASSWORD
from states import AdminAuthStates
from utils import db, db_call, safe_edit_or_answer, clear_all_extra_messages

logger = logging.getLogger("homework_handlers")
router = Router()

@router.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        is_approved = await db_call(db.is_user_approved, user_id)
        if not is_approved:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📝 Подать заявку на вступление", callback_data="apply_for_access")]
            ])
            await message.answer(
                "👋 Привет! Это закрытый бот с домашними заданиями.\n\n"
                "Чтобы получить доступ к расписанию и заданиям, тебе нужно подать заявку.",
                reply_markup=keyboard
            )
            return

    # Регистрируем один раз (убрали дублирование)
    asyncio.create_task(db_call(
        db.register_user,
        user_id=user_id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        is_approved=1
    ))
    
    if message.from_user.id == ADMIN_ID:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👑 Админ панель", callback_data="admin_auth")],
            [InlineKeyboardButton(text="📚 Мои ДЗ", callback_data="student_view")],
            [InlineKeyboardButton(text="💌 Пожелания и идеи", callback_data="show_feedback")],
            [InlineKeyboardButton(text="🕵️ Я только зашёл, что делать?", callback_data="show_instructions")],
        ])
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📚 Мои ДЗ", callback_data="student_view")],
            [InlineKeyboardButton(text="💌 Пожелания и идеи", callback_data="show_feedback")],
            [InlineKeyboardButton(text="🕵️ Я только зашёл, что делать?", callback_data="show_instructions")],
        ])

    await message.answer(
        "👋 Привет! Я бот с домашними заданиями для 10А класса.\n\nВыбери действие:",
        reply_markup=keyboard
    )

@router.message(Command("help"))
async def cmd_help(message: Message):
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

@router.callback_query(F.data == "apply_for_access")
async def apply_for_access(query: CallbackQuery):
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.username
    first_name = query.from_user.first_name
    
    await db_call(
        db.register_user,
        user_id=user_id,
        username=username,
        first_name=first_name,
        is_approved=0
    )
    
    name_parts = [first_name or ""]
    if query.from_user.last_name:
        name_parts.append(query.from_user.last_name)
    sender_name = " ".join(name_parts).strip() or "Неизвестный"
    username_str = f" (@{username})" if username else ""
    
    admin_text = (
        f"🔔 <b>Новая заявка на вступление!</b>\n\n"
        f"👤 От: {sender_name}{username_str}\n"
        f"🆔 ID: <code>{user_id}</code>\n\nРазрешить доступ?"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принять", callback_data=f"approve_{user_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{user_id}")
        ]
    ])
    
    try:
        await query.bot.send_message(chat_id=ADMIN_ID, text=admin_text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Не удалось отправить заявку админу: {e}")
        
    await query.message.edit_text(
        "✅ Твоя заявка отправлена администратору!\n"
        "Ожидай одобрения. После этого мы пришлём тебе уведомление."
    )

@router.callback_query(F.data.startswith("approve_"))
async def approve_user_callback(query: CallbackQuery):
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа!", show_alert=True)
        return
    try:
        user_id = int(query.data.split("_")[1])
    except ValueError:
        await query.answer("❌ Ошибка ID", show_alert=True)
        return
        
    await db_call(db.set_user_approved, user_id, 1)
    
    try:
        await query.bot.send_message(
            chat_id=user_id, 
            text="🎉 <b>Твоя заявка одобрена!</b>\n\nТеперь тебе доступно главное меню. Нажми /start чтобы начать.", 
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning(f"Не удалось отправить уведомление об одобрении {user_id}: {e}")
        
    await query.message.edit_text(f"✅ Заявка пользователя <code>{user_id}</code> одобрена.", parse_mode="HTML")
    await query.answer("Вы одобрили заявку!")

@router.callback_query(F.data.startswith("reject_"))
async def reject_user_callback(query: CallbackQuery):
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа!", show_alert=True)
        return
    try:
        user_id = int(query.data.split("_")[1])
    except ValueError:
        await query.answer("❌ Ошибка ID", show_alert=True)
        return
        
    await db_call(db.delete_user, user_id)
    try:
        await query.bot.send_message(
            chat_id=user_id, 
            text="❌ К сожалению, твоя заявка на вступление была отклонена.", 
            parse_mode="HTML"
        )
    except Exception:
        pass
        
    await query.message.edit_text(f"❌ Заявка пользователя <code>{user_id}</code> отклонена.", parse_mode="HTML")
    await query.answer("Вы отклонили заявку.")


@router.callback_query(F.data == "admin_auth")
async def admin_auth(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ У вас нет доступа!", show_alert=True)
        return
    await query.answer()
    await state.set_state(AdminAuthStates.waiting_for_password)
    cancel_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="pwd_cancel")],
    ])
    await query.message.edit_text("🔐 Введите пароль администратора:", reply_markup=cancel_keyboard)
    await state.update_data(
        pwd_prompt_message_id=query.message.message_id,
        pwd_prompt_chat_id=query.message.chat.id
    )

@router.callback_query(F.data == "pwd_cancel", AdminAuthStates.waiting_for_password)
async def cancel_password_input(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != ADMIN_ID:
        return
    await query.answer()
    await state.clear()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👑 Админ панель", callback_data="admin_auth")],
        [InlineKeyboardButton(text="📚 Мои ДЗ", callback_data="student_view")],
        [InlineKeyboardButton(text="💌 Пожелания и идеи", callback_data="show_feedback")],
        [InlineKeyboardButton(text="🕵️ Я только зашёл, что делать?", callback_data="show_instructions")],
    ])
    await query.message.edit_text("👋 Главное меню\n\nВыбери действие:", reply_markup=keyboard)

@router.message(AdminAuthStates.waiting_for_password)
async def handle_password_input(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    entered = (message.text or "").strip()
    data = await state.get_data()
    try:
        await message.delete()
    except Exception:
        pass
    prompt_message_id = data.get("pwd_prompt_message_id")
    prompt_chat_id = data.get("pwd_prompt_chat_id")
    if prompt_message_id and prompt_chat_id:
        try:
            await message.bot.delete_message(chat_id=prompt_chat_id, message_id=prompt_message_id)
        except Exception:
            pass

    if entered == ADMIN_PASSWORD:
        await state.clear()
        feedback_count = await db_call(db.get_feedback_count)
        fb_label = f"💌 Пожелания учеников" + (f" ({feedback_count}) 🔴" if feedback_count > 0 else "")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить ДЗ", callback_data="add_hw")],
            [InlineKeyboardButton(text="✏️ Редактировать", callback_data="edit_hw")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data="delete_hw")],
            [InlineKeyboardButton(text="📋 Все ДЗ", callback_data="view_all_hw")],
            [InlineKeyboardButton(text=fb_label, callback_data="view_feedbacks")],
            [InlineKeyboardButton(text="👥 Пользователи", callback_data="view_users")],
            [InlineKeyboardButton(text="◀️ Выход в меню", callback_data="back_to_menu")],
        ])
        await message.answer("✅ Добро пожаловать, администратор!\n\nВыберите действие:", reply_markup=keyboard)
    else:
        cancel_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="pwd_cancel")],
        ])
        new_prompt = await message.answer(
            "🔐 Введите пароль администратора:\n\n❌ Неверный пароль, попробуйте ещё раз:",
            reply_markup=cancel_keyboard
        )
        await state.update_data(
            pwd_prompt_message_id=new_prompt.message_id,
            pwd_prompt_chat_id=new_prompt.chat.id
        )

@router.callback_query(F.data == "show_instructions")
async def show_instructions(query: CallbackQuery):
    await query.answer()
    instruction_text = (
        "👋 Добро пожаловать в бот для домашних заданий!\n"
        "Здесь ты всегда найдёшь актуальные задания, расписание и решения — всё в одном месте. Давай разберёмся, как всё работает!\n\n"
        "📚 <b>Домашние задания</b>\n"
        "Нажми «📚 Мои ДЗ» и выбери удобный вариант:\n"
        "- <b>На сегодня</b> — задания на текущий день\n"
        "- <b>На завтра</b> — задания на следующий день\n"
        "- <b>Выбрать дату</b> — откроется календарь для любой даты\n\n"
        "🗓 <b>Расписание и предметы</b>\n"
        "После выбора даты ты увидишь список предметов по расписанию.\n"
        "✅ — означает, что ДЗ по предмету уже добавлено.\n"
        "Просто нажми на нужный предмет, чтобы посмотреть задание.\n\n"
        "🔎 <b>Поиск решений</b>\n"
        "Если задание по Алгебре или Геометрии из учебника — появится кнопка «🔎 Найти решение». Нажми на неё, введи номер задания и получи готовое решение из учебника!\n\n"
        "🏖 <b>Выходные дни</b>\n"
        "В субботу и воскресенье уроков нет — бот сообщит об этом при выборе таких дат. Отдыхай спокойно! 😄\n\n"
        "💬 <b>Пожелания</b>\n"
        "Есть идея или что-то не работает как надо? Нажми «💌 Пожелания и идеи» и напиши — автор бота читает каждое сообщение и старается сделать бот лучше для всех. Твоё мнение реально влияет на развитие! 🙌"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")],
    ])
    await safe_edit_or_answer(query.message, instruction_text, reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(query: CallbackQuery, state: FSMContext):
    await query.answer()
    # Очистка и сброс состояния параллельно
    clear_task = asyncio.create_task(clear_all_extra_messages(query, state, exclude_id=query.message.message_id))
    await state.clear()
    await clear_task
    
    if query.from_user.id == ADMIN_ID:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👑 Админ панель", callback_data="admin_auth")],
            [InlineKeyboardButton(text="📚 Мои ДЗ", callback_data="student_view")],
            [InlineKeyboardButton(text="💌 Пожелания и идеи", callback_data="show_feedback")],
            [InlineKeyboardButton(text="🕵️ Я только зашёл, что делать?", callback_data="show_instructions")],
        ])
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📚 Мои ДЗ", callback_data="student_view")],
            [InlineKeyboardButton(text="💌 Пожелания и идеи", callback_data="show_feedback")],
            [InlineKeyboardButton(text="🕵️ Я только зашёл, что делать?", callback_data="show_instructions")],
        ])
    await safe_edit_or_answer(query.message, "👋 Главное меню\n\nВыбери действие:", reply_markup=keyboard)
    await query.answer()

@router.callback_query(F.data == "noop")
async def noop_callback(query: CallbackQuery):
    await query.answer()

@router.errors()
async def handle_router_errors(event: ErrorEvent):
    exc = event.exception
    if isinstance(exc, TelegramBadRequest):
        text = str(exc).lower()
        if ("query is too old" in text or "query id is invalid" in text or "response timeout expired" in text):
            logger.info("⚠️ Просроченный callback-query проигнорирован.")
            return True
    return False
