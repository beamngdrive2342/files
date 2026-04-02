from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import ADMIN_ID
from keyboards import (
    BROADCAST_PAGE_SIZE,
    build_add_content_keyboard,
    build_admin_panel_keyboard,
    build_broadcast_message_keyboard,
    build_broadcast_preview_keyboard,
    build_broadcast_recipients_keyboard,
    build_broadcast_text_keyboard,
    build_edit_content_keyboard,
    create_month_calendar_keyboard,
    create_schedule_subject_buttons,
    format_date_with_weekday,
)
from states import AddHomeworkStates, BroadcastStates, DeleteHomeworkStates, EditHomeworkStates
from utils import SUBJECTS_WITH_SOLUTIONS, db, db_call, safe_edit_or_answer, send_broadcast_to_users


router = Router()


def _is_admin(query_or_message: CallbackQuery | Message) -> bool:
    return query_or_message.from_user.id == ADMIN_ID


async def render_admin_panel(message: Message, state: FSMContext, greeting: str | None = None):
    await state.clear()
    feedback_count = await db_call(db.get_feedback_count)
    text = "✅ Админ панель\n\nВыберите действие:"
    if greeting:
        text = f"{greeting}\n\n{text}"
    await safe_edit_or_answer(message, text, reply_markup=build_admin_panel_keyboard(feedback_count))


def _build_broadcast_users(raw_users: list[tuple[int, str | None, str | None]]) -> list[dict]:
    users = []
    for user_id, username, first_name in raw_users:
        users.append(
            {
                "user_id": int(user_id),
                "username": username or "",
                "first_name": first_name or "",
            }
        )
    return users


