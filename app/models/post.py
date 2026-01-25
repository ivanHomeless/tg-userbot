from sqlalchemy import (
    Column, BigInteger, String, Text,
    TIMESTAMP, ForeignKey, Integer, Index
)
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSONB
from app.models.base import Base


class Post(Base):
    """
    Готовые посты для публикации
    
    Создаются из обработанных сообщений в message_queue
    """
    
    __tablename__ = 'posts'
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    grouped_id = Column(BigInteger, nullable=True)  # связь частей альбома
    original_source_id = Column(
        BigInteger,
        ForeignKey('sources.chat_id', ondelete='SET NULL'),
        nullable=True
    )
    
    final_text = Column(Text, nullable=True)  # финальный текст для публикации
    
    status = Column(String(20), default='scheduled')  # scheduled, posting, posted, failed
    scheduled_at = Column(TIMESTAMP, nullable=True)
    posted_at = Column(TIMESTAMP, nullable=True)
    post_error = Column(Text, nullable=True)
    
    created_at = Column(TIMESTAMP, server_default=func.now())
    
    # Связи
    media = relationship("PostMedia", back_populates="post", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index('idx_posts_scheduled', 'status', 'scheduled_at',
              postgresql_where=(status == 'scheduled')),
    )
    
    def __repr__(self):
        return f"<Post(id={self.id}, status={self.status}, scheduled={self.scheduled_at})>"


class PostMedia(Base):
    """
    Медиа файлы, прикреплённые к постам
    
    Хранит данные для InputMedia при публикации
    """
    
    __tablename__ = 'post_media'
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    post_id = Column(
        BigInteger,
        ForeignKey('posts.id', ondelete='CASCADE'),
        nullable=False
    )
    message_queue_id = Column(
        BigInteger,
        ForeignKey('message_queue.id', ondelete='SET NULL'),
        nullable=True
    )
    
    media_type = Column(String(50), nullable=False)  # photo, video, document
    order_num = Column(Integer, default=0)  # порядок в альбоме
    
    # Данные для InputMedia
    media_file_id = Column(BigInteger, nullable=True)
    media_access_hash = Column(BigInteger, nullable=True)
    media_file_reference = Column(JSONB, nullable=True)  # bytes в виде list
    
    # Связи
    post = relationship("Post", back_populates="media")
    
    __table_args__ = (
        Index('idx_media_post_id', 'post_id'),
    )
    
    def __repr__(self):
        return f"<PostMedia(id={self.id}, post_id={self.post_id}, type={self.media_type})>"
