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

    Два режима обработки:
    1. collect_album() - обрабатывает альбомы через events.Album (Telethon сам собирает все медиа)
    2. collect_message() - обрабатывает одиночные сообщения

    Умная логика:
    - Склеивает медиа и текст, если они пришли раздельно
    - Ждёт текст после одиночного медиа в течение AWAIT_TEXT_TIMEOUT секунд
    - Связывает текст с недавним альбомом, если текст пришел после альбома
    """

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def collect_album(self, event):
        """
        Обработка альбома через events.Album

        Telethon автоматически собрал все медиа в event.messages
        """
        chat_id = event.chat_id
        messages = event.messages  # Список всех медиа в альбоме

        if not messages:
            logger.warning(f"⚠️ Пустой альбом от {chat_id}")
            return

        # Проверяем активность источника
        stmt = select(Source).where(Source.chat_id == chat_id)
        result = await self.db.execute(stmt)
        source = result.scalar_one_or_none()

        if not source or not source.is_active:
            logger.debug(f"⏭️ Источник {chat_id} неактивен или не найден")
            return

        # Собираем ВСЕ тексты из всех сообщений альбома
        captions = []
        for msg in messages:
            if msg.message and len(msg.message.strip()) > 0:
                captions.append(msg.message)

        # Объединяем все подписи через двойной перенос
        caption = "\n\n".join(captions) if captions else None

        grouped_id = messages[0].grouped_id

        logger.info(
            f"📸 Альбом: {chat_id} grouped_id={grouped_id} "
            f"медиа={len(messages)} текстов={len(captions)}"
        )

        # Сохраняем все медиа альбома
        for msg in messages:
            # Проверяем дубликат
            stmt = select(MessageQueue).where(
                MessageQueue.source_id == chat_id,
                MessageQueue.message_id == msg.id
            )
            result = await self.db.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                logger.debug(f"⏭️ Дубликат медиа из альбома: {chat_id}/{msg.id}")
                continue

            file_id, access_hash, file_ref = self._extract_media_data(msg)

            # Текст сохраняем ТОЛЬКО к первому медиа
            is_first = (msg.id == messages[0].id)

            queue_msg = MessageQueue(
                source_id=chat_id,
                message_id=msg.id,
                grouped_id=grouped_id,
                original_text=caption if is_first else None,
                media_type=self._get_media_type(msg),
                media_file_id=file_id,
                media_access_hash=access_hash,
                media_file_reference=file_ref,
                original_chat_id=chat_id,
                original_message_id=msg.id,
                rewrite_status='skipped',  # Рерайт будет в processor для всего альбома
                awaiting_text=False
            )

            self.db.add(queue_msg)

        await self.db.commit()

        logger.info(
            f"✅ Альбом сохранен: grouped_id={grouped_id}, "
            f"{len(messages)} медиа, текстов={len(captions)}"
        )

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

        # ДИАГНОСТИКА: логируем ВСЕ входящие сообщения (DEST канал фильтруется в bot_logic.py)
        has_media_debug = bool(msg.photo or msg.video or msg.document or msg.voice)
        has_text_debug = bool(msg.message and len(msg.message.strip()) > 0)
        logger.info(
            f"📥 Входящее: {chat_id}/{msg.id} "
            f"grouped_id={msg.grouped_id} "
            f"has_media={has_media_debug} has_text={has_text_debug} "
            f"media_type={type(msg.media).__name__ if msg.media else None}"
        )

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
        has_media = msg.photo or msg.video or msg.document or msg.voice
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

        Проверяем:
        1. Есть ли одиночное медиа, которое ждёт текст (awaiting_text)
        2. Есть ли недавний альбом без текста от этого источника
        3. Иначе — обычное текстовое сообщение
        """
        now = datetime.utcnow()

        # 1. Ищем одиночное медиа, которое ждёт текст
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
            # ✅ Склеиваем с одиночным медиа
            media_msg.original_text = msg.message
            media_msg.awaiting_text = False
            media_msg.linked_message_id = msg.id
            media_msg.rewrite_status = 'pending'

            await self.db.commit()
            logger.info(f"🔗 Склеено: медиа {media_msg.message_id} + текст {msg.id}")
            return

        # 2. Ищем недавний альбом без текста от этого источника
        album_cutoff = now - timedelta(seconds=AWAIT_TEXT_TIMEOUT)

        # Находим любое медиа из недавнего альбома
        stmt = select(MessageQueue).where(
            and_(
                MessageQueue.source_id == chat_id,
                MessageQueue.grouped_id.isnot(None),
                MessageQueue.ready_to_post == False,
                MessageQueue.collected_at > album_cutoff,
                MessageQueue.message_id < msg.id
            )
        ).order_by(MessageQueue.collected_at.desc()).limit(1)

        result = await self.db.execute(stmt)
        album_msg = result.scalar_one_or_none()

        if album_msg:
            # Используем Lock для синхронизации привязки текста к альбому
            async with self._album_locks[album_msg.grouped_id]:
                # Проверяем: есть ли уже текст в этом альбоме?
                stmt_check = select(MessageQueue).where(
                    and_(
                        MessageQueue.source_id == chat_id,
                        MessageQueue.grouped_id == album_msg.grouped_id,
                        MessageQueue.original_text.isnot(None),
                        MessageQueue.original_text != ''
                    )
                ).limit(1)
                result_check = await self.db.execute(stmt_check)
                has_text = result_check.scalar_one_or_none()

                if not has_text:
                    # ✅ Альбом без текста — прикрепляем текст к первому медиа
                    stmt_first = select(MessageQueue).where(
                        and_(
                            MessageQueue.source_id == chat_id,
                            MessageQueue.grouped_id == album_msg.grouped_id
                        )
                    ).order_by(MessageQueue.message_id).limit(1)
                    result_first = await self.db.execute(stmt_first)
                    first_media = result_first.scalar_one_or_none()

                    if first_media:
                        first_media.original_text = msg.message
                        first_media.linked_message_id = msg.id
                        # Сбрасываем таймер альбома (даём время на сборку)
                        await self._update_album_collected_at(chat_id, album_msg.grouped_id)
                        await self.db.commit()
                        logger.info(
                            f"🔗 Склеено: альбом grouped_id={album_msg.grouped_id} "
                            f"+ текст {msg.id}"
                        )
                        return

        # 3. Обычное текстовое сообщение
        queue_msg = MessageQueue(
            source_id=chat_id,
            message_id=msg.id,
            grouped_id=msg.grouped_id,
            original_text=msg.message,
            media_type=None,
            rewrite_status='pending'
        )
        self.db.add(queue_msg)
        await self.db.commit()
        logger.info(f"✅ Текст без медиа: {chat_id}/{msg.id} (grouped_id={msg.grouped_id})")

    async def _handle_media_with_text(self, msg, chat_id):
        """Обработка одиночного медиа + текст (альбомы обрабатываются в collect_album)"""
        file_id, access_hash, file_ref = self._extract_media_data(msg)

        # Одиночное медиа (альбомы не должны попадать сюда)
        queue_msg = MessageQueue(
            source_id=chat_id,
            message_id=msg.id,
            grouped_id=None,
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
        logger.info(f"✅ Медиа+текст (одиночное): {chat_id}/{msg.id}")

    async def _handle_media_without_text(self, msg, chat_id):
        """
        Обработка одиночного медиа без текста (альбомы обрабатываются в collect_album)

        Логика: одиночное медиа → ждём текст 20 сек
        """
        file_id, access_hash, file_ref = self._extract_media_data(msg)

        # Одиночное медиа — ЖДЁМ текст
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
    
    async def _update_album_collected_at(self, chat_id: int, grouped_id: int):
        """
        Обновляет collected_at у всех медиа в альбоме

        Используется только когда текст приходит ПОСЛЕ альбома
        """
        from sqlalchemy import update

        now = datetime.utcnow()

        stmt = update(MessageQueue).where(
            and_(
                MessageQueue.source_id == chat_id,
                MessageQueue.grouped_id == grouped_id,
                MessageQueue.ready_to_post == False
            )
        ).values(collected_at=now)

        await self.db.execute(stmt)
        await self.db.commit()

        logger.debug(f"🔗 Альбом {grouped_id}: обновлен collected_at (привязан текст)")

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
