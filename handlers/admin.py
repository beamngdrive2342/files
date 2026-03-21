from datetime import datetime
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from utils import db, db_call, SUBJECTS_WITH_SOLUTIONS
from states import AddHomeworkStates, EditHomeworkStates, DeleteHomeworkStates
from keyboards import create_month_calendar_keyboard, create_schedule_subject_buttons, format_date_with_weekday, build_add_content_keyboard, build_edit_content_keyboard
from config import ADMIN_ID

router = Router()

@router.callback_query(F.data == "admin_panel")
async def show_admin_panel(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ У вас нет доступа!", show_alert=True)
        return
    await query.answer()
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
    await query.message.edit_text("✅ Админ панель\n\nВыберите действие:", reply_markup=keyboard)

@router.callback_query(F.data == "add_hw")
async def start_add_hw(query: CallbackQuery, state: FSMContext):
    await query.answer()
    await state.set_state(AddHomeworkStates.waiting_for_date)
    today = datetime.now()
    keyboard = create_month_calendar_keyboard(today.year, today.month, "admin_panel", "add_date_", "add_calendar_")
    await query.message.edit_text("📅 Выберите дату в календаре:", reply_markup=keyboard)

@router.callback_query(F.data.startswith("add_calendar_"), AddHomeworkStates.waiting_for_date)
async def add_calendar_month(query: CallbackQuery):
    payload = query.data.replace("add_calendar_", "", 1)
    try:
        year_str, month_str = payload.split("_", 1)
        year, month = int(year_str), int(month_str)
    except Exception: return await query.answer("❌ Ошибка календаря", show_alert=True)
    keyboard = create_month_calendar_keyboard(year, month, "admin_panel", "add_date_", "add_calendar_")
    await query.message.edit_text("📅 Выберите дату в календаре:", reply_markup=keyboard)

@router.callback_query(F.data.startswith("add_date_"), AddHomeworkStates.waiting_for_date)
async def add_select_date(query: CallbackQuery, state: FSMContext):
    date = query.data.replace("add_date_", "")
    await state.update_data(date=date)
    await state.set_state(AddHomeworkStates.waiting_for_subject)
    homework_dict = await db_call(db.get_homework_by_date, date)
    keyboard = create_schedule_subject_buttons(date, "add_subject_", "add_hw", homework_dict=homework_dict)
    await query.message.edit_text(f"📚 Выберите предмет для даты {format_date_with_weekday(date)}:", reply_markup=keyboard)

@router.callback_query(F.data.startswith("add_subject_"), AddHomeworkStates.waiting_for_subject)
async def add_select_subject(query: CallbackQuery, state: FSMContext):
    subject = query.data.replace("add_subject_", "")
    data = await state.get_data()
    date = data.get("date")
    if await db_call(db.homework_exists, date, subject):
        return await query.answer(f"⚠️ ДЗ по {subject} на {format_date_with_weekday(date)} уже существует!", show_alert=True)
    await state.update_data(subject=subject, photos=[], text_parts=[])
    if subject in SUBJECTS_WITH_SOLUTIONS:
        await state.set_state(AddHomeworkStates.waiting_for_source_type)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📘 Из учебника", callback_data="add_source_textbook")],
            [InlineKeyboardButton(text="📝 Другое (доска...)", callback_data="add_source_other")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="add_back_to_subject_from_source")],
        ])
        await query.message.edit_text(f"📚 {subject} — откуда задание?\n📘 **Из учебника**\n📝 **Другое**", reply_markup=keyboard)
        return await query.answer()
    
    await state.update_data(is_textbook=False)
    await state.set_state(AddHomeworkStates.waiting_for_content)
    await query.message.edit_text(f"📝 Добавление ДЗ:\nДата: {format_date_with_weekday(date)}\nПредмет: {subject}\n\nДобавляйте текст, фото и PDF в любом порядке:", reply_markup=build_add_content_keyboard())