async def render_broadcast_recipients(message: Message, state: FSMContext):
    data = await state.get_data()
    users = data.get("broadcast_users", [])
    selected_ids = set(data.get("broadcast_selected_ids", []))
    page = int(data.get("broadcast_page", 0))
    total_pages = max(1, (len(users) + BROADCAST_PAGE_SIZE - 1) // BROADCAST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    await state.update_data(broadcast_page=page)

    text = (
        "📣 Рассылка\n\n"
        "Шаг 2 из 3. Выберите получателей.\n"
        f"Одобренных пользователей: {len(users)}\n"
        f"Выбрано: {len(selected_ids)}\n"
        f"Страница: {page + 1}/{total_pages}"
    )
    await safe_edit_or_answer(
        message,
        text,
        reply_markup=build_broadcast_recipients_keyboard(users, selected_ids, page),
    )


async def render_broadcast_preview(message: Message, state: FSMContext):
    data = await state.get_data()
    selected_ids = data.get("broadcast_selected_ids", [])
    broadcast_text = (data.get("broadcast_text") or "").strip()
    preview_text = (
        "📣 Предпросмотр рассылки\n\n"
        f"Получателей: {len(selected_ids)}\n\n"
        "Текст сообщения:\n\n"
        f"{broadcast_text}\n\n"
        "Под сообщением будет кнопка:\n"
        "• Мои ДЗ"
    )
    await safe_edit_or_answer(message, preview_text, reply_markup=build_broadcast_preview_keyboard())


@router.callback_query(F.data == "admin_panel")
async def show_admin_panel(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        await query.answer("❌ У вас нет доступа!", show_alert=True)
        return
    await render_admin_panel(query.message, state)
    await query.answer()


@router.callback_query(F.data == "broadcast_menu")
async def broadcast_menu(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        await query.answer("❌ У вас нет доступа!", show_alert=True)
        return
    await state.clear()
    await state.set_state(BroadcastStates.waiting_for_text)
    await state.update_data(broadcast_selected_ids=[], broadcast_page=0)
    await safe_edit_or_answer(
        query.message,
        "📣 Рассылка\n\nШаг 1 из 3. Отправьте текст напоминания для учеников.",
        reply_markup=build_broadcast_text_keyboard(),
    )
    await query.answer()


@router.message(BroadcastStates.waiting_for_text)
async def broadcast_receive_text(message: Message, state: FSMContext):
    if not _is_admin(message):
        return
    broadcast_text = (message.text or "").strip()
    if not broadcast_text:
        await message.answer("⚠️ Нужен обычный текст для рассылки.", reply_markup=build_broadcast_text_keyboard())
        return

    existing_data = await state.get_data()
    raw_users = await db_call(db.get_approved_users_for_broadcast)
    users = _build_broadcast_users(raw_users)
    if not users:
        await state.clear()
        await message.answer("⚠️ Нет одобренных пользователей для рассылки.")
        return

    valid_user_ids = {int(user["user_id"]) for user in users}
    selected_ids = [
        user_id for user_id in existing_data.get("broadcast_selected_ids", []) if user_id in valid_user_ids
    ]

    await state.set_state(BroadcastStates.waiting_for_recipients)
    await state.update_data(
        broadcast_text=broadcast_text,
        broadcast_users=users,
        broadcast_selected_ids=selected_ids,
        broadcast_page=existing_data.get("broadcast_page", 0),
    )
    await render_broadcast_recipients(message, state)


@router.callback_query(F.data == "broadcast_cancel")
async def broadcast_cancel(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        return
    await render_admin_panel(query.message, state)
    await query.answer("Рассылка отменена")


@router.callback_query(F.data.startswith("broadcast_toggle_"), BroadcastStates.waiting_for_recipients)
async def broadcast_toggle_recipient(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        return
    user_id = int(query.data.replace("broadcast_toggle_", "", 1))
    data = await state.get_data()
    selected_ids = set(data.get("broadcast_selected_ids", []))
    if user_id in selected_ids:
        selected_ids.remove(user_id)
    else:
        selected_ids.add(user_id)
    await state.update_data(broadcast_selected_ids=sorted(selected_ids))
    await render_broadcast_recipients(query.message, state)
    await query.answer()


@router.callback_query(F.data.startswith("broadcast_page_"), BroadcastStates.waiting_for_recipients)
async def broadcast_change_page(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        return
    page = int(query.data.replace("broadcast_page_", "", 1))
    data = await state.get_data()
    users = data.get("broadcast_users", [])
    total_pages = max(1, (len(users) + BROADCAST_PAGE_SIZE - 1) // BROADCAST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    await state.update_data(broadcast_page=page)
    await render_broadcast_recipients(query.message, state)
    await query.answer()


@router.callback_query(F.data == "broadcast_select_all", BroadcastStates.waiting_for_recipients)
async def broadcast_select_all(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        return
    data = await state.get_data()
    users = data.get("broadcast_users", [])
    await state.update_data(broadcast_selected_ids=[int(user["user_id"]) for user in users])
    await render_broadcast_recipients(query.message, state)
    await query.answer("Выбраны все пользователи")


@router.callback_query(F.data == "broadcast_clear_all", BroadcastStates.waiting_for_recipients)
async def broadcast_clear_all(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        return
    await state.update_data(broadcast_selected_ids=[])
    await render_broadcast_recipients(query.message, state)
    await query.answer("Выбор очищен")


@router.callback_query(F.data == "broadcast_preview", BroadcastStates.waiting_for_recipients)
async def broadcast_preview(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        return
    data = await state.get_data()
    selected_ids = data.get("broadcast_selected_ids", [])
    if not selected_ids:
        await query.answer("⚠️ Выберите хотя бы одного получателя.", show_alert=True)
        return
    await state.set_state(BroadcastStates.waiting_for_confirmation)
    await render_broadcast_preview(query.message, state)
    await query.answer()


@router.callback_query(F.data == "broadcast_back_recipients", BroadcastStates.waiting_for_confirmation)
async def broadcast_back_recipients(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        return
    await state.set_state(BroadcastStates.waiting_for_recipients)
    await render_broadcast_recipients(query.message, state)
    await query.answer()


@router.callback_query(F.data == "broadcast_edit_text", BroadcastStates.waiting_for_confirmation)
async def broadcast_edit_text(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        return
    await state.set_state(BroadcastStates.waiting_for_text)
    await safe_edit_or_answer(
        query.message,
        "📣 Рассылка\n\nОтправьте новый текст рассылки.",
        reply_markup=build_broadcast_text_keyboard(),
    )
    await query.answer()


@router.callback_query(F.data == "broadcast_test", BroadcastStates.waiting_for_confirmation)
async def broadcast_test(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        return
    data = await state.get_data()
    broadcast_text = (data.get("broadcast_text") or "").strip()
    await query.bot.send_message(
        ADMIN_ID,
        broadcast_text,
        reply_markup=build_broadcast_message_keyboard(),
    )
    await query.answer("Тест отправлен вам")


@router.callback_query(F.data == "broadcast_send", BroadcastStates.waiting_for_confirmation)
async def broadcast_send(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        return
    data = await state.get_data()
    selected_ids = data.get("broadcast_selected_ids", [])
    broadcast_text = (data.get("broadcast_text") or "").strip()
    if not broadcast_text:
        await query.answer("⚠️ Текст рассылки пуст.", show_alert=True)
        return
    if not selected_ids:
        await query.answer("⚠️ Получатели не выбраны.", show_alert=True)
        return

    stats = await send_broadcast_to_users(
        bot=query.bot,
        user_ids=selected_ids,
        text=broadcast_text,
        reply_markup=build_broadcast_message_keyboard(),
    )
    await state.clear()

    result_text = (
        "✅ Рассылка завершена\n\n"
        f"Всего получателей: {stats['total']}\n"
        f"Успешно: {stats['success_count']}\n"
        f"Ошибок: {stats['failed_count']}"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📣 Новая рассылка", callback_data="broadcast_menu")],
            [InlineKeyboardButton(text="🔙 В админ панель", callback_data="admin_panel")],
        ]
    )
    await safe_edit_or_answer(query.message, result_text, reply_markup=keyboard)
    await query.answer()


@router.callback_query(F.data == "add_hw")
async def start_add_hw(query: CallbackQuery, state: FSMContext):
    await state.set_state(AddHomeworkStates.waiting_for_date)
    today = datetime.now()
    keyboard = create_month_calendar_keyboard(today.year, today.month, "admin_panel", "add_date_", "add_calendar_")
    await query.message.edit_text("📅 Выберите дату в календаре:", reply_markup=keyboard)
    await query.answer()


@router.callback_query(F.data.startswith("add_calendar_"), AddHomeworkStates.waiting_for_date)
async def add_calendar_month(query: CallbackQuery):
    payload = query.data.replace("add_calendar_", "", 1)
    try:
        year_str, month_str = payload.split("_", 1)
        year, month = int(year_str), int(month_str)
    except Exception:
        await query.answer("❌ Ошибка календаря", show_alert=True)
        return
    keyboard = create_month_calendar_keyboard(year, month, "admin_panel", "add_date_", "add_calendar_")
    await query.message.edit_text("📅 Выберите дату в календаре:", reply_markup=keyboard)
    await query.answer()


@router.callback_query(F.data.startswith("add_date_"), AddHomeworkStates.waiting_for_date)
async def add_select_date(query: CallbackQuery, state: FSMContext):
    date = query.data.replace("add_date_", "")
    await state.update_data(date=date)
    await state.set_state(AddHomeworkStates.waiting_for_subject)
    homework_dict = await db_call(db.get_homework_by_date, date)
    keyboard = create_schedule_subject_buttons(date, "add_subject_", "add_hw", homework_dict=homework_dict)
    await query.message.edit_text(
        f"📚 Выберите предмет для даты {format_date_with_weekday(date)}:",
        reply_markup=keyboard,
    )
    await query.answer()


@router.callback_query(F.data.startswith("add_subject_"), AddHomeworkStates.waiting_for_subject)
async def add_select_subject(query: CallbackQuery, state: FSMContext):
    subject = query.data.replace("add_subject_", "")
    data = await state.get_data()
    date = data.get("date")
    if await db_call(db.homework_exists, date, subject):
        await query.answer(
            f"⚠️ ДЗ по {subject} на {format_date_with_weekday(date)} уже существует!",
            show_alert=True,
        )
        return

    await state.update_data(subject=subject, photos=[], text_parts=[])
    if subject in SUBJECTS_WITH_SOLUTIONS:
        await state.set_state(AddHomeworkStates.waiting_for_source_type)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📘 Из учебника", callback_data="add_source_textbook")],
                [InlineKeyboardButton(text="📝 Другое (доска...)", callback_data="add_source_other")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="add_back_to_subject_from_source")],
            ]
        )
        await query.message.edit_text(
            f"📚 {subject} — откуда задание?\n📘 Из учебника\n📝 Другое",
            reply_markup=keyboard,
        )
        await query.answer()
        return

    await state.update_data(is_textbook=False)
    await state.set_state(AddHomeworkStates.waiting_for_content)
    await query.message.edit_text(
        f"📝 Добавление ДЗ:\nДата: {format_date_with_weekday(date)}\nПредмет: {subject}\n\n"
        "Добавляйте текст, фото и PDF в любом порядке:",
        reply_markup=build_add_content_keyboard(),
    )
    await query.answer()


@router.callback_query(F.data == "add_back_to_subject_from_source", AddHomeworkStates.waiting_for_source_type)
async def add_back_to_subject_from_source(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        return
    data = await state.get_data()
    date = data.get("date")
    await state.update_data(subject=None, is_textbook=False)
    await state.set_state(AddHomeworkStates.waiting_for_subject)
    homework_dict = await db_call(db.get_homework_by_date, date)
    keyboard = create_schedule_subject_buttons(date, "add_subject_", "add_hw", homework_dict=homework_dict)
    await query.message.edit_text(
        f"📚 Выберите предмет для даты {format_date_with_weekday(date)}:",
        reply_markup=keyboard,
    )
    await query.answer()


@router.callback_query(F.data.startswith("add_source_"), AddHomeworkStates.waiting_for_source_type)
async def add_select_source_type(query: CallbackQuery, state: FSMContext):
    is_textbook = query.data.replace("add_source_", "") == "textbook"
    data = await state.get_data()
    date, subject = data.get("date"), data.get("subject")
    await state.update_data(is_textbook=is_textbook)
    await state.set_state(AddHomeworkStates.waiting_for_content)
    source_label = "📘 из учебника" if is_textbook else "📝 другое"
    await query.message.edit_text(
        f"📝 Добавление ДЗ:\nДата: {format_date_with_weekday(date)}\nПредмет: {subject} ({source_label})\n\n"
        "Добавляйте текст, фото и PDF в любом порядке:",
        reply_markup=build_add_content_keyboard(),
    )
    await query.answer()


@router.callback_query(F.data == "add_text", AddHomeworkStates.waiting_for_content)
async def add_text_input(query: CallbackQuery, state: FSMContext):
    await state.update_data(waiting_for_text=True)
    await query.message.edit_text("✍️ Отправьте текст задания:")
    await query.answer()


@router.message(AddHomeworkStates.waiting_for_content)
async def process_add_content(message: Message, state: FSMContext, album: list[Message] = None):
    data = await state.get_data()
    photos = data.get("photos", [])
    prompt_id = data.get("content_prompt_id")
    if prompt_id:
        try:
            await message.bot.delete_message(message.chat.id, prompt_id)
        except Exception:
            pass

    if data.get("waiting_for_text"):
        text_parts = data.get("text_parts", [])
        text_parts.append(message.text or "")
        await state.update_data(text_parts=text_parts, waiting_for_text=False)
        await message.answer("✅ Текст добавлен!\nПродолжайте:", reply_markup=build_add_content_keyboard())
    elif album:
        for msg in album:
            if msg.photo:
                photos.append(msg.photo[-1].file_id)
            elif msg.document and msg.document.mime_type == "application/pdf":
                photos.append(f"pdf:{msg.document.file_id}")
        await state.update_data(photos=photos)
        await message.answer(f"✅ Добавлено файлов: {len(album)}!\nПродолжайте:", reply_markup=build_add_content_keyboard())
    elif message.photo:
        photos.append(message.photo[-1].file_id)
        await state.update_data(photos=photos)
        await message.answer("✅ Фото добавлено!\nПродолжайте:", reply_markup=build_add_content_keyboard())
    elif message.document and message.document.mime_type == "application/pdf":
        photos.append(f"pdf:{message.document.file_id}")
        await state.update_data(photos=photos)
        await message.answer("✅ PDF добавлен!\nПродолжайте:", reply_markup=build_add_content_keyboard())
    else:
        await message.answer("⚠️ Неподдерживаемый формат.", reply_markup=build_add_content_keyboard())


@router.callback_query(F.data == "add_photo", AddHomeworkStates.waiting_for_content)
async def add_photo_input(query: CallbackQuery, state: FSMContext):
    msg = await query.message.edit_text(
        "📸 Отправьте фотографию задания (или несколько по одной):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_content_input")]]
        ),
    )
    await state.update_data(content_prompt_id=msg.message_id)
    await query.answer()


@router.callback_query(F.data == "add_pdf", AddHomeworkStates.waiting_for_content)
async def add_pdf_input(query: CallbackQuery, state: FSMContext):
    msg = await query.message.edit_text(
        "📋 Отправьте PDF-файл (или несколько по одному):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_content_input")]]
        ),
    )
    await state.update_data(content_prompt_id=msg.message_id)
    await query.answer()


@router.callback_query(F.data == "cancel_content_input", AddHomeworkStates.waiting_for_content)
async def cancel_content_input(query: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(waiting_for_text=False)
    await query.message.edit_text(
        f"📝 Добавление ДЗ:\nДата: {data.get('date')}\nПредмет: {data.get('subject')}\n\nВыбирайте действие:",
        reply_markup=build_add_content_keyboard(),
    )
    await query.answer()


@router.callback_query(F.data == "add_back_subject", AddHomeworkStates.waiting_for_content)
async def add_back_to_subject(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        return
    data = await state.get_data()
    date = data.get("date")
    await state.update_data(subject=None, text_parts=[], photos=[], waiting_for_text=False)
    await state.set_state(AddHomeworkStates.waiting_for_subject)
    homework_dict = await db_call(db.get_homework_by_date, date)
    keyboard = create_schedule_subject_buttons(date, "add_subject_", "add_hw", homework_dict=homework_dict)
    await query.message.edit_text(
        f"📚 Выберите предмет для даты {format_date_with_weekday(date)}:",
        reply_markup=keyboard,
    )
    await query.answer()


@router.callback_query(F.data == "finish_add", AddHomeworkStates.waiting_for_content)
async def finish_add_hw(query: CallbackQuery, state: FSMContext):
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
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить ещё (на ту же дату)", callback_data=f"add_more_hw_{date}")],
                [InlineKeyboardButton(text="📊 В админ панель", callback_data="admin_panel")],
            ]
        )
        await query.message.edit_text(
            f"✅ ДЗ по {subject} на {format_date_with_weekday(date)} успешно добавлено!\nЧто хотите сделать дальше?",
            reply_markup=keyboard,
        )
        await state.clear()
    else:
        await query.message.edit_text("❌ Ошибка при сохранении в базу данных!")
    await query.answer()


@router.callback_query(F.data.startswith("add_more_hw_"))
async def add_more_hw(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        return
    date = query.data.replace("add_more_hw_", "")
    await state.update_data(date=date)
    await state.set_state(AddHomeworkStates.waiting_for_subject)
    homework_dict = await db_call(db.get_homework_by_date, date)
    keyboard = create_schedule_subject_buttons(date, "add_subject_", "admin_panel", homework_dict=homework_dict)
    await query.message.edit_text(
        f"📚 Выберите следующий предмет для даты {format_date_with_weekday(date)}:",
        reply_markup=keyboard,
    )
    await query.answer()


@router.callback_query(F.data == "edit_hw")
async def start_edit_hw(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        return
    await state.set_state(EditHomeworkStates.waiting_for_date)
    today = datetime.now()
    keyboard = create_month_calendar_keyboard(today.year, today.month, "admin_panel", "edit_date_", "edit_calendar_")
    await query.message.edit_text("📅 Выберите дату в календаре:", reply_markup=keyboard)
    await query.answer()


@router.callback_query(F.data.startswith("edit_calendar_"), EditHomeworkStates.waiting_for_date)
async def edit_calendar_month(query: CallbackQuery):
    payload = query.data.replace("edit_calendar_", "", 1)
    try:
        year, month = map(int, payload.split("_", 1))
    except Exception:
        await query.answer("❌ Ошибка календаря", show_alert=True)
        return
    keyboard = create_month_calendar_keyboard(year, month, "admin_panel", "edit_date_", "edit_calendar_")
    await query.message.edit_text("📅 Выберите дату в календаре:", reply_markup=keyboard)
    await query.answer()


@router.callback_query(F.data.startswith("edit_date_"), EditHomeworkStates.waiting_for_date)
async def edit_select_date(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        return
    date = query.data.replace("edit_date_", "")
    homework_dict = await db_call(db.get_homework_by_date, date)
    if not homework_dict:
        await query.answer(f"❌ На дату {format_date_with_weekday(date)} нет ДЗ", show_alert=True)
        return
    await state.update_data(date=date)
    await state.set_state(EditHomeworkStates.waiting_for_subject)
    keyboard = create_schedule_subject_buttons(date, "edit_subject_", "edit_hw", homework_dict=homework_dict)
    await query.message.edit_text(
        f"📚 Выберите предмет для редактирования на {format_date_with_weekday(date)}:",
        reply_markup=keyboard,
    )
    await query.answer()


@router.callback_query(F.data.startswith("edit_subject_"), EditHomeworkStates.waiting_for_subject)
async def edit_select_subject(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
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
    preview = text[:200] + "..." if len(text) > 200 else text
    await query.message.edit_text(
        f"✏️ Редактирование ДЗ:\n\n📅 Дата: {format_date_with_weekday(date)}\n📚 Предмет: {subject}\n\n"
        f"📝 Текущий текст:\n{preview}\n\n📸 Фото: {len(photos or [])} шт.\n\nВыберите действие:",
        reply_markup=build_edit_content_keyboard(),
    )
    await query.answer()


@router.callback_query(F.data == "edit_text", EditHomeworkStates.waiting_for_content)
async def edit_text_input(query: CallbackQuery, state: FSMContext):
    await state.update_data(waiting_for_text=True)
    await query.message.edit_text("✍️ Отправьте новый текст задания:")
    await query.answer()


@router.callback_query(F.data == "edit_photo", EditHomeworkStates.waiting_for_content)
async def edit_photo_input(query: CallbackQuery, state: FSMContext):
    await state.update_data(waiting_for_photo=True)
    await query.message.edit_text("📸 Отправьте фотографию для добавления к заданию:")
    await query.answer()


@router.callback_query(F.data == "edit_back_subject", EditHomeworkStates.waiting_for_content)
async def edit_back_to_subject(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        return
    data = await state.get_data()
    date = data.get("date")
    await state.update_data(subject=None, text=None, photos=[], waiting_for_text=False, waiting_for_photo=False)
    await state.set_state(EditHomeworkStates.waiting_for_subject)
    homework_dict = await db_call(db.get_homework_by_date, date)
    keyboard = create_schedule_subject_buttons(date, "edit_subject_", "edit_hw", homework_dict=homework_dict)
    await query.message.edit_text(f"📚 Выберите предмет на {format_date_with_weekday(date)}:", reply_markup=keyboard)
    await query.answer()


@router.callback_query(F.data == "finish_edit", EditHomeworkStates.waiting_for_content)
async def finish_edit_hw(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
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
            f"✅ ДЗ успешно обновлено!\n\n📅 Дата: {format_date_with_weekday(date)}\n📚 Предмет: {subject}"
        )
    else:
        await query.message.edit_text("❌ Ошибка при сохранении!")
    await state.clear()
    await query.answer()


@router.message(EditHomeworkStates.waiting_for_content)
async def process_edit_content(message: Message, state: FSMContext, album: list[Message] = None):
    if not _is_admin(message):
        return
    data = await state.get_data()
    if data.get("waiting_for_text"):
        await state.update_data(text=message.text or "", waiting_for_text=False)
        await message.answer("✅ Текст обновлён! Выберите дальнейшее действие:", reply_markup=build_edit_content_keyboard())
    elif data.get("waiting_for_photo"):
        photos = data.get("photos", [])
        if album:
            for msg in album:
                if msg.photo:
                    photos.append(msg.photo[-1].file_id)
            await state.update_data(photos=photos, waiting_for_photo=False)
            await message.answer(
                f"✅ Добавлено фото: {len(album)}! Выберите действие:",
                reply_markup=build_edit_content_keyboard(),
            )
        elif message.photo:
            photos.append(message.photo[-1].file_id)
            await state.update_data(photos=photos, waiting_for_photo=False)
            await message.answer(
                f"✅ Фото добавлено! (Всего: {len(photos)} шт.) Выберите действие:",
                reply_markup=build_edit_content_keyboard(),
            )


@router.callback_query(F.data == "delete_hw")
async def start_delete_hw(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        return
    await state.set_state(DeleteHomeworkStates.waiting_for_date)
    today = datetime.now()
    keyboard = create_month_calendar_keyboard(today.year, today.month, "admin_panel", "del_date_", "del_calendar_")
    await query.message.edit_text("📅 Выберите дату в календаре:", reply_markup=keyboard)
    await query.answer()


@router.callback_query(F.data.startswith("del_calendar_"), DeleteHomeworkStates.waiting_for_date)
async def delete_calendar_month(query: CallbackQuery):
    if not _is_admin(query):
        return
    payload = query.data.replace("del_calendar_", "", 1)
    try:
        year, month = map(int, payload.split("_", 1))
    except Exception:
        await query.answer("❌ Ошибка календаря", show_alert=True)
        return
    keyboard = create_month_calendar_keyboard(year, month, "admin_panel", "del_date_", "del_calendar_")
    await query.message.edit_text("📅 Выберите дату в календаре:", reply_markup=keyboard)
    await query.answer()


@router.callback_query(F.data.startswith("del_date_"), DeleteHomeworkStates.waiting_for_date)
async def delete_select_date(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        return
    date = query.data.replace("del_date_", "")
    homework_dict = await db_call(db.get_homework_by_date, date)
    if not homework_dict:
        await query.answer(f"❌ На дату {format_date_with_weekday(date)} нет ДЗ", show_alert=True)
        return
    subjects = list(homework_dict.keys())
    await state.update_data(date=date, delete_subjects=subjects)
    await state.set_state(DeleteHomeworkStates.waiting_for_subject)
    buttons = [[InlineKeyboardButton(text=f"🗑 {sub}", callback_data=f"del_subject_idx_{idx}")] for idx, sub in enumerate(subjects)]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="delete_hw")])
    await query.message.edit_text(
        f"🗑 Выберите предмет для удаления на {format_date_with_weekday(date)}:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await query.answer()


@router.callback_query(F.data.startswith("del_subject_"), DeleteHomeworkStates.waiting_for_subject)
async def delete_confirm(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        return
    data = await state.get_data()
    callback_data = query.data or ""
    if callback_data.startswith("del_subject_idx_"):
        idx = int(callback_data.replace("del_subject_idx_", "", 1))
        subject = data.get("delete_subjects", [])[idx]
    else:
        subject = callback_data.replace("del_subject_", "", 1)
    await state.update_data(subject=subject)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить", callback_data="confirm_del")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="del_back_subject")],
        ]
    )
    await query.message.edit_text(
        f"⚠️ Вы уверены, что хотите удалить ДЗ по {subject} на {format_date_with_weekday(data.get('date'))}?",
        reply_markup=keyboard,
    )
    await query.answer()


@router.callback_query(F.data == "del_back_subject")
async def delete_back_to_subject(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        return
    data = await state.get_data()
    date = data.get("date")
    await state.set_state(DeleteHomeworkStates.waiting_for_subject)
    buttons = [
        [InlineKeyboardButton(text=f"🗑 {sub}", callback_data=f"del_subject_idx_{idx}")]
        for idx, sub in enumerate(data.get("delete_subjects", []))
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="delete_hw")])
    await query.message.edit_text(
        f"🗑 Выберите предмет для удаления на {format_date_with_weekday(date)}:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await query.answer()


@router.callback_query(F.data == "confirm_del")
async def confirm_delete(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        return
    data = await state.get_data()
    date, subject = data.get("date"), data.get("subject")
    if await db_call(db.delete_homework, date, subject):
        await query.message.edit_text(f"✅ ДЗ по {subject} на {format_date_with_weekday(date)} удалено!")
    else:
        await query.message.edit_text("❌ Ошибка при удалении")
    await state.clear()
    await query.answer()


@router.callback_query(F.data == "view_all_hw")
async def view_all_hw(query: CallbackQuery):
    all_hw = await db_call(db.get_all_homework)
    if not all_hw:
        await query.message.edit_text("🭭 ДЗ ещё не добавлено")
        await query.answer()
        return
    text = "📋 Все домашние задания:\n\n"
    for date, subject, hw_text, photos, is_tb in all_hw:
        photo_info = f" 📸({len(photos)})" if photos else ""
        textbook_mark = " 📘" if is_tb else ""
        preview = hw_text[:50] + ("..." if len(hw_text) > 50 else "")
        text += f"📅 {format_date_with_weekday(date)} | 📚 {subject}{textbook_mark}{photo_info}\n   {preview}\n\n"
    await query.message.edit_text(
        text[:4000],
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]]
        ),
    )
    await query.answer()


@router.callback_query(F.data == "view_feedbacks")
async def view_feedbacks(query: CallbackQuery):
    if not _is_admin(query):
        return
    feedbacks = await db_call(db.get_all_feedback)
    if not feedbacks:
        await query.message.edit_text(
            "💌 Пожелания учеников\n\n📤 Пока пожеланий нет.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]]
            ),
        )
        await query.answer()
        return

    text_lines = [f"💌 <b>Пожелания учеников</b> ({len(feedbacks)} шт.):\n"]
    buttons = []
    for fid, user_id, username, first_name, last_name, fb_text, created_at in feedbacks:
        sender = f"{first_name} {last_name}".strip() or "Неизвестный"
        uname = f" (@{username})" if username else ""
        if isinstance(created_at, str) and "-" in created_at:
            date_str = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y %H:%M")
        else:
            date_str = str(created_at)
        text_lines.append(f"\n📝 <b>{sender}{uname}</b> | {date_str}\n{fb_text[:120]}...\n─────────────")
        buttons.append([InlineKeyboardButton(text=f"🗑 Удалить №{fid} ({sender})", callback_data=f"del_feedback_{fid}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад в админ панель", callback_data="admin_panel")])
    await query.message.edit_text(
        "".join(text_lines)[:4000],
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )
    await query.answer()


@router.callback_query(F.data.startswith("del_feedback_"))
async def delete_feedback_item(query: CallbackQuery):
    if not _is_admin(query):
        return
    fb_id = int(query.data.replace("del_feedback_", ""))
    await db_call(db.delete_feedback, fb_id)
    await query.answer("✅ Пожелание удалено")
    await view_feedbacks(query)


@router.callback_query(F.data == "view_users")
async def view_users(query: CallbackQuery, state: FSMContext):
    if not _is_admin(query):
        return

    users = await db_call(db.get_users_info)
    if not users:
        await query.message.edit_text(
            "👥 Нет зарегистрированных пользователей.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]]
            ),
        )
        await query.answer()
        return

    text = f"👥 <b>Пользователи бота ({len(users)})</b>:\n\n"
    for idx, (uid, uname, fname, reg_at, approved) in enumerate(users, 1):
        status = "✅" if approved else "⏳"
        name = fname or "Без имени"
        uname_str = f" (@{uname})" if uname else ""
        date_str = str(reg_at).split(" ")[0][:10] if reg_at else "?"
        line = f"{idx}. {status} <b>{name}</b>{uname_str}\n   🆔 <code>{uid}</code> | 📅 {date_str}\n\n"
        if len(text) + len(line) > 3800:
            text += f"... и ещё {len(users) - idx + 1} чел.\n"
            break
        text += line

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад в админ панель", callback_data="admin_panel")]]
    )
    await query.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await query.answer()
