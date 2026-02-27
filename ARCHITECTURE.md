🏗️ АРХИТЕКТУРА И СТРУКТУРА ПРОЕКТА

═══════════════════════════════════════════════════════════════════════════════

📐 ОБЩАЯ АРХИТЕКТУРА

┌─────────────────────────────────────────────────────────────────┐
│                      TELEGRAM SERVER                            │
│                    (telegram.org API)                           │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                    ┌──────────┴──────────┐
                    │ BOT (aiogram)       │
                    │ Polling Mode        │
                    └──────────┬──────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
    ┌───▼───┐            ┌────▼─────┐         ┌─────▼────┐
    │config │            │ handlers  │         │ database │
    │ .py   │            │  .py      │         │  .py     │
    └───────┘            └──────────┘         └──────────┘
                               │
                               │
                         ┌─────▼──────┐
                         │ SQLite DB  │
                         │homework.db│
                         └────────────┘


═══════════════════════════════════════════════════════════════════════════════

📁 СТРУКТУРА ФАЙЛОВ

homework_bot/
│
├── 📄 main.py
│   ├─ Точка входа программы
│   ├─ Инициализация Bot и Dispatcher
│   ├─ Запуск polling (слушание сообщений)
│   └─ Обработка жизненного цикла бота
│
├── 📄 config.py
│   ├─ BOT_TOKEN - токен Telegram бота
│   ├─ ADMIN_ID - User ID администратора
│   ├─ DATABASE_PATH - путь к БД
│   └─ DEBUG_MODE - режим отладки
│
├── 📄 database.py
│   ├─ Класс Database для работы с SQLite
│   ├─ add_homework() - добавление нового задания
│   ├─ get_homework() - получение задания по дате
│   ├─ update_homework() - редактирование задания
│   ├─ delete_homework() - удаление задания
│   └─ get_all_homework() - получение всех заданий
│
├── 📄 handlers.py
│   ├─ Обработчики команд (/start, /help и т.д.)
│   ├─ Состояния FSM для добавления, редактирования, удаления
│   ├─ Обработчики callback-кнопок
│   ├─ Логика выбора даты
│   └─ Отправка заданий пользователям
│
├── 📄 requirements.txt
│   ├─ aiogram==3.3.0
│   └─ python-dotenv==1.0.0
│
├── 📄 homework.db
│   ├─ БД SQLite (создается автоматически)
│   └─ Таблица: homework
│
├── 📄 README.md
│   └─ Инструкция по установке
│
├── 📄 EXAMPLES.md
│   └─ Примеры и FAQ
│
└── 📄 ARCHITECTURE.md
    └─ Этот файл


═══════════════════════════════════════════════════════════════════════════════

🔄 ЖИЗНЕННЫЙ ЦИКЛ СООБЩЕНИЯ

1. Пользователь отправляет сообщение в Telegram
                    ↓
2. Telegram отправляет его на Telegram API
                    ↓
3. Бот через polling получает обновление
                    ↓
4. Dispatcher распределяет обновление
                    ↓
5. Router выбирает подходящий обработчик
   ├─ Проверка Command (@router.message(Command("start")))
   ├─ Проверка CallbackQuery (@router.callback_query(F.data))
   ├─ Проверка State (@router.message(State))
   └─ Проверка других условий (@router.message())
                    ↓
6. Обработчик выполняет логику
   ├─ Проверка прав (если админ-функция)
   ├─ Работа с БД (database.py)
   ├─ Создание сообщения/кнопок
   └─ Отправка ответа в Telegram
                    ↓
7. Telegram отправляет сообщение пользователю
                    ↓
8. Пользователь получает ответ


═══════════════════════════════════════════════════════════════════════════════

🎯 ОСНОВНЫЕ КЛАССЫ И ФУНКЦИИ

📦 database.py
───────────────

