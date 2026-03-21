import asyncio
from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from utils import db, db_call, safe_edit_or_answer, clear_last_homework_photos, clear_last_solution_messages, clear_all_extra_messages
from states import FeedbackStates
from keyboards import create_month_calendar_keyboard, create_schedule_subject_buttons, get_weekday_from_date, format_date_with_weekday
from config import ADMIN_ID

router = Router()

@router.callback_query(F.data == "student_view")
async def student_view(query: CallbackQuery, state: FSMContext):
    await query.answer()
    # Запускаем очистку и обновление стейта параллельно
    asyncio.create_task(state.update_data(schedule_back_callback=None))
    asyncio.create_task(clear_all_extra_messages(query, state, exclude_id=query.message.message_id))
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 На сегодня", callback_data="view_today")],
        [InlineKeyboardButton(text="📅 На завтра", callback_data="view_tomorrow")],
        [InlineKeyboardButton(text="📅 Выбрать дату", callback_data="view_select_date")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")],
    ])
    await safe_edit_or_answer(query.message, "📚 Просмотр домашних заданий для 10А класса\n\nВыберите действие:", reply_markup=keyboard)

@router.callback_query(F.data == "view_select_date")
async def view_select_date(query: CallbackQuery, state: FSMContext):
    await query.answer()
    asyncio.create_task(state.update_data(schedule_back_callback="view_select_date"))
    today = datetime.now()
    keyboard = create_month_calendar_keyboard(today.year, today.month, "student_view")
    await query.message.edit_text("📅 Выберите дату в календаре:", reply_markup=keyboard)

@router.callback_query(F.data.startswith("view_calendar_"))
async def view_calendar_month(query: CallbackQuery, state: FSMContext):
    await query.answer()
    await state.update_data(schedule_back_callback="view_select_date")
    payload = query.data.replace("view_calendar_", "", 1)
    try:
        year_str, month_str = payload.split("_", 1)
        year = int(year_str)
        month = int(month_str)
    except Exception:
        await query.answer("❌ Ошибка календаря", show_alert=True)
        return
    keyboard = create_month_calendar_keyboard(year, month, "student_view")
    await query.message.edit_text("📅 Выберите дату в календаре:", reply_markup=keyboard)

async def display_homework_for_date(query: CallbackQuery, state: FSMContext, date: str, date_label: str):
    weekday = get_weekday_from_date(date)
    if weekday is not None and weekday >= 5:
        await query.answer("😴 В этот день уроков нет!", show_alert=True)
        return
    await query.answer()
    # Запускаем очистку и получение данных из БД одновременно
    clear_task = asyncio.create_task(clear_all_extra_messages(query, state, exclude_id=query.message.message_id))
    hw_task = asyncio.create_task(db_call(db.get_homework_by_date, date))
    
    formatted_date = format_date_with_weekday(date, mark_today=True)
    asyncio.create_task(state.update_data(current_view_date=date))
    
    homework_dict = await hw_task
    await clear_task
    if date_label in ("Сегодня", "Завтра"):
        title = f"📚 Расписание на {date_label.lower()} ({formatted_date})"
    else:
        title = f"📚 Расписание на {formatted_date}"
    data = await state.get_data()
    back_cb = data.get("schedule_back_callback") or "student_view"
    keyboard = create_schedule_subject_buttons(date, prefix=f"stview_{date}_", back_callback=back_cb, homework_dict=homework_dict)
    await safe_edit_or_answer(query.message, f"{title}\n\n✅ — задание есть\nНажмите на предмет, чтобы посмотреть ДЗ:", reply_markup=keyboard)

@router.callback_query(F.data == "view_today")
async def view_today_homework(query: CallbackQuery, state: FSMContext):
    await state.update_data(schedule_back_callback="student_view")
    today = datetime.now().strftime("%d.%m.%Y")
    await display_homework_for_date(query, state, today, "Сегодня")

