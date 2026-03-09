import calendar
from datetime import datetime, timedelta
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from config import DAYS_TO_SHOW, SCHEDULE, SUBJECTS, get_unique_subjects_for_weekday

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

def get_dates_list(days: int = DAYS_TO_SHOW, past_days: int = 0):
    dates = []
    for i in range(-past_days, days):
        date = datetime.now() + timedelta(days=i)
        dates.append(date.strftime("%d.%m.%Y"))
    return dates

def format_date_with_weekday(date_str: str, mark_today: bool = False) -> str:
    try:
        dt = datetime.strptime(date_str, "%d.%m.%Y")
        weekday_names = {
            "Mon": "понедельник", "Tue": "вторник", "Wed": "среда",
            "Thu": "четверг", "Fri": "пятница", "Sat": "суббота", "Sun": "воскресенье",
        }
        day_name = weekday_names.get(dt.strftime("%a"), dt.strftime("%A"))
        formatted = f"{date_str} ({day_name})"
        if mark_today and date_str == datetime.now().strftime("%d.%m.%Y"):
            formatted += " [сегодня]"
        return formatted
    except Exception:
        return date_str

def create_date_buttons(
    dates: list, prefix: str = "select_date_", back_callback: str = "back_to_menu", mark_today: bool = False
) -> InlineKeyboardMarkup:
    buttons = []
    for date in dates:
        label = format_date_with_weekday(date, mark_today=mark_today)
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"{prefix}{date}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def create_subject_buttons(prefix: str = "subject_", back_callback: str = "back_to_menu") -> InlineKeyboardMarkup:
    buttons = []
    for subject in SUBJECTS:
        buttons.append([InlineKeyboardButton(text=subject, callback_data=f"{prefix}{subject}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_weekday_from_date(date_str: str) -> int | None:
    try:
        dt = datetime.strptime(date_str, "%d.%m.%Y")
        return dt.weekday()
    except Exception:
        return None

def create_schedule_subject_buttons(
    date_str: str, prefix: str = "subject_", back_callback: str = "back_to_menu", homework_dict: dict | None = None,
) -> InlineKeyboardMarkup:
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
    year: int, month: int, back_callback: str = "student_view", date_callback_prefix: str = "view_date_",
    nav_callback_prefix: str = "view_calendar_",
) -> InlineKeyboardMarkup:
    month_names = {
        1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель", 5: "Май", 6: "Июнь",
        7: "Июль", 8: "Август", 9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
    }
    weekday_headers = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    today = datetime.now()

    cal = calendar.Calendar(firstweekday=0)
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

    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)

    buttons.append([
        InlineKeyboardButton(text="◀️", callback_data=f"{nav_callback_prefix}{prev_year}_{prev_month:02d}"),
        InlineKeyboardButton(text="➡️", callback_data=f"{nav_callback_prefix}{next_year}_{next_month:02d}"),
    ])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