@router.callback_query(F.data == "add_back_to_subject_from_source", AddHomeworkStates.waiting_for_source_type)
async def add_back_to_subject_from_source(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != ADMIN_ID: return
    data = await state.get_data()
    date = data.get("date")
    await state.update_data(subject=None, is_textbook=False)
    await state.set_state(AddHomeworkStates.waiting_for_subject)
    homework_dict = await db_call(db.get_homework_by_date, date)
    keyboard = create_schedule_subject_buttons(date, "add_subject_", "add_hw", homework_dict=homework_dict)
    await query.message.edit_text(f"📚 Выберите предмет для даты {format_date_with_weekday(date)}:", reply_markup=keyboard)

@router.callback_query(F.data.startswith("add_source_"), AddHomeworkStates.waiting_for_source_type)
async def add_select_source_type(query: CallbackQuery, state: FSMContext):
    is_textbook = (query.data.replace("add_source_", "") == "textbook")
    data = await state.get_data()
    date, subject = data.get("date"), data.get("subject")
    await state.update_data(is_textbook=is_textbook)
    await state.set_state(AddHomeworkStates.waiting_for_content)
    source_label = "📘 из учебника" if is_textbook else "📝 другое"
    await query.message.edit_text(f"📝 Добавление ДЗ:\nДата: {format_date_with_weekday(date)}\nПредмет: {subject} ({source_label})\n\nДобавляйте текст, фото и PDF в любом порядке:", reply_markup=build_add_content_keyboard())

@router.callback_query(F.data == "add_text", AddHomeworkStates.waiting_for_content)
async def add_text_input(query: CallbackQuery, state: FSMContext):
    await state.update_data(waiting_for_text=True)
    await query.message.edit_text("✍️ Отправьте текст задания:")

@router.message(AddHomeworkStates.waiting_for_content)
async def process_add_content(message: Message, state: FSMContext, album: list[Message] = None):
    data = await state.get_data()
    photos = data.get("photos", [])
    prompt_id = data.get("content_prompt_id")
    if prompt_id:
        try: await message.bot.delete_message(message.chat.id, prompt_id)
        except Exception: pass
    
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
    msg = await query.message.edit_text("📸 Отправьте фотографию задания (или несколько по одной):", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_content_input")]]))
    await state.update_data(content_prompt_id=msg.message_id)

@router.callback_query(F.data == "add_pdf", AddHomeworkStates.waiting_for_content)
async def add_pdf_input(query: CallbackQuery, state: FSMContext):
    msg = await query.message.edit_text("📋 Отправьте PDF-файл (или несколько по одному):", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_content_input")]]))
    await state.update_data(content_prompt_id=msg.message_id)

@router.callback_query(F.data == "cancel_content_input", AddHomeworkStates.waiting_for_content)
async def cancel_content_input(query: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(waiting_for_text=False)
    await query.message.edit_text(f"📝 Добавление ДЗ:\nДата: {data.get('date')}\nПредмет: {data.get('subject')}\n\nВыбирайте действие:", reply_markup=build_add_content_keyboard())

@router.callback_query(F.data == "add_back_subject", AddHomeworkStates.waiting_for_content)
async def add_back_to_subject(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != ADMIN_ID: return
    data = await state.get_data()
    date = data.get("date")
    await state.update_data(subject=None, text_parts=[], photos=[], waiting_for_text=False)
    await state.set_state(AddHomeworkStates.waiting_for_subject)
    homework_dict = await db_call(db.get_homework_by_date, date)
    keyboard = create_schedule_subject_buttons(date, "add_subject_", "add_hw", homework_dict=homework_dict)
    await query.message.edit_text(f"📚 Выберите предмет для даты {format_date_with_weekday(date)}:", reply_markup=keyboard)

@router.callback_query(F.data == "finish_add", AddHomeworkStates.waiting_for_content)
async def finish_add_hw(query: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    date, subject, text_parts, photos, is_textbook = data.get("date"), data.get("subject"), data.get("text_parts", []), data.get("photos", []), data.get("is_textbook", False)
    if not text_parts and not photos:
        return await query.answer("❌ Добавьте текст, фото или PDF!", show_alert=True)
    full_text = "\n\n".join(text_parts).strip()
    if await db_call(db.add_homework, date, subject, full_text, photos, is_textbook):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить еще (на ту же дату)", callback_data=f"add_more_hw_{date}")],
            [InlineKeyboardButton(text="📊 В админ панель", callback_data="admin_panel")],
        ])
        await query.message.edit_text(f"✅ ДЗ по {subject} на {format_date_with_weekday(date)} успешно добавлено!\nЧто вы хотите сделать дальше?", reply_markup=keyboard)
        await state.clear()
    else:
        await query.message.edit_text("❌ Ошибка при сохранении в базу данных!")

@router.callback_query(F.data.startswith("add_more_hw_"))
async def add_more_hw(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != ADMIN_ID: return
    date = query.data.replace("add_more_hw_", "")
    await state.update_data(date=date)
    await state.set_state(AddHomeworkStates.waiting_for_subject)
    homework_dict = await db_call(db.get_homework_by_date, date)
    keyboard = create_schedule_subject_buttons(date, "add_subject_", "admin_panel", homework_dict=homework_dict)
    await query.message.edit_text(f"📚 Выберите следующий предмет для даты {format_date_with_weekday(date)}:", reply_markup=keyboard)


@router.callback_query(F.data == "edit_hw")
async def start_edit_hw(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != ADMIN_ID: return
    await state.set_state(EditHomeworkStates.waiting_for_date)
    today = datetime.now()
    keyboard = create_month_calendar_keyboard(today.year, today.month, "admin_panel", "edit_date_", "edit_calendar_")
    await query.message.edit_text("📅 Выберите дату в календаре:", reply_markup=keyboard)

@router.callback_query(F.data.startswith("edit_calendar_"), EditHomeworkStates.waiting_for_date)
async def edit_calendar_month(query: CallbackQuery):
    payload = query.data.replace("edit_calendar_", "", 1)
    try:
        year, month = map(int, payload.split("_", 1))
    except Exception: return await query.answer("❌ Ошибка календаря", show_alert=True)
    keyboard = create_month_calendar_keyboard(year, month, "admin_panel", "edit_date_", "edit_calendar_")
    await query.message.edit_text("📅 Выберите дату в календаре:", reply_markup=keyboard)

@router.callback_query(F.data.startswith("edit_date_"), EditHomeworkStates.waiting_for_date)
async def edit_select_date(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != ADMIN_ID: return
    date = query.data.replace("edit_date_", "")
    homework_dict = await db_call(db.get_homework_by_date, date)
    if not homework_dict: return await query.answer(f"❌ На дату {format_date_with_weekday(date)} нет ДЗ", show_alert=True)
    await state.update_data(date=date)
    await state.set_state(EditHomeworkStates.waiting_for_subject)
    keyboard = create_schedule_subject_buttons(date, "edit_subject_", "edit_hw", homework_dict=homework_dict)
    await query.message.edit_text(f"📚 Выберите предмет для редактирования на {format_date_with_weekday(date)}:", reply_markup=keyboard)

@router.callback_query(F.data.startswith("edit_subject_"), EditHomeworkStates.waiting_for_subject)
async def edit_select_subject(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != ADMIN_ID: return
    subject = query.data.replace("edit_subject_", "")
    data = await state.get_data()
    date = data.get("date")
    homework = await db_call(db.get_homework, date, subject)
    if not homework: return await query.answer("❌ ДЗ не найдено", show_alert=True)
    text, photos, is_textbook = homework
    await state.update_data(subject=subject, text=text, photos=photos or [], is_textbook=is_textbook)
    await state.set_state(EditHomeworkStates.waiting_for_content)
    preview = text[:200] + "..." if len(text) > 200 else text
    await query.message.edit_text(f"✏️ Редактирование ДЗ:\n\n📅 Дата: {format_date_with_weekday(date)}\n📚 Предмет: {subject}\n\n📝 Текущий текст:\n{preview}\n\n📸 Фото: {len(photos or [])} шт.\n\nВыберите действие:", reply_markup=build_edit_content_keyboard())

@router.callback_query(F.data == "edit_text", EditHomeworkStates.waiting_for_content)
async def edit_text_input(query: CallbackQuery, state: FSMContext):
    await state.update_data(waiting_for_text=True)
    await query.message.edit_text("✍️ Отправьте новый текст задания:")

@router.callback_query(F.data == "edit_photo", EditHomeworkStates.waiting_for_content)
async def edit_photo_input(query: CallbackQuery, state: FSMContext):
    await state.update_data(waiting_for_photo=True)
    await query.message.edit_text("📸 Отправьте фотографию для добавления к заданию:")

@router.callback_query(F.data == "edit_back_subject", EditHomeworkStates.waiting_for_content)
async def edit_back_to_subject(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != ADMIN_ID: return
    data = await state.get_data()
    date = data.get("date")
    await state.update_data(subject=None, text=None, photos=[], waiting_for_text=False, waiting_for_photo=False)
    await state.set_state(EditHomeworkStates.waiting_for_subject)
    homework_dict = await db_call(db.get_homework_by_date, date)
    keyboard = create_schedule_subject_buttons(date, "edit_subject_", "edit_hw", homework_dict=homework_dict)
    await query.message.edit_text(f"📚 Выберите предмет на {format_date_with_weekday(date)}:", reply_markup=keyboard)

@router.callback_query(F.data == "finish_edit", EditHomeworkStates.waiting_for_content)
async def finish_edit_hw(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != ADMIN_ID: return
    data = await state.get_data()
    date, subject, text, photos = data.get("date"), data.get("subject"), data.get("text", ""), data.get("photos", [])
    if not text: return await query.answer("❌ Текст задания не может быть пустым!", show_alert=True)
    if await db_call(db.update_homework, date, subject, text, photos):
        await query.message.edit_text(f"✅ ДЗ успешно обновлено!\n\n📅 Дата: {format_date_with_weekday(date)}\n📚 Предмет: {subject}")
    else:
        await query.message.edit_text("❌ Ошибка при сохранении!")
    await state.clear()

@router.message(EditHomeworkStates.waiting_for_content)
async def process_edit_content(message: Message, state: FSMContext, album: list[Message] = None):
    if message.from_user.id != ADMIN_ID: return
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
            await message.answer(f"✅ Добавлено фото: {len(album)}! Выберите действие:", reply_markup=build_edit_content_keyboard())
        elif message.photo:
            photos.append(message.photo[-1].file_id)
            await state.update_data(photos=photos, waiting_for_photo=False)
            await message.answer(f"✅ Фото добавлено! (Всего: {len(photos)} шт.) Выберите действие:", reply_markup=build_edit_content_keyboard())

@router.callback_query(F.data == "delete_hw")
async def start_delete_hw(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != ADMIN_ID: return
    await state.set_state(DeleteHomeworkStates.waiting_for_date)
    today = datetime.now()
    keyboard = create_month_calendar_keyboard(today.year, today.month, "admin_panel", "del_date_", "del_calendar_")
    await query.message.edit_text("📅 Выберите дату в календаре:", reply_markup=keyboard)

@router.callback_query(F.data.startswith("del_calendar_"), DeleteHomeworkStates.waiting_for_date)
async def delete_calendar_month(query: CallbackQuery):
    if query.from_user.id != ADMIN_ID: return
    payload = query.data.replace("del_calendar_", "", 1)
    try:
        year, month = map(int, payload.split("_", 1))
    except Exception: return await query.answer("❌ Ошибка календаря", show_alert=True)
    keyboard = create_month_calendar_keyboard(year, month, "admin_panel", "del_date_", "del_calendar_")
    await query.message.edit_text("📅 Выберите дату в календаре:", reply_markup=keyboard)

@router.callback_query(F.data.startswith("del_date_"), DeleteHomeworkStates.waiting_for_date)
async def delete_select_date(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != ADMIN_ID: return
    date = query.data.replace("del_date_", "")
    homework_dict = await db_call(db.get_homework_by_date, date)
    if not homework_dict: return await query.answer(f"❌ На дату {format_date_with_weekday(date)} нет ДЗ", show_alert=True)
    subjects = list(homework_dict.keys())
    await state.update_data(date=date, delete_subjects=subjects)
    await state.set_state(DeleteHomeworkStates.waiting_for_subject)
    buttons = [[InlineKeyboardButton(text=f"🗑 {sub}", callback_data=f"del_subject_idx_{idx}")] for idx, sub in enumerate(subjects)]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="delete_hw")])
    await query.message.edit_text(f"🗑 Выберите предмет для удаления на {format_date_with_weekday(date)}:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data.startswith("del_subject_"), DeleteHomeworkStates.waiting_for_subject)
async def delete_confirm(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != ADMIN_ID: return
    data = await state.get_data()
    callback_data = query.data or ""
    if callback_data.startswith("del_subject_idx_"):
        idx = int(callback_data.replace("del_subject_idx_", "", 1))
        subject = data.get("delete_subjects", [])[idx]
    else:
        subject = callback_data.replace("del_subject_", "", 1)
    await state.update_data(subject=subject)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data="confirm_del")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="del_back_subject")],
    ])
    await query.message.edit_text(f"⚠️ Вы уверены что хотите удалить ДЗ по {subject} на {format_date_with_weekday(data.get('date'))}?", reply_markup=keyboard)

@router.callback_query(F.data.startswith("del_back_subject"))
async def delete_back_to_subject(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != ADMIN_ID: return
    data = await state.get_data()
    date = data.get("date")
    await state.set_state(DeleteHomeworkStates.waiting_for_subject)
    buttons = [[InlineKeyboardButton(text=f"🗑 {sub}", callback_data=f"del_subject_idx_{idx}")] for idx, sub in enumerate(data.get("delete_subjects", []))]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="delete_hw")])
    await query.message.edit_text(f"🗑 Выберите предмет для удаления на {format_date_with_weekday(date)}:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data.startswith("confirm_del"))
async def confirm_delete(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != ADMIN_ID: return
    data = await state.get_data()
    date, subject = data.get("date"), data.get("subject")
    if await db_call(db.delete_homework, date, subject):
        await query.message.edit_text(f"✅ ДЗ по {subject} на {format_date_with_weekday(date)} удалено!")
    else:
        await query.message.edit_text("❌ Ошибка при удалении")
    await state.clear()

@router.callback_query(F.data == "view_all_hw")
async def view_all_hw(query: CallbackQuery):
    all_hw = await db_call(db.get_all_homework)
    if not all_hw: return await query.message.edit_text("📭 ДЗ еще не добавлено")
    text = "📋 Все домашние задания:\n\n"
    for date, subject, hw_text, photos, is_tb in all_hw:
        photo_info = f" 📸({len(photos)})" if photos else ""
        text += f"📅 {format_date_with_weekday(date)} | 📚 {subject} {'📘' if is_tb else ''}{photo_info}\n   {hw_text[:50]}{'...' if len(hw_text) > 50 else ''}\n\n"
    await query.message.edit_text(text[:4000], reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]]))

@router.callback_query(F.data == "view_feedbacks")
async def view_feedbacks(query: CallbackQuery):
    if query.from_user.id != ADMIN_ID: return
    feedbacks = await db_call(db.get_all_feedback)
    if not feedbacks: return await query.message.edit_text("💌 Пожелания учеников\n\n📤 Пока пожеланий нет.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]]))
    text_lines = [f"💌 <b>Пожелания учеников</b> ({len(feedbacks)} шт.):\n"]
    buttons = []
    for fid, user_id, username, first_name, last_name, fb_text, created_at in feedbacks:
        sender = f"{first_name} {last_name}".strip() or "Неизвестный"
        uname = f" (@{username})" if username else ""
        date_str = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y %H:%M") if isinstance(created_at, str) and "-" in created_at else created_at
        text_lines.append(f"\n📝 <b>{sender}{uname}</b> | {date_str}\n{fb_text[:120]}...\n―――――――――――――")
        buttons.append([InlineKeyboardButton(text=f"🗑 Удалить №{fid} ({sender})", callback_data=f"del_feedback_{fid}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад в админ панель", callback_data="admin_panel")])
    await query.message.edit_text("".join(text_lines)[:4000], reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data.startswith("del_feedback_"))
async def delete_feedback_item(query: CallbackQuery):
    if query.from_user.id != ADMIN_ID: return
    fb_id = int(query.data.replace("del_feedback_", ""))
    await db_call(db.delete_feedback, fb_id)
    await query.answer("✅ Пожелание удалено")
    await view_feedbacks(query)

@router.callback_query(F.data == "view_users")
async def view_users(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != ADMIN_ID:
        return
    
    users = await db_call(db.get_users_info)
    
    if not users:
        await query.message.edit_text(
            "👥 Нет зарегистрированных пользователей.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]
            ])
        )
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

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад в админ панель", callback_data="admin_panel")]
    ])
    await query.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
