import calendar
from datetime import datetime, timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import DAYS_TO_SHOW, SCHEDULE, SUBJECTS, get_unique_subjects_for_weekday


BROADCAST_PAGE_SIZE = 8


def build_admin_panel_keyboard(feedback_count: int = 0) -> InlineKeyboardMarkup:
    fb_label = "рџ’Њ РџРѕР¶РµР»Р°РЅРёСЏ СѓС‡РµРЅРёРєРѕРІ"
    if feedback_count > 0:
        fb_label += f" ({feedback_count}) рџ”ґ"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="вћ• Р”РѕР±Р°РІРёС‚СЊ Р”Р—", callback_data="add_hw")],
            [InlineKeyboardButton(text="вњЏпёЏ Р РµРґР°РєС‚РёСЂРѕРІР°С‚СЊ", callback_data="edit_hw")],
            [InlineKeyboardButton(text="рџ—‘ РЈРґР°Р»РёС‚СЊ", callback_data="delete_hw")],
            [InlineKeyboardButton(text="рџ“‹ Р’СЃРµ Р”Р—", callback_data="view_all_hw")],
            [InlineKeyboardButton(text="рџ“Ј РћС‚РїСЂР°РІРёС‚СЊ СЂР°СЃСЃС‹Р»РєСѓ", callback_data="broadcast_menu")],
            [InlineKeyboardButton(text=fb_label, callback_data="view_feedbacks")],
            [InlineKeyboardButton(text="рџ‘Ґ РџРѕР»СЊР·РѕРІР°С‚РµР»Рё", callback_data="view_users")],
            [InlineKeyboardButton(text="в—ЂпёЏ Р’С‹С…РѕРґ РІ РјРµРЅСЋ", callback_data="back_to_menu")],
        ]
    )


def build_add_content_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="вњЏпёЏ Р”РѕР±Р°РІРёС‚СЊ С‚РµРєСЃС‚", callback_data="add_text")],
            [
                InlineKeyboardButton(text="рџ“ё Р”РѕР±Р°РІРёС‚СЊ С„РѕС‚Рѕ", callback_data="add_photo"),
                InlineKeyboardButton(text="рџ“‹ Р”РѕР±Р°РІРёС‚СЊ PDF", callback_data="add_pdf"),
            ],
            [InlineKeyboardButton(text="вњ… Р—Р°РІРµСЂС€РёС‚СЊ", callback_data="finish_add")],
            [InlineKeyboardButton(text="в—ЂпёЏ РќР°Р·Р°Рґ", callback_data="add_back_subject")],
        ]
    )


def build_edit_content_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="вњЏпёЏ РР·РјРµРЅРёС‚СЊ С‚РµРєСЃС‚", callback_data="edit_text")],
            [InlineKeyboardButton(text="рџ“ё Р”РѕР±Р°РІРёС‚СЊ РµС‰С‘ С„РѕС‚Рѕ", callback_data="edit_photo")],
            [InlineKeyboardButton(text="вњ… РЎРѕС…СЂР°РЅРёС‚СЊ РёР·РјРµРЅРµРЅРёСЏ", callback_data="finish_edit")],
            [InlineKeyboardButton(text="в—ЂпёЏ РќР°Р·Р°Рґ", callback_data="edit_back_subject")],
        ]
    )


def build_broadcast_text_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="вќЊ РћС‚РјРµРЅР°", callback_data="broadcast_cancel")],
        ]
    )


def build_broadcast_message_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="рџ“љ РњРѕРё Р”Р—", callback_data="student_view")],
        ]
    )


def build_broadcast_preview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="рџ§Є РўРµСЃС‚ СЃРµР±Рµ", callback_data="broadcast_test")],
            [InlineKeyboardButton(text="вњ… РћС‚РїСЂР°РІРёС‚СЊ СЂР°СЃСЃС‹Р»РєСѓ", callback_data="broadcast_send")],
            [
                InlineKeyboardButton(text="рџ‘Ґ РџРѕР»СѓС‡Р°С‚РµР»Рё", callback_data="broadcast_back_recipients"),
                InlineKeyboardButton(text="вњЏпёЏ РР·РјРµРЅРёС‚СЊ С‚РµРєСЃС‚", callback_data="broadcast_edit_text"),
            ],
            [InlineKeyboardButton(text="вќЊ РћС‚РјРµРЅР°", callback_data="broadcast_cancel")],
        ]
    )


