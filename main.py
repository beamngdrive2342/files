"""
Главный файл бота v2.0
Инициализация, запуск и управление жизненным циклом
"""

import asyncio
import logging
from aiogram import Dispatcher, Bot
from aiogram.types import BotCommand
from config import BOT_TOKEN, DEBUG_MODE, ADMIN_PASSWORD
from handlers import router

# ==================== КОНФИГУРАЦИЯ ЛОГИРОВАНИЯ ====================
logging.basicConfig(
    level=logging.INFO if not DEBUG_MODE else logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ==================== ИНИЦИАЛИЗАЦИЯ БОТА ====================
async def set_commands(bot: Bot):
    """Установка списка команд для меню бота"""
    commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="help", description="Справка по командам"),
    ]
    await bot.set_my_commands(commands)
    logger.info("✅ Команды установлены")


async def on_startup(bot: Bot):
    """Действия при запуске бота"""
    logger.info("=" * 50)
    logger.info("🚀 БОТ ДОМАШНИХ ЗАДАНИЙ V2.0")
    logger.info("=" * 50)
    await set_commands(bot)
    logger.info("✅ Бот готов к работе!")
    logger.info("📡 Слушаю сообщения...")
    logger.info("=" * 50)


async def on_shutdown(bot: Bot):
    """Действия при остановке бота"""
    logger.info("=" * 50)
    logger.info("🛑 Бот останавливается...")
    logger.info("=" * 50)


async def main():
    """Главная функция - запуск бота"""
    # Локальная проверка на случай запуска вне корректного .env
    bot_token = BOT_TOKEN
    if not bot_token:
        logger.error("❌ ОШИБКА: BOT_TOKEN не задан в .env")
        logger.error("   Создайте .env на основе .env.example и заполните BOT_TOKEN")
        return

    if ADMIN_PASSWORD in ("12345", "your_strong_password_here", ""):
        logger.warning("⚠️  ВНИМАНИЕ: Используется слабый или шаблонный ADMIN_PASSWORD!")
        logger.warning("   Измените ADMIN_PASSWORD в .env на более надежный!")

    # Инициализация бота и диспетчера
    bot = Bot(token=bot_token)
    dp = Dispatcher()

    # Подключаем маршрутизатор с обработчиками
    dp.include_router(router)

    # Запускаем действия при старте
    await on_startup(bot)

    try:
        # Удаляем вебхуки если они были
        await bot.delete_webhook(drop_pending_updates=True)
        
        # Запускаем long polling только по реально используемым типам апдейтов
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
    finally:
        await on_shutdown(bot)
        await bot.session.close()


if __name__ == "__main__":
    """Точка входа программы"""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⌨️  Бот остановлен пользователем")