class Database:
    def __init__(db_path: str)
        • Инициализация подключения
        • Создание таблицы если не существует

    def add_homework(date: str, text: str, photo_ids: List[str]) → bool
        • Добавление нового задания
        • Возвращает True/False

    def get_homework(date: str) → Optional[Tuple[str, List[str]]]
        • Получение задания по дате
        • Возвращает (текст, список фото) или None

    def update_homework(date: str, text: str, photo_ids: List[str]) → bool
        • Обновление существующего задания
        • Возвращает True/False

    def delete_homework(date: str) → bool
        • Удаление задания по дате
        • Возвращает True/False

    def get_all_homework() → List[Tuple[str, str, List[str]]]
        • Получение всех заданий
        • Возвращает список (дата, текст, фото)

    def homework_exists(date: str) → bool
        • Проверка существования задания
        • Возвращает True/False


🎯 handlers.py
───────────────

FSM Состояния:

class AddHomeworkStates(StatesGroup):
    waiting_for_date: State
    waiting_for_text: State
    waiting_for_photos: State

class EditHomeworkStates(StatesGroup):
    waiting_for_date: State
    waiting_for_action: State
    waiting_for_new_text: State
    waiting_for_new_photos: State

class DeleteHomeworkStates(StatesGroup):
    waiting_for_date: State


Основные обработчики:

@router.message(Command("start"))
async def cmd_start(message: Message)
    • Главное меню
    • Разные кнопки для админа и пользователей

@router.callback_query(F.data == "admin_panel")
async def admin_panel(query: CallbackQuery)
    • Админ панель с опциями

@router.message(AddHomeworkStates.waiting_for_date)
async def process_add_date(message: Message, state: FSMContext)
    • Обработка даты при добавлении

async def show_homework(query: CallbackQuery, date_str: str)
    • Показ задания по дате


═══════════════════════════════════════════════════════════════════════════════

🔐 ПОТОК РАЗРЕШЕНИЙ

Админ-функции:
┌─────────────────────────────────────┐
│ Проверка: if user_id == ADMIN_ID    │
│           │                         │
│           ├─ True → Доступ         │
│           └─ False → Отказ         │
└─────────────────────────────────────┘

Пользовательские функции:
┌─────────────────────────────────────┐
│ Доступны для всех                   │
│ └─ Просмотр по датам                │
└─────────────────────────────────────┘


═══════════════════════════════════════════════════════════════════════════════

💾 РАБОТА С БД

Инициализация:
  db = Database()  # Создает homework.db и таблицу

Добавление:
  db.add_homework("16.02.2026", "Прочитать главу 5", ["photo_id_1"])

Получение:
  text, photos = db.get_homework("16.02.2026")

Обновление:
  db.update_homework("16.02.2026", text="Новый текст")

Удаление:
  db.delete_homework("16.02.2026")

Получение всех:
  all_hw = db.get_all_homework()
  for date, text, photos in all_hw:
      print(f"{date}: {text}")


═══════════════════════════════════════════════════════════════════════════════

🎮 РАБОТА С КНОПКАМИ

Создание кнопок:
  keyboard = InlineKeyboardMarkup(inline_keyboard=[
      [InlineKeyboardButton(text="Текст", callback_data="callback_id")],
      [InlineKeyboardButton(text="Еще", callback_data="another_id")],
  ])

Отправка с кнопками:
  await message.answer("Выберите:", reply_markup=keyboard)

Обработка нажатия:
  @router.callback_query(F.data == "callback_id")
  async def handle_button(query: CallbackQuery):
      await query.message.edit_text("Вы нажали кнопку")
      await query.answer()


═══════════════════════════════════════════════════════════════════════════════

🤖 РАБОТА С FSM (STATE MACHINE)

Инициализация состояния:
  await state.set_state(AddHomeworkStates.waiting_for_date)

Сохранение данных:
  await state.update_data(date="16.02.2026")

Получение данных:
  data = await state.get_data()
  date = data.get("date")

Очистка состояния:
  await state.clear()


═══════════════════════════════════════════════════════════════════════════════

📨 ОТПРАВКА РАЗЛИЧНЫХ ТИПОВ СООБЩЕНИЙ

Текст:
  await message.answer("Привет!")

С кнопками:
  await message.answer("Выбирай", reply_markup=keyboard)

