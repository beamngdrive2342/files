"""
Модуль работы с базой данных v2.0
Управление таблицей заданий по предметам
"""

import sqlite3
import json
from typing import List, Optional, Tuple, Dict
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
        self._homework_by_date_cache: Dict[str, Tuple[float, Dict[str, Tuple[str, List[str]]]]] = {}
        self._homework_by_date_ttl_sec = 45
        self.init_db()

    @staticmethod
    def _clone_homework_dict(
        homework_dict: Dict[str, Tuple[str, List[str]]]
    ) -> Dict[str, Tuple[str, List[str]]]:
        """Клонирует структуру ДЗ, чтобы внешний код не изменял кэш напрямую."""
        return {subject: (text, list(photos)) for subject, (text, photos) in homework_dict.items()}

    def _invalidate_homework_cache(self, target_date: Optional[str] = None):
        """Инвалидация кэша ДЗ по дате (точечно или полностью)."""
        with self._cache_lock:
            if target_date is None:
                self._homework_by_date_cache.clear()
            else:
                self._homework_by_date_cache.pop(target_date, None)

    def _get_cached_homework_by_date(self, target_date: str) -> Optional[Dict[str, Tuple[str, List[str]]]]:
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

    def _set_cached_homework_by_date(self, target_date: str, payload: Dict[str, Tuple[str, List[str]]]):
        """Сохранение ДЗ по дате в кэш."""
        expires_at = monotonic() + self._homework_by_date_ttl_sec
        with self._cache_lock:
            self._homework_by_date_cache[target_date] = (
                expires_at,
                self._clone_homework_dict(payload)
            )

    def _connect(self) -> sqlite3.Connection:
        """Создание соединения с безопасным таймаутом блокировок."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, subject)
            )
        """)

        # Таблица для отслеживания пользователей для уведомлений
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_homework_date ON homework(date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_homework_subject ON homework(subject)")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = NORMAL")

        conn.commit()
        conn.close()
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

    def add_homework(self, date: str, subject: str, text: str, photo_ids: List[str] = None) -> bool:
        """
        Добавление нового задания
        
        Args:
            date: Дата в формате ДД.МММ.ГГГГ
            subject: Предмет (Алгебра, Геометрия и т.д.)
            text: Текст задания
            photo_ids: Список ID фотографий
            
        Returns:
            True если успешно, False если ошибка
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()

            photo_json = json.dumps(photo_ids) if photo_ids else None

            cursor.execute("""
                INSERT INTO homework (date, subject, text, photo_ids)
                VALUES (?, ?, ?, ?)
            """, (date, subject, text, photo_json))

            conn.commit()
            conn.close()
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

            text, photos = homework
            photos.append(photo_id)

            return self.update_homework(date, subject, text, photos)

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

            text, photos = homework
            combined_text = f"{text}\n\n{new_text}"

            return self.update_homework(date, subject, combined_text, photos)

        except Exception as e:
            print(f"❌ Ошибка при добавлении текста: {e}")
            return False

    def get_homework(self, date: str, subject: str) -> Optional[Tuple[str, List[str]]]:
        """
        Получение задания по дате и предмету
        
        Args:
            date: Дата в формате ДД.МММ.ГГГГ
            subject: Предмет
            
        Returns:
            Кортеж (текст, список ID фото) или None если не найдено
        """
        try:
            cached_by_date = self._get_cached_homework_by_date(date)
            if cached_by_date is not None:
                cached_hw = cached_by_date.get(subject)
                if not cached_hw:
                    return None
                text, photos = cached_hw
                return text, list(photos)

            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT text, photo_ids FROM homework 
                WHERE date = ? AND subject = ?
            """, (date, subject))

            result = cursor.fetchone()
            conn.close()

            if result:
                text, photo_ids = result
                photos = json.loads(photo_ids) if photo_ids else []
                return text, photos
            return None

        except Exception as e:
            print(f"❌ Ошибка при получении: {e}")
            return None

    def get_homework_by_date(self, date: str) -> Dict[str, Tuple[str, List[str]]]:
        """
        Получение всех заданий на дату
        
        Args:
            date: Дата в формате ДД.МММ.ГГГГ
            
        Returns:
            Словарь {предмет: (текст, фото)}
        """
        try:
            cached = self._get_cached_homework_by_date(date)
            if cached is not None:
                return cached

            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT subject, text, photo_ids FROM homework 
                WHERE date = ?
                ORDER BY subject
            """, (date,))

            results = cursor.fetchall()
            conn.close()

            homework_dict = {}
            for subject, text, photo_ids in results:
                photos = json.loads(photo_ids) if photo_ids else []
                homework_dict[subject] = (text, photos)

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
            conn.close()

            homework_dict = {}
            for date, text, photo_ids in results:
                photos = json.loads(photo_ids) if photo_ids else []
                homework_dict[date] = (text, photos)

            return homework_dict

        except Exception as e:
            print(f"❌ Ошибка при получении по предмету: {e}")
            return {}

    def update_homework(self, date: str, subject: str, text: str, photo_ids: List[str] = None) -> bool:
        """
        Обновление задания
        
        Args:
            date: Дата
            subject: Предмет
            text: Новый текст
            photo_ids: Новые фото
            
        Returns:
            True если успешно
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()

            photo_json = json.dumps(photo_ids) if photo_ids else None

            cursor.execute("""
                UPDATE homework 
                SET text = ?, photo_ids = ?, updated_at = CURRENT_TIMESTAMP
                WHERE date = ? AND subject = ?
            """, (text, photo_json, date, subject))

            updated = cursor.rowcount > 0
            conn.commit()
            conn.close()
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
            conn.close()
            if deleted:
                self._invalidate_homework_cache(date)
            return deleted

        except Exception as e:
            print(f"❌ Ошибка при удалении: {e}")
            return False

    def get_all_homework(self) -> List[Tuple[str, str, str, List[str]]]:
        """
        Получение всех заданий
        
        Returns:
            Список кортежей (дата, предмет, текст, фото)
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT date, subject, text, photo_ids FROM homework 
                ORDER BY date DESC, subject
            """)

            results = cursor.fetchall()
            conn.close()

            homework_list = []
            for date, subject, text, photo_ids in results:
                photos = json.loads(photo_ids) if photo_ids else []
                homework_list.append((date, subject, text, photos))

            return homework_list

        except Exception as e:
            print(f"❌ Ошибка при получении всех: {e}")
            return []

    def homework_exists(self, date: str, subject: str) -> bool:
        """
        Проверка существования задания
        
        Args:
            date: Дата
            subject: Предмет
            
        Returns:
            True если существует
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute(
                "SELECT 1 FROM homework WHERE date = ? AND subject = ?",
                (date, subject)
            )

            result = cursor.fetchone()
            conn.close()

            return result is not None

        except Exception as e:
            print(f"❌ Ошибка при проверке: {e}")
            return False

    def register_user(self, user_id: int, username: str = None, first_name: str = None) -> bool:
        """
        Регистрация пользователя для получения уведомлений
        
        Args:
            user_id: Telegram user ID
            username: Username пользователя
            first_name: Имя пользователя
            
        Returns:
            True если успешно
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR IGNORE INTO users (user_id, username, first_name)
                VALUES (?, ?, ?)
            """, (user_id, username, first_name))

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            print(f"❌ Ошибка при регистрации пользователя: {e}")
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

            cursor.execute("SELECT user_id FROM users")

            results = cursor.fetchall()
            conn.close()

            return [row[0] for row in results]

        except Exception as e:
            print(f"❌ Ошибка при получении пользователей: {e}")
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
            conn.close()

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

            conn.close()
            return len(ids_to_delete)
        except Exception as e:
            print(f"❌ Ошибка при очистке старых ДЗ: {e}")
            return 0
