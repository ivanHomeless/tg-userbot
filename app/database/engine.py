from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config import DATABASE_URL
import logging

logger = logging.getLogger(__name__)

# Создаём асинхронный движок PostgreSQL
engine = create_async_engine(
    DATABASE_URL,
    echo=False,  # Логирование SQL запросов (True для отладки)
    pool_size=10,  # Размер пула соединений
    max_overflow=20,  # Максимум дополнительных соединений
    pool_pre_ping=True,  # Проверка соединений перед использованием
)

# Фабрика сессий
SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)


async def init_db():
    """
    Инициализация базы данных
    
    Создаёт все таблицы, если их нет
    (В продакшене лучше использовать Alembic миграции)
    """
    from app.models.base import Base
    # Импортируем все модели, чтобы SQLAlchemy их увидел
    from app.models.source import Source
    from app.models.message import MessageQueue
    from app.models.post import Post, PostMedia
    
    async with engine.begin() as conn:
        # Создаём все таблицы
        await conn.run_sync(Base.metadata.create_all)
        logger.info("✅ База данных инициализирована")


async def get_session() -> AsyncSession:
    """Получить сессию базы данных (для dependency injection)"""
    async with SessionLocal() as session:
        yield session
