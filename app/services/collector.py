from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from app.models.message import MessageQueue
from app.models.source import Source
from app.config import AWAIT_TEXT_TIMEOUT
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class MessageCollector:
    """
    Сборщик сообщений из источников
    
    Умная логика:
    - Склеивает медиа и текст, если они пришли раздельно
    - Ждёт текст после медиа в течение AWAIT_TEXT_TIMEOUT секунд
    """
    
    def __init__(self, db_session: AsyncSession):
        self.db = db_session
    
    async def collect_message(self, event):
        """
        Сохраняет сообщение в очередь с умной логикой склейки
        
        Обрабатывает 4 случая:
        1. Текст без медиа → проверяем ожидающее медиа
        2. Медиа + текст → обычное сохранение
        3. Медиа без текста → помечаем "ждём текст"
        4. Пустое → игнорируем
        """
        msg = event.message
        chat_id = event.chat_id
        
        # Проверяем активность источника
        stmt = select(Source).where(Source.chat_id == chat_id)
        result = await self.db.execute(stmt)
        source = result.scalar_one_or_none()
        
        if not source or not source.is_active:
            logger.debug(f"⏭️ Источник {chat_id} неактивен или не найден")
            return
        
        # Проверяем дубликат
        stmt = select(MessageQueue).where(
            MessageQueue.source_id == chat_id,
            MessageQueue.message_id == msg.id
        )
        result = await self.db.execute(stmt)
        existing = result.scalar_one_or_none()
        
        if existing:
            logger.debug(f"⏭️ Дубликат: {chat_id}/{msg.id}")
            return
        
        # Определяем тип сообщения
        has_media = msg.photo or msg.video or msg.document
        has_text = msg.message and len(msg.message.strip()) > 0
        
        # СЛУЧАЙ 1: Текст без медиа
        if has_text and not has_media:
            await self._handle_text_message(msg, chat_id)
            return
        
        # СЛУЧАЙ 2: Медиа + текст
        if has_media and has_text:
            await self._handle_media_with_text(msg, chat_id)
            return
        
        # СЛУЧАЙ 3: Медиа без текста
        if has_media and not has_text:
            await self._handle_media_without_text(msg, chat_id)
            return
        
        # СЛУЧАЙ 4: Пустое сообщение
        logger.debug(f"⏭️ Пустое сообщение: {chat_id}/{msg.id}")
    
    async def _handle_text_message(self, msg, chat_id):
        """
        Обработка текста без медиа
        
        Проверяем: есть ли медиа, которое ждёт текст
        """
        now = datetime.utcnow()
        
        # Ищем недавнее медиа, которое ждёт текст
        stmt = select(MessageQueue).where(
            and_(
                MessageQueue.source_id == chat_id,
                MessageQueue.awaiting_text == True,
                MessageQueue.awaiting_until > now,
                MessageQueue.message_id < msg.id
            )
        ).order_by(MessageQueue.message_id.desc()).limit(1)
        
        result = await self.db.execute(stmt)
        media_msg = result.scalar_one_or_none()
        
        if media_msg:
            # ✅ Склеиваем с медиа
            media_msg.original_text = msg.message
            media_msg.awaiting_text = False
            media_msg.linked_message_id = msg.id
            media_msg.rewrite_status = 'pending'
            
            await self.db.commit()
            logger.info(f"🔗 Склеено: медиа {media_msg.message_id} + текст {msg.id}")
        else:
            # ❌ Это просто текстовое сообщение
            queue_msg = MessageQueue(
                source_id=chat_id,
                message_id=msg.id,
                original_text=msg.message,
                media_type=None,
                rewrite_status='pending'
            )
            self.db.add(queue_msg)
            await self.db.commit()
            logger.info(f"✅ Текст без медиа: {chat_id}/{msg.id}")
    
    async def _handle_media_with_text(self, msg, chat_id):
        """Обработка медиа + текст (обычный случай)"""
        file_id, access_hash, file_ref = self._extract_media_data(msg)
        
        queue_msg = MessageQueue(
            source_id=chat_id,
            message_id=msg.id,
            grouped_id=msg.grouped_id,
            original_text=msg.message,
            media_type=self._get_media_type(msg),
            media_file_id=file_id,
            media_access_hash=access_hash,
            media_file_reference=file_ref,
            original_chat_id=chat_id,
            original_message_id=msg.id,
            rewrite_status='pending',
            awaiting_text=False
        )
        
        self.db.add(queue_msg)
        await self.db.commit()
        logger.info(f"✅ Медиа+текст: {chat_id}/{msg.id}")
    
    async def _handle_media_without_text(self, msg, chat_id):
        """
        Обработка медиа без текста
        
        ВАЖНО: В альбомах только ОДНО медиа (обычно первое) имеет текст!
        
        Логика:
        - Если grouped_id == None → одиночное медиа → ждём текст 10 сек
        - Если grouped_id != None → часть альбома → НЕ ждём текст
        """
        file_id, access_hash, file_ref = self._extract_media_data(msg)
        
        # Проверяем: это часть альбома?
        if msg.grouped_id:
            # ✅ Часть альбома — сохраняем без ожидания
            # Текст будет у ОДНОГО из медиа в альбоме (обычно первого)
            queue_msg = MessageQueue(
                source_id=chat_id,
                message_id=msg.id,
                grouped_id=msg.grouped_id,
                original_text=None,
                media_type=self._get_media_type(msg),
                media_file_id=file_id,
                media_access_hash=access_hash,
                media_file_reference=file_ref,
                original_chat_id=chat_id,
                original_message_id=msg.id,
                rewrite_status='skipped',  # рерайтить нечего
                awaiting_text=False
            )
            
            self.db.add(queue_msg)
            await self.db.commit()
            logger.debug(f"📸 Альбом: медиа #{msg.id} (grouped_id={msg.grouped_id})")
        else:
            # ❌ Одиночное медиа — ЖДЁМ текст
            awaiting_until = datetime.utcnow() + timedelta(seconds=AWAIT_TEXT_TIMEOUT)
            
            queue_msg = MessageQueue(
                source_id=chat_id,
                message_id=msg.id,
                grouped_id=None,
                original_text=None,
                media_type=self._get_media_type(msg),
                media_file_id=file_id,
                media_access_hash=access_hash,
                media_file_reference=file_ref,
                original_chat_id=chat_id,
                original_message_id=msg.id,
                rewrite_status='skipped',
                awaiting_text=True,  # ЖДЁМ текст
                awaiting_until=awaiting_until
            )
            
            self.db.add(queue_msg)
            await self.db.commit()
            logger.info(f"⏳ Одиночное медиа без текста (ждём {AWAIT_TEXT_TIMEOUT}с): {chat_id}/{msg.id}")
    
    def _extract_media_data(self, msg):
        """Извлекает file_id, access_hash, file_reference из медиа"""
        file_id, access_hash, file_ref = None, None, None
        
        if msg.photo:
            file_id = msg.photo.id
            access_hash = msg.photo.access_hash
            file_ref = msg.photo.file_reference
        elif msg.video:
            file_id = msg.video.id
            access_hash = msg.video.access_hash
            file_ref = msg.video.file_reference
        elif msg.document:
            file_id = msg.document.id
            access_hash = msg.document.access_hash
            file_ref = msg.document.file_reference
        
        return file_id, access_hash, file_ref
    
    def _get_media_type(self, msg):
        """Определяет тип медиа"""
        if msg.photo:
            return 'photo'
        if msg.video:
            return 'video'
        if msg.document:
            return 'document'
        if msg.voice:
            return 'voice'
        return 'other'
