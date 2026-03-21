"""
Модуль работы с базой данных v2.0
Управление таблицей заданий по предметам
"""

import sqlite3
import json
from typing import List, Optional, Tuple, Dict
import logging

logger = logging.getLogger("homework_db")
from datetime import datetime, timedelta, date
from threading import Lock
from time import monotonic
from config import DATABASE_PATH


class Database:
    """Класс для работы с базой данных домашних заданий"""

    def __init__(self, db_path: str = DATABASE_PATH):
        """Инициализация подключения к БД"""
        self.db_path = db_path
        self._last_purge_date: Optional[date] = None
        self._cache_lock = Lock()
        self._conn_lock = Lock()
        self._persistent_conn: Optional[sqlite3.Connection] = None
        self._homework_by_date_cache: Dict[str, Tuple[float, Dict[str, Tuple[str, List[str], bool]]]] = {}
        self._homework_by_date_ttl_sec = 300
        # Кэш одобренных пользователей: {user_id: is_approved}
        self._approved_users_cache: Dict[int, bool] = {}
        self.init_db()

    @staticmethod
    def _clone_homework_dict(
        homework_dict: Dict[str, Tuple[str, List[str], bool]]
    ) -> Dict[str, Tuple[str, List[str], bool]]:
        """Клонирует структуру ДЗ, чтобы внешний код не изменял кэш напрямую."""
        return {subject: (text, list(photos), is_tb) for subject, (text, photos, is_tb) in homework_dict.items()}

    def _invalidate_homework_cache(self, target_date: Optional[str] = None):
        """Инвалидация кэша ДЗ по дате (точечно или полностью)."""
        with self._cache_lock:
            if target_date is None:
                self._homework_by_date_cache.clear()
            else:
                self._homework_by_date_cache.pop(target_date, None)

    def _get_cached_homework_by_date(self, target_date: str) -> Optional[Dict[str, Tuple[str, List[str], bool]]]:
        """Получение ДЗ по дате из кэша с проверкой TTL."""
        now = monotonic()
        with self._cache_lock:
            cached = self._homework_by_date_cache.get(target_date)
            if not cached:
                return None
            expires_at, payload = cached
            if now >= expires_at:
                self._homework_by_date_cache.pop(target_date, None)
                return None
            return self._clone_homework_dict(payload)

    def _set_cached_homework_by_date(self, target_date: str, payload: Dict[str, Tuple[str, List[str], bool]]):
        """Сохранение ДЗ по дате в кэш."""
        expires_at = monotonic() + self._homework_by_date_ttl_sec
        with self._cache_lock:
            self._homework_by_date_cache[target_date] = (
                expires_at,
                self._clone_homework_dict(payload)
            )

    def _connect(self) -> sqlite3.Connection:
        """Возвращает переиспользуемое соединение (thread-safe через Lock)."""
        with self._conn_lock:
            if self._persistent_conn is None:
                conn = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
                conn.execute("PRAGMA busy_timeout = 5000")
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA synchronous = NORMAL")
                conn.execute("PRAGMA cache_size = -8000")  # 8MB cache
                self._persistent_conn = conn
            return self._persistent_conn

    def init_db(self):
        """Создание таблицы если её нет"""
        conn = self._connect()
        cursor = conn.cursor()

        # Новая структура: дата + предмет = одна уникальная запись
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS homework (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                subject TEXT NOT NULL,
                text TEXT NOT NULL,
                photo_ids TEXT,
                is_textbook INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, subject)
            )
        """)

        # Миграция: добавляем колонку is_textbook если её нет
        try:
            cursor.execute("SELECT is_textbook FROM homework LIMIT 1")
        except sqlite3.OperationalError:
            logger.info("🔄 Миграция БД: добавляю колонку is_textbook...")
            cursor.execute("ALTER TABLE homework ADD COLUMN is_textbook INTEGER NOT NULL DEFAULT 0")
            logger.info("✅ Колонка is_textbook добавлена.")

        # Таблица для отслеживания пользователей для уведомлений
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_approved INTEGER NOT NULL DEFAULT 1
            )
        """)

        # Миграция: добавляем колонку is_approved если её нет (с дефолтом 1 для старых учеников)
        try:
            cursor.execute("SELECT is_approved FROM users LIMIT 1")
        except sqlite3.OperationalError:
            logger.info("🔄 Миграция БД: добавляю колонку is_approved...")
            cursor.execute("ALTER TABLE users ADD COLUMN is_approved INTEGER NOT NULL DEFAULT 1")
            logger.info("✅ Колонка is_approved добавлена.")

        # Таблица для хранения пожеланий учеников
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_homework_date ON homework(date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_homework_subject ON homework(subject)")

        conn.commit()
        self._maybe_purge_old_homework(keep_past_days=5)

    def _maybe_purge_old_homework(self, keep_past_days: int = 5) -> int:
        """
        Запускает очистку старых ДЗ не чаще 1 раза в сутки.
        Это уменьшает задержки при массовом добавлении/редактировании.
        """
        today = datetime.now().date()
        if self._last_purge_date == today:
            return 0
        removed = self.purge_old_homework(keep_past_days=keep_past_days)
        self._last_purge_date = today
        return removed

    def add_homework(self, date: str, subject: str, text: str, photo_ids: List[str] = None, is_textbook: bool = False) -> bool:
        """
        Добавление нового задания
        
        Args:
            date: Дата в формате ДД.МММ.ГГГГ
            subject: Предмет (Алгебра, Геометрия и т.д.)
            text: Текст задания
            photo_ids: Список ID фотографий
            is_textbook: Задание из учебника (True = ученики увидят кнопку «Найти решение»)
            
        Returns:
            True если успешно, False если ошибка
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()

            photo_json = json.dumps(photo_ids) if photo_ids else None

            cursor.execute("""
                INSERT INTO homework (date, subject, text, photo_ids, is_textbook)
                VALUES (?, ?, ?, ?, ?)
            """, (date, subject, text, photo_json, 1 if is_textbook else 0))

            conn.commit()
            self._invalidate_homework_cache(date)
            self._maybe_purge_old_homework(keep_past_days=5)
            return True

        except sqlite3.IntegrityError:
            return False
        except Exception as e:
            print(f"❌ Ошибка при добавлении: {e}")
            return False

    def add_photo_to_homework(self, date: str, subject: str, photo_id: str) -> bool:
        """
        Добавление фото к существующему заданию
        
        Args:
            date: Дата
            subject: Предмет
            photo_id: ID фото
            
        Returns:
            True если успешно
        """
        try:
            homework = self.get_homework(date, subject)
            if not homework:
                return False

            hw_text, photos, _ = homework
            photos.append(photo_id)

            return self.update_homework(date, subject, hw_text, photos)

        except Exception as e:
            print(f"❌ Ошибка при добавлении фото: {e}")
            return False

    def add_text_to_homework(self, date: str, subject: str, new_text: str) -> bool:
        """
        Добавление текста к существующему заданию
        
        Args:
            date: Дата
            subject: Предмет
            new_text: Новый текст
            
        Returns:
            True если успешно
        """
        try:
            homework = self.get_homework(date, subject)
            if not homework:
                return False

            hw_text, photos, _ = homework
            combined_text = f"{hw_text}\n\n{new_text}"

            return self.update_homework(date, subject, combined_text, photos)

        except Exception as e:
            print(f"❌ Ошибка при добавлении текста: {e}")
            return False

    def get_homework(self, date: str, subject: str) -> Optional[Tuple[str, List[str], bool]]:
        """
        Получение задания по дате и предмету
        
        Args:
            date: Дата в формате ДД.МММ.ГГГГ
            subject: Предмет
            
        Returns:
            Кортеж (текст, список ID фото, is_textbook) или None если не найдено
        """
        try:
            cached_by_date = self._get_cached_homework_by_date(date)
            if cached_by_date is not None:
                cached_hw = cached_by_date.get(subject)
                if not cached_hw:
                    return None
                hw_text, photos, is_tb = cached_hw
                return hw_text, list(photos), is_tb

            # Если кэша нет для этой даты — загрузим всю дату целиком (заполнит кэш)
            all_hw = self.get_homework_by_date(date)
            if subject in all_hw:
                hw_text, photos, is_tb = all_hw[subject]
                return hw_text, list(photos), is_tb
            return None

        except Exception as e:
            print(f"❌ Ошибка при получении: {e}")
            return None

    def get_homework_by_date(self, date: str) -> Dict[str, Tuple[str, List[str], bool]]:
        """
        Получение всех заданий на дату
        
        Args:
            date: Дата в формате ДД.МММ.ГГГГ
            
        Returns:
            Словарь {предмет: (текст, фото, is_textbook)}
        """
        try:
            cached = self._get_cached_homework_by_date(date)
            if cached is not None:
                return cached

            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT subject, text, photo_ids, is_textbook FROM homework 
                WHERE date = ?
                ORDER BY subject
            """, (date,))

            results = cursor.fetchall()

            homework_dict = {}
            for subject, hw_text, photo_ids, is_tb in results:
                photos = json.loads(photo_ids) if photo_ids else []
                homework_dict[subject] = (hw_text, photos, bool(is_tb))

            self._set_cached_homework_by_date(date, homework_dict)
            return homework_dict

        except Exception as e:
            print(f"❌ Ошибка при получении по дате: {e}")
            return {}

    def get_homework_by_subject(self, subject: str) -> Dict[str, Tuple[str, List[str]]]:
        """
        Получение всех заданий по предмету
        
        Args:
            subject: Предмет
            
        Returns:
            Словарь {дата: (текст, фото)}
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT date, text, photo_ids FROM homework 
                WHERE subject = ?
                ORDER BY date DESC
            """, (subject,))

            results = cursor.fetchall()

            homework_dict = {}
            for hw_date, hw_text, photo_ids in results:
                photos = json.loads(photo_ids) if photo_ids else []
                homework_dict[hw_date] = (hw_text, photos)

            return homework_dict

        except Exception as e:
            print(f"❌ Ошибка при получении по предмету: {e}")
            return {}

    def update_homework(self, date: str, subject: str, text: str, photo_ids: List[str] = None, is_textbook: bool | None = None) -> bool:
        """
        Обновление задания
        
        Args:
            date: Дата
            subject: Предмет
            text: Новый текст
            photo_ids: Новые фото
            is_textbook: Обновить флаг «из учебника» (None = не менять)
            
        Returns:
            True если успешно
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()

            photo_json = json.dumps(photo_ids) if photo_ids else None

            if is_textbook is not None:
                cursor.execute("""
                    UPDATE homework 
                    SET text = ?, photo_ids = ?, is_textbook = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE date = ? AND subject = ?
                """, (text, photo_json, 1 if is_textbook else 0, date, subject))
            else:
                cursor.execute("""
                    UPDATE homework 
                    SET text = ?, photo_ids = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE date = ? AND subject = ?
                """, (text, photo_json, date, subject))

            updated = cursor.rowcount > 0
            conn.commit()
            if updated:
                self._invalidate_homework_cache(date)
            return updated

        except Exception as e:
            print(f"❌ Ошибка при обновлении: {e}")
            return False

    def delete_homework(self, date: str, subject: str = None) -> bool:
        """
        Удаление задания
        
        Args:
            date: Дата
            subject: Предмет (если None - удалить все на дату)
            
        Returns:
            True если успешно
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()

            if subject:
                cursor.execute(
                    "DELETE FROM homework WHERE date = ? AND subject = ?",
                    (date, subject)
                )
            else:
                cursor.execute("DELETE FROM homework WHERE date = ?", (date,))

            deleted = cursor.rowcount > 0
            conn.commit()
            if deleted:
                self._invalidate_homework_cache(date)
            return deleted

        except Exception as e:
            print(f"❌ Ошибка при удалении: {e}")
            return False

    def get_all_homework(self) -> List[Tuple[str, str, str, List[str], bool]]:
        """
        Получение всех заданий
        
        Returns:
            Список кортежей (дата, предмет, текст, фото, is_textbook)
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT date, subject, text, photo_ids, is_textbook FROM homework 
                ORDER BY date DESC, subject
            """)

            results = cursor.fetchall()

            homework_list = []
            for hw_date, subject, hw_text, photo_ids, is_tb in results:
                photos = json.loads(photo_ids) if photo_ids else []
                homework_list.append((hw_date, subject, hw_text, photos, bool(is_tb)))

            return homework_list

        except Exception as e:
            print(f"❌ Ошибка при получении всех: {e}")
            return []

    def homework_exists(self, date: str, subject: str) -> bool:
        """
        Проверка существования задания.
        Использует кэш get_homework_by_date для быстрой проверки.
        """
        try:
            # Сначала проверяем кэш (мгновенно)
            cached = self._get_cached_homework_by_date(date)
            if cached is not None:
                return subject in cached
            # Если кэша нет для этой даты — загрузим всю дату (заполнит кэш)
            all_hw = self.get_homework_by_date(date)
            return subject in all_hw
        except Exception as e:
            print(f"❌ Ошибка при проверке: {e}")
            return False

    def register_user(self, user_id: int, username: str = None, first_name: str = None, is_approved: int = 1) -> bool:
        """
        Регистрация пользователя.
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR IGNORE INTO users (user_id, username, first_name, is_approved)
                VALUES (?, ?, ?, ?)
            """, (user_id, username, first_name, is_approved))

            conn.commit()
            # Обновляем кэш
            with self._cache_lock:
                self._approved_users_cache[user_id] = bool(is_approved)
            return True

        except Exception as e:
            print(f"❌ Ошибка при регистрации пользователя: {e}")
            return False

    def is_user_approved(self, user_id: int) -> bool:
        """
        Проверка, одобрен ли пользователь (с кэшем).
        """
        # Сначала проверяем кэш
        with self._cache_lock:
            cached = self._approved_users_cache.get(user_id)
            if cached is not None:
                return cached
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute("SELECT is_approved FROM users WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            approved = bool(result[0]) if result else False
            with self._cache_lock:
                self._approved_users_cache[user_id] = approved
            return approved
        except Exception as e:
            print(f"❌ Ошибка при проверке одобрения пользователя: {e}")
            return False

    def set_user_approved(self, user_id: int, is_approved: int) -> bool:
        """
        Установка статуса одобрения пользователя.
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET is_approved = ? WHERE user_id = ?", (is_approved, user_id))
            updated = cursor.rowcount > 0
            conn.commit()
            if updated:
                with self._cache_lock:
                    self._approved_users_cache[user_id] = bool(is_approved)
            return updated
        except Exception as e:
            print(f"❌ Ошибка при обновлении статуса одобрения: {e}")
            return False

    def delete_user(self, user_id: int) -> bool:
        """
        Удаление пользователя.
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            deleted = cursor.rowcount > 0
            conn.commit()
            if deleted:
                with self._cache_lock:
                    self._approved_users_cache.pop(user_id, None)
            return deleted
        except Exception as e:
            print(f"❌ Ошибка при удалении пользователя: {e}")
            return False

    def get_all_users(self) -> List[int]:
        """
        Получение списка всех зарегистрированных пользователей
        
        Returns:
            Список user_id
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute("SELECT user_id FROM users WHERE is_approved = 1")

            results = cursor.fetchall()

            return [row[0] for row in results]

        except Exception as e:
            print(f"❌ Ошибка при получении пользователей: {e}")
            return []

    def get_users_info(self) -> List[Tuple[int, str, str, str, int]]:
        """
        Получение подробного списка пользователей
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT user_id, username, first_name, registered_at, is_approved 
                FROM users 
                ORDER BY registered_at DESC
            """)

            results = cursor.fetchall()

            return results

        except Exception as e:
            print(f"❌ Ошибка при получении списка пользователей: {e}")
            return []

    def user_exists(self, user_id: int) -> bool:
        """
        Проверка существования пользователя
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            True если существует
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))

            result = cursor.fetchone()

            return result is not None

        except Exception as e:
            print(f"❌ Ошибка при проверке пользователя: {e}")
            return False

    def purge_old_homework(self, keep_past_days: int = 5) -> int:
        """
        Удаляет задания старше указанного количества дней назад.

        Returns:
            Количество удаленных записей
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute("SELECT id, date FROM homework")
            rows = cursor.fetchall()

            threshold = datetime.now().date() - timedelta(days=keep_past_days)
            ids_to_delete = []

            for hw_id, date_str in rows:
                try:
                    hw_date = datetime.strptime(date_str, "%d.%m.%Y").date()
                    if hw_date < threshold:
                        ids_to_delete.append(hw_id)
                except Exception:
                    # Невалидные даты не трогаем автоматически
                    continue

            if ids_to_delete:
                placeholders = ",".join("?" for _ in ids_to_delete)
                cursor.execute(f"DELETE FROM homework WHERE id IN ({placeholders})", ids_to_delete)
                conn.commit()
                self._invalidate_homework_cache()

            return len(ids_to_delete)
        except Exception as e:
            print(f"❌ Ошибка при очистке старых ДЗ: {e}")
            return 0

    def add_feedback(self, user_id: int, username: str, first_name: str, last_name: str, text: str) -> bool:
        """
        Сохранение пожелания ученика в БД.

        Returns:
            True если успешно
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO feedback (user_id, username, first_name, last_name, text)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, username, first_name, last_name, text))
            conn.commit()
            return True
        except Exception as e:
            print(f"❌ Ошибка при сохранении пожелания: {e}")
            return False

    def get_all_feedback(self) -> list:
        """
        Получение всех пожеланий (новые первыми).

        Returns:
            Список кортежей (id, user_id, username, first_name, last_name, text, created_at)
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, user_id, username, first_name, last_name, text, created_at
                FROM feedback
                ORDER BY created_at DESC
            """)
            rows = cursor.fetchall()
            return rows
        except Exception as e:
            print(f"❌ Ошибка при получении пожеланий: {e}")
            return []

    def delete_feedback(self, feedback_id: int) -> bool:
        """
        Удаление пожелания по ID.

        Returns:
            True если успешно
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM feedback WHERE id = ?", (feedback_id,))
            deleted = cursor.rowcount > 0
            conn.commit()
            return deleted
        except Exception as e:
            print(f"❌ Ошибка при удалении пожелания: {e}")
            return False

    def get_feedback_count(self) -> int:
        """
        Количество непрочитанных пожеланий.

        Returns:
            Целое число
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM feedback")
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            print(f"❌ Ошибка при подсчёте пожеланий: {e}")
            return 0