def build_broadcast_recipients_keyboard(
    users: list[dict],
    selected_ids: set[int],
    page: int,
    page_size: int = BROADCAST_PAGE_SIZE,
) -> InlineKeyboardMarkup:
    total_pages = max(1, (len(users) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    end = start + page_size
    page_users = users[start:end]

    buttons: list[list[InlineKeyboardButton]] = []
    for user in page_users:
        user_id = int(user["user_id"])
        checked = "вњ…" if user_id in selected_ids else "в‘пёЏ"
        first_name = (user.get("first_name") or "Р‘РµР· РёРјРµРЅРё").strip()
        username = f" (@{user['username']})" if user.get("username") else ""
        label = f"{checked} {first_name}{username}"
        buttons.append([InlineKeyboardButton(text=label[:64], callback_data=f"broadcast_toggle_{user_id}")])

    if total_pages > 1:
        buttons.append(
            [
                InlineKeyboardButton(text="в—ЂпёЏ", callback_data=f"broadcast_page_{page - 1}"),
                InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"),
                InlineKeyboardButton(text="в–¶пёЏ", callback_data=f"broadcast_page_{page + 1}"),
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(text="вњ… Р’С‹Р±СЂР°С‚СЊ РІСЃРµС…", callback_data="broadcast_select_all"),
            InlineKeyboardButton(text="рџ§№ РЎРЅСЏС‚СЊ РІСЃРµС…", callback_data="broadcast_clear_all"),
        ]
    )
    buttons.append([InlineKeyboardButton(text="вћЎпёЏ РџСЂРѕРґРѕР»Р¶РёС‚СЊ", callback_data="broadcast_preview")])
    buttons.append([InlineKeyboardButton(text="вќЊ РћС‚РјРµРЅР°", callback_data="broadcast_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


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
            "Mon": "РїРѕРЅРµРґРµР»СЊРЅРёРє",
            "Tue": "РІС‚РѕСЂРЅРёРє",
            "Wed": "СЃСЂРµРґР°",
            "Thu": "С‡РµС‚РІРµСЂРі",
            "Fri": "РїСЏС‚РЅРёС†Р°",
            "Sat": "СЃСѓР±Р±РѕС‚Р°",
            "Sun": "РІРѕСЃРєСЂРµСЃРµРЅСЊРµ",
        }
        day_name = weekday_names.get(dt.strftime("%a"), dt.strftime("%A"))
        formatted = f"{date_str} ({day_name})"
        if mark_today and date_str == datetime.now().strftime("%d.%m.%Y"):
            formatted += " [СЃРµРіРѕРґРЅСЏ]"
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
    buttons.append([InlineKeyboardButton(text="в—ЂпёЏ РќР°Р·Р°Рґ", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def create_subject_buttons(prefix: str = "subject_", back_callback: str = "back_to_menu") -> InlineKeyboardMarkup:
    buttons = []
    for subject in SUBJECTS:
        buttons.append([InlineKeyboardButton(text=subject, callback_data=f"{prefix}{subject}")])
    buttons.append([InlineKeyboardButton(text="в—ЂпёЏ РќР°Р·Р°Рґ", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_weekday_from_date(date_str: str) -> int | None:
    try:
        dt = datetime.strptime(date_str, "%d.%m.%Y")
        return dt.weekday()
    except Exception:
        return None


def create_schedule_subject_buttons(
    date_str: str, prefix: str = "subject_", back_callback: str = "back_to_menu", homework_dict: dict | None = None
) -> InlineKeyboardMarkup:
    weekday = get_weekday_from_date(date_str)
    if weekday is not None and weekday in SCHEDULE:
        subjects = get_unique_subjects_for_weekday(weekday)
    else:
        subjects = SUBJECTS

    buttons = []
    for idx, subject in enumerate(subjects, start=1):
        if homework_dict is not None and subject in homework_dict:
            label = f"{idx}. вњ… {subject}"
            button = InlineKeyboardButton(
                text=label,
                callback_data=f"{prefix}{subject}",
                style="primary",
            )
        else:
            label = f"{idx}. {subject}"
            button = InlineKeyboardButton(text=label, callback_data=f"{prefix}{subject}")
        buttons.append([button])
    buttons.append([InlineKeyboardButton(text="в—ЂпёЏ РќР°Р·Р°Рґ", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def create_month_calendar_keyboard(
    year: int,
    month: int,
    back_callback: str = "student_view",
    date_callback_prefix: str = "view_date_",
    nav_callback_prefix: str = "view_calendar_",
) -> InlineKeyboardMarkup:
    month_names = {
        1: "РЇРЅРІР°СЂСЊ",
        2: "Р¤РµРІСЂР°Р»СЊ",
        3: "РњР°СЂС‚",
        4: "РђРїСЂРµР»СЊ",
        5: "РњР°Р№",
        6: "РСЋРЅСЊ",
        7: "РСЋР»СЊ",
        8: "РђРІРіСѓСЃС‚",
        9: "РЎРµРЅС‚СЏР±СЂСЊ",
        10: "РћРєС‚СЏР±СЂСЊ",
        11: "РќРѕСЏР±СЂСЊ",
        12: "Р”РµРєР°Р±СЂСЊ",
    }
    weekday_headers = ["РџРЅ", "Р’С‚", "РЎСЂ", "Р§С‚", "РџС‚", "РЎР±", "Р’СЃ"]
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
                row.append(
                    InlineKeyboardButton(
                        text=label,
                        callback_data=f"{date_callback_prefix}{date_str}",
                        style="success",
                    )
                )
            else:
                label = f" {day_label} "
                row.append(InlineKeyboardButton(text=label, callback_data=f"{date_callback_prefix}{date_str}"))
        buttons.append(row)

    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)

    buttons.append(
        [
            InlineKeyboardButton(text="в—ЂпёЏ", callback_data=f"{nav_callback_prefix}{prev_year}_{prev_month:02d}"),
            InlineKeyboardButton(text="вћЎпёЏ", callback_data=f"{nav_callback_prefix}{next_year}_{next_month:02d}"),
        ]
    )
    buttons.append([InlineKeyboardButton(text="в—ЂпёЏ РќР°Р·Р°Рґ", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
