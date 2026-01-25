from sqlalchemy import (
    Column, BigInteger, String, Text, Boolean,
    TIMESTAMP, ForeignKey, LargeBinary, Index, UniqueConstraint
)
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.models.base import Base


class MessageQueue(Base):
    """
    Очередь сообщений для обработки
    
    Хранит оригинальные сообщения, рерайты и статусы обработки
    """
    
    __tablename__ = 'message_queue'
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    source_id = Column(
        BigInteger,
        ForeignKey('sources.chat_id', ondelete='CASCADE'),
        nullable=False
    )
    message_id = Column(BigInteger, nullable=False)
    grouped_id = Column(BigInteger, nullable=True)  # для альбомов
    
    # ============================================
    # ОРИГИНАЛЬНЫЕ ДАННЫЕ
    # ============================================
    original_text = Column(Text, nullable=True)
    media_type = Column(String(50), nullable=True)  # photo, video, document, null
    
    # ============================================
    # МЕДИА (для InputMedia при публикации)
    # ============================================
    media_file_id = Column(BigInteger, nullable=True)
    media_access_hash = Column(BigInteger, nullable=True)
    media_file_reference = Column(LargeBinary, nullable=True)
    
    # Для форварда (запасной вариант)
    original_chat_id = Column(BigInteger, nullable=True)
    original_message_id = Column(BigInteger, nullable=True)
    
    # ============================================
    # ОБРАБОТАННЫЕ ДАННЫЕ (РЕРАЙТ)
    # ============================================
    rewritten_text = Column(Text, nullable=True)
    rewrite_status = Column(String(20), default='pending')  # pending, processing, done, failed, skipped
    rewrite_error = Column(Text, nullable=True)
    rewritten_at = Column(TIMESTAMP, nullable=True)
    ai_provider = Column(String(50), nullable=True)
    ai_model = Column(String(100), nullable=True)
    
    # ============================================
    # СКЛЕЙКА МЕДИА + ТЕКСТ
    # ============================================
    awaiting_text = Column(Boolean, default=False)  # ждём текст после медиа
    awaiting_until = Column(TIMESTAMP, nullable=True)  # до какого времени ждём
    linked_message_id = Column(BigInteger, nullable=True)  # ID связанного текста
    
    # ============================================
    # СТАТУС ОБРАБОТКИ
    # ============================================
    collected_at = Column(TIMESTAMP, server_default=func.now())
    ready_to_post = Column(Boolean, default=False)  # готово к публикации
    
    # Связи
    source = relationship("Source", back_populates="messages")
    
    __table_args__ = (
        UniqueConstraint('source_id', 'message_id', name='uq_source_message'),
        Index('idx_queue_rewrite_pending', 'rewrite_status',
              postgresql_where=(rewrite_status == 'pending')),
        Index('idx_queue_ready_to_post', 'ready_to_post',
              postgresql_where=(ready_to_post == True)),
        Index('idx_queue_grouped', 'grouped_id',
              postgresql_where=(grouped_id.isnot(None))),
        Index('idx_queue_awaiting', 'awaiting_text', 'awaiting_until',
              postgresql_where=(awaiting_text == True)),
    )
    
    def __repr__(self):
        return f"<MessageQueue(id={self.id}, source={self.source_id}, msg_id={self.message_id}, status={self.rewrite_status})>"
