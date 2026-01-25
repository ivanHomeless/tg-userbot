from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from app.models.message import MessageQueue
from app.models.post import Post, PostMedia
from app.config import MEDIA_ONLY_CAPTION
from app import ai
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class MessageProcessor:
    """
    Обработчик сообщений
    
    Выполняет три основные задачи:
    1. Рерайт текстов через AI
    2. Закрытие "ожидающих текст" медиа по таймауту
    3. Сборка готовых постов из обработанных сообщений
    """
    
    def __init__(self, db_session: AsyncSession):
        self.db = db_session
    
    async def process_pending_rewrites(self):
        """
        Шаг 1: Рерайт текстов
        
        Обрабатывает сообщения со статусом 'pending'
        """
        stmt = select(MessageQueue).where(
            MessageQueue.rewrite_status == 'pending',
            MessageQueue.original_text.isnot(None),
            MessageQueue.original_text != ''
        ).limit(50)
        
        result = await self.db.execute(stmt)
        messages = result.scalars().all()
        
        if not messages:
            return
        
        logger.info(f"📝 Найдено {len(messages)} сообщений для рерайта")
        
        for msg in messages:
            await self._rewrite_message(msg)
    
    async def _rewrite_message(self, msg: MessageQueue):
        """Рерайт одного сообщения через AI"""
        try:
            msg.rewrite_status = 'processing'
            await self.db.commit()
            
            # РЕРАЙТ
            rewritten = ai.rewrite_text(msg.original_text)
            
            # Сохраняем результат
            msg.rewritten_text = rewritten
            msg.rewrite_status = 'done'
            msg.rewritten_at = datetime.utcnow()
            msg.ai_provider = ai._PROVIDER
            msg.ai_model = ai.get_current_model()
            
            await self.db.commit()
            logger.info(f"✅ Рерайт готов: msg_id={msg.id}")
            
        except Exception as e:
            msg.rewrite_status = 'failed'
            msg.rewrite_error = str(e)
            await self.db.commit()
            logger.error(f"❌ Ошибка рерайта msg_id={msg.id}: {e}")
    
    async def close_expired_awaiting(self):
        """
        Шаг 2: Закрытие медиа, которые не дождались текста
        
        Запускать каждые 15-30 секунд
        """
        now = datetime.utcnow()
        
        stmt = select(MessageQueue).where(
            and_(
                MessageQueue.awaiting_text == True,
                MessageQueue.awaiting_until <= now
            )
        )
        
        result = await self.db.execute(stmt)
        expired = result.scalars().all()
        
        if not expired:
            return
        
        logger.info(f"⏰ Найдено {len(expired)} медиа с истёкшим ожиданием текста")
        
        for msg in expired:
            msg.awaiting_text = False
            
            # Если текста так и не пришло
            if not msg.original_text:
                msg.original_text = ""
                msg.rewrite_status = 'skipped'
                logger.info(f"📸 Медиа без текста (тайм-аут): {msg.source_id}/{msg.message_id}")
        
        await self.db.commit()
    
    async def build_posts_from_messages(self):
        """
        Шаг 3: Сборка готовых постов
        
        Обрабатывает:
        - Альбомы (grouped_id)
        - Одиночные сообщения
        """
        # Берём все готовые к сборке сообщения
        stmt = select(MessageQueue).where(
            MessageQueue.ready_to_post == False,
            or_(
                MessageQueue.rewrite_status == 'done',
                and_(
                    MessageQueue.rewrite_status == 'skipped',
                    MessageQueue.media_type.isnot(None)
                )
            )
        ).order_by(MessageQueue.collected_at)
        
        result = await self.db.execute(stmt)
        messages = result.scalars().all()
        
        if not messages:
            return
        
        logger.info(f"📦 Найдено {len(messages)} сообщений для сборки постов")
        
        # Группируем по типам
        albums = {}
        singles = []
        
        for msg in messages:
            if msg.grouped_id:
                if msg.grouped_id not in albums:
                    albums[msg.grouped_id] = []
                albums[msg.grouped_id].append(msg)
            else:
                singles.append(msg)
        
        # Обрабатываем альбомы
        for gid, msgs in albums.items():
            await self._build_album_post(msgs)
        
        # Обрабатываем одиночные
        for msg in singles:
            await self._build_single_post(msg)
    
    async def _build_album_post(self, messages: list[MessageQueue]):
        """Создаёт пост из альбома (несколько медиа)"""
        # Склеиваем весь рерайтнутый текст
        texts = [m.rewritten_text for m in messages if m.rewritten_text]
        final_text = "\n\n".join(texts) if texts else ""
        
        # Если альбом без текста — добавляем стандартную подпись
        if not final_text:
            final_text = MEDIA_ONLY_CAPTION
            logger.info(f"📸 Альбом без текста — добавлена стандартная подпись")
        
        # Создаём пост
        post = Post(
            grouped_id=messages[0].grouped_id,
            original_source_id=messages[0].source_id,
            final_text=final_text,
            status='scheduled',
            scheduled_at=datetime.utcnow()
        )
        self.db.add(post)
        await self.db.flush()
        
        # Добавляем медиа из альбома
        for idx, msg in enumerate(messages):
            if msg.media_type:
                media = PostMedia(
                    post_id=post.id,
                    message_queue_id=msg.id,
                    media_type=msg.media_type,
                    media_file_id=msg.media_file_id,
                    media_access_hash=msg.media_access_hash,
                    media_file_reference=list(msg.media_file_reference) if msg.media_file_reference else None,
                    order_num=idx
                )
                self.db.add(media)
        
        # Помечаем как готовые
        for msg in messages:
            msg.ready_to_post = True
        
        await self.db.commit()
        logger.info(f"✅ Альбом собран: grouped_id={post.grouped_id}, {len(messages)} файлов")
    
    async def _build_single_post(self, msg: MessageQueue):
        """Создаёт пост из одиночного сообщения"""
        final_text = msg.rewritten_text or ""
        
        # Если только медиа без текста — добавляем стандартную подпись
        if msg.media_type and not final_text:
            final_text = MEDIA_ONLY_CAPTION
            logger.info(f"📸 Медиа без текста — добавлена стандартная подпись")
        
        # Создаём пост
        post = Post(
            grouped_id=None,
            original_source_id=msg.source_id,
            final_text=final_text,
            status='scheduled',
            scheduled_at=datetime.utcnow()
        )
        self.db.add(post)
        await self.db.flush()
        
        # Если есть медиа — добавляем
        if msg.media_type:
            media = PostMedia(
                post_id=post.id,
                message_queue_id=msg.id,
                media_type=msg.media_type,
                media_file_id=msg.media_file_id,
                media_access_hash=msg.media_access_hash,
                media_file_reference=list(msg.media_file_reference) if msg.media_file_reference else None,
                order_num=0
            )
            self.db.add(media)
        
        msg.ready_to_post = True
        await self.db.commit()
        
        logger.info(f"✅ Одиночный пост: msg_id={msg.id}, media={msg.media_type}, text_len={len(final_text)}")
