from sqlalchemy import Column, BigInteger, String, Boolean, TIMESTAMP, Text, Index
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.models.base import Base


class Source(Base):
    """Источники - каналы/группы, откуда собираем сообщения"""
    
    __tablename__ = 'sources'
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(255), nullable=True)
    title = Column(String(255), nullable=True)
    join_link = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    added_at = Column(TIMESTAMP, server_default=func.now())
    last_check = Column(TIMESTAMP, nullable=True)
    last_message_id = Column(BigInteger, nullable=True)
    
    # Связи
    messages = relationship("MessageQueue", back_populates="source", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index('idx_sources_active', 'is_active', postgresql_where=(is_active == True)),
    )
    
    def __repr__(self):
        return f"<Source(chat_id={self.chat_id}, title='{self.title}', active={self.is_active})>"
