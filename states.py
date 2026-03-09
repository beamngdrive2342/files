from aiogram.fsm.state import State, StatesGroup

class AdminAuthStates(StatesGroup):
    """Аутентификация админа"""
    waiting_for_password = State()

class AddHomeworkStates(StatesGroup):
    """Добавление ДЗ"""
    waiting_for_date = State()
    waiting_for_subject = State()
    waiting_for_source_type = State()
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
    """Поиск решений"""
    waiting_for_number = State()

class FeedbackStates(StatesGroup):
    """Отправка пожелания/идеи"""
    waiting_for_feedback = State()
