from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from app.models.post import Post, PostMedia
from app.utils import split_text
from app.config import DEST, POST_DELAY, CAPTION_LIMIT
from datetime import datetime
from telethon import TelegramClient
from telethon.tl.types import (
    InputMediaPhoto, InputPhoto,
    InputMediaDocument, InputDocument
)
import asyncio
import logging

logger = logging.getLogger(__name__)


class PostPublisher:
    """
    Публикатор готовых постов
    
    Отправляет посты в целевой канал с учётом всех сценариев:
    1. Только текст
    2. Только медиа
    3. Медиа + текст (ВСЕГДА разделяем!)
    """
    
    def __init__(self, client: TelegramClient, db_session: AsyncSession):
        self.client = client
        self.db = db_session
    
    async def publish_scheduled_posts(self):
        """Публикует посты по расписанию"""
        now = datetime.utcnow()
        
        stmt = select(Post).where(
            and_(
                Post.status == 'scheduled',
                Post.scheduled_at <= now
            )
        ).order_by(Post.scheduled_at).limit(10)
        
        result = await self.db.execute(stmt)
        posts = result.scalars().all()
        
        if not posts:
            return
        
        logger.info(f"📤 Найдено {len(posts)} постов для публикации")
        
        for post in posts:
            try:
                await self._publish_post(post)
                post.status = 'posted'
                post.posted_at = datetime.utcnow()
                logger.info(f"✅ Опубликовано: post_id={post.id}")
            except Exception as e:
                logger.error(f"❌ Ошибка публикации {post.id}: {e}", exc_info=True)
                post.status = 'failed'
                post.post_error = str(e)
            
            await self.db.commit()
            await asyncio.sleep(POST_DELAY)
    
    async def _publish_post(self, post: Post):
        """
        Публикует один пост
        
        КРИТИЧНО: Всегда разделяем медиа и текст!
        """
        # Загружаем медиа
        stmt = select(PostMedia).where(
            PostMedia.post_id == post.id
        ).order_by(PostMedia.order_num)
        
        result = await self.db.execute(stmt)
        media_items = result.scalars().all()
        
        has_media = len(media_items) > 0
        has_text = post.final_text and len(post.final_text.strip()) > 0
        text = post.final_text.strip() if has_text else ""
        
        # СЦЕНАРИЙ 1: Только текст
        if not has_media and has_text:
            await self._send_text_only(text)
            return
        
        # СЦЕНАРИЙ 2: Только медиа
        if has_media and not has_text:
            await self._send_media_only(media_items)
            return
        
        # СЦЕНАРИЙ 3: Медиа + текст (ВСЕГДА разделяем!)
        if has_media and has_text:
            await self._send_media_and_text(media_items, text)
            return
        
        logger.warning(f"⚠️ Пост {post.id} пустой (нет медиа и текста)")
    
    async def _send_text_only(self, text: str):
        """Отправка только текста (чанками по 4096)"""
        chunks = split_text(text, limit=4096)
        for chunk in chunks:
            await self.client.send_message(DEST, chunk)
            await asyncio.sleep(0.5)
        logger.info(f"📝 Отправлен текст ({len(chunks)} частей)")
    
    async def _send_media_only(self, media_items: list[PostMedia]):
        """Отправка только медиа (без текста)"""
        media_objects = []
        for item in media_items:
            media_obj = self._restore_input_media(item)
            if media_obj:
                media_objects.append(media_obj)
        
        if media_objects:
            await self.client.send_file(
                DEST,
                media_objects,
                caption=None,
                force_document=False
            )
            logger.info(f"🖼️ Отправлено медиа: {len(media_objects)} файлов")
    
    async def _send_media_and_text(self, media_items: list[PostMedia], text: str):
        """
        Отправка медиа + текст
        
        УМНАЯ ЛОГИКА:
        - Если текст короткий (< CAPTION_LIMIT) → отправляем ВМЕСТЕ
        - Если текст длинный (>= CAPTION_LIMIT) → РАЗДЕЛЯЕМ
        
        Почему разделяем длинный текст:
        1. Caption ограничен (1024 без премиума, 2048 с премиумом)
        2. Длинный текст в caption обрезается Telegram
        3. Гарантия доставки полного текста
        """
        # Определяем стратегию
        text_length = len(text)
        use_caption = text_length < CAPTION_LIMIT
        
        if use_caption:
            # ✅ ВМЕСТЕ: текст короткий, можно в caption
            media_objects = []
            for item in media_items:
                media_obj = self._restore_input_media(item)
                if media_obj:
                    media_objects.append(media_obj)
            
            if media_objects:
                await self.client.send_file(
                    DEST,
                    media_objects,
                    caption=text,  # ← С подписью!
                    force_document=False
                )
                logger.info(f"🖼️📝 Отправлено медиа + текст вместе: {len(media_objects)} файлов, caption={text_length} симв")
        else:
            # ❌ РАЗДЕЛЯЕМ: текст длинный, не влезет в caption
            # 1. Отправляем медиа БЕЗ caption
            media_objects = []
            for item in media_items:
                media_obj = self._restore_input_media(item)
                if media_obj:
                    media_objects.append(media_obj)
            
            if media_objects:
                await self.client.send_file(
                    DEST,
                    media_objects,
                    caption=None,  # ← БЕЗ подписи!
                    force_document=False
                )
                logger.info(f"🖼️ Отправлено медиа: {len(media_objects)} файлов")
            
            # 2. Ждём гарантированной доставки медиа
            await asyncio.sleep(1.5)
            
            # 3. Текст отдельными сообщениями
            chunks = split_text(text, limit=4096)
            for chunk in chunks:
                await self.client.send_message(DEST, chunk)
                await asyncio.sleep(0.5)
            
            logger.info(f"📝 Отправлен длинный текст после медиа ({len(chunks)} частей, {text_length} симв)")
    
    def _restore_input_media(self, media_item: PostMedia):
        """
        Восстанавливает InputMedia из сохранённых данных
        
        Использует InputPhoto/InputDocument для быстрой пересылки
        """
        if not media_item.media_file_id:
            logger.warning(f"⚠️ Медиа {media_item.id} не имеет file_id")
            return None
        
        try:
            # Восстанавливаем file_reference из JSONB (список байтов)
            file_ref = bytes(media_item.media_file_reference) if media_item.media_file_reference else b''
            
            if media_item.media_type == 'photo':
                input_photo = InputPhoto(
                    id=media_item.media_file_id,
                    access_hash=media_item.media_access_hash,
                    file_reference=file_ref
                )
                return InputMediaPhoto(input_photo)
            
            elif media_item.media_type in ('video', 'document'):
                input_doc = InputDocument(
                    id=media_item.media_file_id,
                    access_hash=media_item.media_access_hash,
                    file_reference=file_ref
                )
                return InputMediaDocument(input_doc)
            
            else:
                logger.warning(f"⚠️ Неизвестный тип медиа: {media_item.media_type}")
                return None
        
        except Exception as e:
            logger.error(f"❌ Ошибка восстановления медиа {media_item.id}: {e}")
            return None