@router.callback_query(F.data == "view_tomorrow")
async def view_tomorrow_homework(query: CallbackQuery, state: FSMContext):
    await state.update_data(schedule_back_callback="student_view")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d.%m.%Y")
    await display_homework_for_date(query, state, tomorrow, "Завтра")

@router.callback_query(F.data.startswith("view_date_"))
async def view_date_selected(query: CallbackQuery, state: FSMContext):
    date = query.data.replace("view_date_", "")
    await display_homework_for_date(query, state, date, date)

@router.callback_query(F.data.startswith("stview_"))
async def view_subject_from_schedule(query: CallbackQuery, state: FSMContext):
    payload = query.data.replace("stview_", "", 1)
    date = payload[:10]
    subject = payload[11:]
    await query.answer()
    # Запускаем три задачи параллельно: зачистку двух типов сообщений и запрос в БД
    s_clear = asyncio.create_task(clear_last_solution_messages(query, state))
    p_clear = asyncio.create_task(clear_last_homework_photos(query, state))
    hw_task = asyncio.create_task(db_call(db.get_homework, date, subject))
    
    homework = await hw_task
    if not homework:
        await s_clear
        await p_clear
        return await query.message.answer(f"📭 По предмету {subject} задание не задано", show_alert=True)
    
    text, photos, is_textbook = homework
    await s_clear
    await p_clear
    buttons = []
    if is_textbook:
        if subject == "Алгебра": buttons.append([InlineKeyboardButton(text="🔎 Найти решение", callback_data="find_solution_algebra")])
        elif subject == "Геометрия": buttons.append([InlineKeyboardButton(text="🔎 Найти решение", callback_data="find_solution_geometry")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"view_date_{date}")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    photo_message_ids = []
    if photos:
        header_msg = await safe_edit_or_answer(query.message, f"📚 {subject}\n📅 {format_date_with_weekday(date, mark_today=True)}")
        if header_msg: photo_message_ids.append(header_msg.message_id)

        from aiogram.utils.media_group import MediaGroupBuilder
        photo_group = MediaGroupBuilder()
        doc_group = MediaGroupBuilder()
        has_photos, has_docs = False, False

        for photo_id in photos:
            if isinstance(photo_id, str) and photo_id.startswith("pdf:"):
                doc_group.add_document(media=photo_id[4:])
                has_docs = True
            else:
                photo_group.add_photo(media=photo_id)
                has_photos = True

        if has_photos:
            try:
                msgs = await query.bot.send_media_group(chat_id=query.message.chat.id, media=photo_group.build())
                photo_message_ids.extend(m.message_id for m in msgs)
            except Exception: pass

        if has_docs:
            try:
                msgs = await query.bot.send_media_group(chat_id=query.message.chat.id, media=doc_group.build())
                photo_message_ids.extend(m.message_id for m in msgs)
            except Exception: pass

        bottom_msg = await query.message.answer(f"📝 {text}", reply_markup=keyboard)
        photo_message_ids.append(bottom_msg.message_id)
    else:
        await safe_edit_or_answer(query.message, f"📚 {subject}\n📅 {format_date_with_weekday(date, mark_today=True)}\n\n📝 {text}", reply_markup=keyboard)

    await state.update_data(last_homework_message_ids=photo_message_ids)

@router.callback_query(F.data.startswith("view_subject_"))
async def view_homework(query: CallbackQuery, state: FSMContext):
    data_parts = query.data.replace("view_subject_", "").rsplit("_", 1)
    date, subject = data_parts[0], data_parts[1]
    await query.answer()
    # Параллельная зачистка и БД
    s_clear = asyncio.create_task(clear_last_solution_messages(query, state))
    p_clear = asyncio.create_task(clear_last_homework_photos(query, state))
    hw_task = asyncio.create_task(db_call(db.get_homework, date, subject))
    
    homework = await hw_task
    if not homework:
        await s_clear
        await p_clear
        return await query.message.answer("❌ ДЗ не найдено", show_alert=True)
    
    text, photos, is_textbook = homework
    await s_clear
    await p_clear
    buttons = []
    if is_textbook:
        if subject == "Алгебра": buttons.append([InlineKeyboardButton(text="🔎 Найти решение", callback_data="find_solution_algebra")])
        elif subject == "Геометрия": buttons.append([InlineKeyboardButton(text="🔎 Найти решение", callback_data="find_solution_geometry")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"view_date_{date}")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    photo_message_ids = []
    if photos:
        header_msg = await safe_edit_or_answer(query.message, f"📚 {subject}\n📅 {format_date_with_weekday(date, mark_today=True)}")
        if header_msg: photo_message_ids.append(header_msg.message_id)

        from aiogram.utils.media_group import MediaGroupBuilder
        photo_group = MediaGroupBuilder()
        doc_group = MediaGroupBuilder()
        has_photos, has_docs = False, False

        for photo_id in photos:
            if isinstance(photo_id, str) and photo_id.startswith("pdf:"):
                doc_group.add_document(media=photo_id[4:])
                has_docs = True
            else:
                photo_group.add_photo(media=photo_id)
                has_photos = True

        if has_photos:
            try:
                msgs = await query.bot.send_media_group(chat_id=query.message.chat.id, media=photo_group.build())
                photo_message_ids.extend(m.message_id for m in msgs)
            except Exception: pass

        if has_docs:
            try:
                msgs = await query.bot.send_media_group(chat_id=query.message.chat.id, media=doc_group.build())
                photo_message_ids.extend(m.message_id for m in msgs)
            except Exception: pass

        bottom_msg = await query.message.answer(f"📝 {text}", reply_markup=keyboard)
        photo_message_ids.append(bottom_msg.message_id)
    else:
        await safe_edit_or_answer(query.message, f"📚 {subject}\n📅 {format_date_with_weekday(date, mark_today=True)}\n\n📝 {text}", reply_markup=keyboard)

    await state.update_data(last_homework_message_ids=photo_message_ids)

@router.callback_query(F.data == "show_feedback")
async def show_feedback(query: CallbackQuery, state: FSMContext):
    await query.answer()
    await state.set_state(FeedbackStates.waiting_for_feedback)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_feedback")]])
    await safe_edit_or_answer(query.message, "💌 <b>Пожелания и идеи</b>\n\nЗдесь ты можешь оставить своё пожелание или идею для улучшения бота.\n", reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(F.data == "cancel_feedback", FeedbackStates.waiting_for_feedback)
async def cancel_feedback(query: CallbackQuery, state: FSMContext):
    await query.answer()
    await state.clear()
    kb_buttons = [
        [InlineKeyboardButton(text="📚 Мои ДЗ", callback_data="student_view")],
        [InlineKeyboardButton(text="💌 Пожелания и идеи", callback_data="show_feedback")],
        [InlineKeyboardButton(text="🕵️ Я только зашёл, что делать?", callback_data="show_instructions")],
    ]
    if query.from_user.id == ADMIN_ID:
        kb_buttons.insert(0, [InlineKeyboardButton(text="👑 Админ панель", callback_data="admin_auth")])
    await safe_edit_or_answer(query.message, "👋 Главное меню\n\nВыбери действие:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons))

@router.message(FeedbackStates.waiting_for_feedback)
async def process_feedback(message: Message, state: FSMContext):
    feedback_text = (message.text or "").strip()
    if not feedback_text:
        await message.answer("⚠️ Пожалуйста, напиши текстовое сообщение.")
        return
    await state.clear()
    await db_call(db.add_feedback, message.from_user.id, message.from_user.username or "", message.from_user.first_name or "", message.from_user.last_name or "", feedback_text)
    await message.answer("✅ Спасибо! Твоё пожелание отправлено.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ В главное меню", callback_data="back_to_menu")]]))
    try:
        feedback_count = await db_call(db.get_feedback_count)
        await message.bot.send_message(ADMIN_ID, f"💌 Новое пожелание от ученика!\nВсего непрочитанных: {feedback_count}\n\nОткрой → Админ панель → Пожелания учеников")
    except Exception: pass
