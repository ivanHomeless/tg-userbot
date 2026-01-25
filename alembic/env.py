from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context
import asyncio

# Добавляем корень проекта в path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Импортируем базу и модели
from app.models.base import Base
from app.config import DATABASE_URL

# ВАЖНО: Импортируем ВСЕ модели, чтобы Alembic их видел
from app.models.source import Source
from app.models.message import MessageQueue
from app.models.post import Post, PostMedia

# Конфиг Alembic
config = context.config

# Устанавливаем URL из .env
config.set_main_option('sqlalchemy.url', DATABASE_URL)

# Логирование
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Метаданные моделей
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Миграции в режиме 'offline' (генерация SQL)
    
    Используется для создания SQL скриптов без подключения к БД
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Выполнение миграций через connection"""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Асинхронные миграции для asyncpg"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """
    Миграции в режиме 'online' (прямое подключение к БД)
    
    Используется при запуске alembic upgrade head
    """
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