Фото:
  await message.answer_photo(photo_id, caption="Подпись")

Редактирование сообщения:
  await query.message.edit_text("Новый текст")

Уведомление (pop-up):
  await query.answer("Уведомление", show_alert=True)


═══════════════════════════════════════════════════════════════════════════════

🧵 ПОТОК ВЫПОЛНЕНИЯ ДЛЯ ДОБАВЛЕНИЯ ЗАДАНИЯ

1. Пользователь нажимает "➕ Добавить задание"
                    ↓
2. cmd_start → start_add_homework()
   • Проверяем: if user_id == ADMIN_ID ✓
   • Устанавливаем состояние: waiting_for_date
   • Отправляем: "Введите дату"
                    ↓
3. Админ пишет дату: "16.02.2026"
                    ↓
4. process_add_date() активирован
   • Валидируем формат даты ✓
   • Проверяем: не существует ли уже? ✓
   • Сохраняем дату в state
   • Устанавливаем состояние: waiting_for_text
   • Отправляем: "Введите текст"
                    ↓
5. Админ пишет текст задания
                    ↓
6. process_add_text() активирован
   • Валидируем текст (не пусто, минимум 3 символа) ✓
   • Сохраняем текст в state
   • Устанавливаем состояние: waiting_for_photos
   • Отправляем: "Отправьте фото (опционально)"
                    ↓
7. Админ отправляет фото (или пропускает)
                    ↓
8. process_add_photos() или skip_photos_handler()
   • Сохраняем фото ID в state
                    ↓
9. Админ нажимает "✅ Завершить"
                    ↓
10. finish_adding_homework()
    • Получаем все данные из state
    • Вызываем: db.add_homework(date, text, photos)
    • Сохраняются в SQLite ✓
    • Отправляем: "✅ Задание добавлено!"
    • Очищаем state: await state.clear()
                    ↓
11. Готово!


═══════════════════════════════════════════════════════════════════════════════

🔧 РАСШИРЕНИЕ ФУНКЦИОНАЛЬНОСТИ

Чтобы добавить новую функцию:

1. Добавьте новые поля в БД (database.py):
   ```
   ALTER TABLE homework ADD COLUMN deadline TEXT;
   ```

2. Обновите класс Database:
   ```
   def add_homework(self, date: str, text: str, photo_ids: List[str], deadline: str):
       # Добавьте новый параметр
   ```

3. Создайте новый обработчик (handlers.py):
   ```
   @router.callback_query(F.data == "new_feature")
   async def new_feature_handler(query: CallbackQuery):
       # Реализуйте логику
   ```

4. Добавьте кнопку для вызова новой функции


═══════════════════════════════════════════════════════════════════════════════

📊 SCHEMA БД

Таблица: homework

┌─────────────┬──────────────┬─────────┐
│ Название    │ Тип          │ Описание │
├─────────────┼──────────────┼─────────┤
│ id          │ INTEGER PK   │ ID      │
│ date        │ TEXT UNIQUE  │ Дата    │
│ text        │ TEXT         │ Текст   │
│ photo_ids   │ TEXT (JSON)  │ Фото    │
│ created_at  │ TIMESTAMP    │ Создан  │
│ updated_at  │ TIMESTAMP    │ Обновлен│
└─────────────┴──────────────┴─────────┘

Пример записи:
{
  id: 1,
  date: "16.02.2026",
  text: "Прочитать главу 5",
  photo_ids: '["AgACAgIAAxkBAAIBZGXx5Kz..."]',
  created_at: "2026-02-15 10:30:00",
  updated_at: "2026-02-15 10:35:00"
}


═══════════════════════════════════════════════════════════════════════════════

🚀 ОПТИМИЗАЦИЯ

Для улучшения производительности:

1. Кеширование часто запрашиваемых заданий
2. Индексация по дате в БД
3. Асинхронная отправка уведомлений
4. Пагинация при выводе больших списков
5. Ограничение размера фото
6. Компрессия фото перед сохранением


═══════════════════════════════════════════════════════════════════════════════

✨ ГОТОВО! Теперь вы понимаете архитектуру бота! 🎉
