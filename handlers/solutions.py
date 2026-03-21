from pathlib import Path
import asyncio
from time import monotonic
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.fsm.context import FSMContext

from states import SolutionSearchStates
from utils import IMAGE_EXTENSIONS, SOLUTIONS_INDEX_TTL_SEC, clear_last_solution_messages

router = Router()

_solution_index: dict[str, dict[str, list[Path]]] = {"algebra": {}, "geometry": {}}
_solution_index_expires_at = 0.0
_solution_index_lock = asyncio.Lock()

def build_solutions_index() -> dict[str, dict[str, list[Path]]]:
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


@router.callback_query(F.data == "find_solution_algebra")
async def find_solution_algebra(query: CallbackQuery, state: FSMContext):
    await query.answer()
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

@router.callback_query(F.data == "find_solution_geometry")
async def find_solution_geometry(query: CallbackQuery, state: FSMContext):
    await query.answer()
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

@router.callback_query(F.data == "cancel_solution_search", SolutionSearchStates.waiting_for_number)
async def cancel_solution_search(query: CallbackQuery, state: FSMContext):
    await clear_last_solution_messages(query, state)
    await state.set_state(None)
    await query.answer("↩️ Возврат к ДЗ за день")

@router.message(SolutionSearchStates.waiting_for_number)
async def handle_solution_number(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("❌ Введите только номер задания (например, 123).")
        return

    data = await state.get_data()
    solution_subject = data.get("solution_subject", "algebra")
    subject_name = "геометрии" if solution_subject == "geometry" else "алгебре"

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

    from aiogram.utils.media_group import MediaGroupBuilder
    photo_group = MediaGroupBuilder()
    
    for img_path in image_files:
        photo_group.add_photo(media=FSInputFile(str(img_path)))

    try:
        msgs = await message.bot.send_media_group(chat_id=message.chat.id, media=photo_group.build())
        solution_message_ids.extend(m.message_id for m in msgs)
    except Exception as e:
        print(f"❌ Ошибка отправки решения: {e}")

    back_msg = await message.answer("👆 Нажмите кнопку ниже, чтобы вернуться", reply_markup=back_keyboard)
    solution_message_ids.append(back_msg.message_id)

    await state.update_data(
        last_solution_message_ids=solution_message_ids,
        solution_user_task_message_id=message.message_id
    )
    await state.set_state(None)

@router.callback_query(F.data == "solution_back")
async def solution_back(query: CallbackQuery, state: FSMContext):
    await clear_last_solution_messages(query, state)
    await state.set_state(None)
    await query.answer("↩️ Возврат к ДЗ за день")
