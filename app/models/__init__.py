"""
Модели SQLAlchemy для Telegram бота

ВАЖНО: Порядок импорта критичен для relationship()
"""

# Сначала импортируем Base
from app.models.base import Base

# Затем модели в правильном порядке (от простых к сложным)
from app.models.source import Source
from app.models.message import MessageQueue
from app.models.post import Post, PostMedia

# Экспортируем все модели
__all__ = [
    'Base',
    'Source',
    'MessageQueue',
    'Post',
    'PostMedia',
]