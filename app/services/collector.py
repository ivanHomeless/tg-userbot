from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from app.models.message import MessageQueue
from app.models.source import Source
from app.config import AWAIT_TEXT_TIMEOUT
from datetime import datetime, timedelta
from collections import defaultdict
import asyncio
import logging

logger = logging.getLogger(__name__)


class MessageCollector:
    """
    Сборщик сообщений из источников

    Умная логика:
    - Склеивает медиа и текст, если они пришли раздельно
    - Ждёт текст после медиа в течение AWAIT_TEXT_TIMEOUT секунд
    - Использует Lock для синхронизации обработки альбомов
    - Использует таймеры для автоматической сборки альбомов
    """

    # Словарь Lock'ов по grouped_id (класс-уровень для всех инстансов)
    _album_locks = defaultdict(asyncio.Lock)

    # Словарь таймеров для альбомов: grouped_id -> asyncio.Task
    _album_timers = {}

    # Timeout для сборки альбома (секунды после последнего медиа)
    ALBUM_BUILD_TIMEOUT = 30

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

        # ДИАГНОСТИКА: логируем ВСЕ входящие сообщения (теперь только INCOMING благодаря фильтру)
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
        """Обработка медиа + текст (обычный случай)"""
        file_id, access_hash, file_ref = self._extract_media_data(msg)

        # ИСПРАВЛЕНИЕ: для альбомов статус всегда 'skipped'
        # (рерайт будет в _build_album_post)
        if msg.grouped_id:
            rewrite_status = 'skipped'
        else:
            rewrite_status = 'pending'

        # Для альбомов используем Lock для синхронизации
        if msg.grouped_id:
            async with self._album_locks[msg.grouped_id]:
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
                    rewrite_status=rewrite_status,
                    awaiting_text=False
                )
                self.db.add(queue_msg)
                await self.db.commit()

                # Обновляем collected_at у ВСЕХ медиа альбома
                await self._update_album_collected_at(chat_id, msg.grouped_id)
                logger.info(f"✅ Медиа+текст (альбом): {chat_id}/{msg.id} grouped_id={msg.grouped_id}")
        else:
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
                rewrite_status=rewrite_status,
                awaiting_text=False
            )
            self.db.add(queue_msg)
            await self.db.commit()
            logger.info(f"✅ Медиа+текст (одиночное): {chat_id}/{msg.id}")

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
            # ✅ Часть альбома — сохраняем без ожидания (с Lock для синхронизации)
            # Текст будет у ОДНОГО из медиа в альбоме (обычно первого)
            async with self._album_locks[msg.grouped_id]:
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

                # ВАЖНО: Обновляем collected_at у всех медиа альбома (сброс таймера)
                await self._update_album_collected_at(chat_id, msg.grouped_id)

                logger.info(f"📸 Альбом медиа без текста: {chat_id}/{msg.id} grouped_id={msg.grouped_id}")
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
            logger.info(f"⏳ Одиночное медиа без текста (ждём {AWAIT_TEXT_TIMEOUT}с): {chat_id}/{msg.id} grouped_id=None")
    
    async def _update_album_collected_at(self, chat_id: int, grouped_id: int):
        """
        Обновляет collected_at у всех медиа в альбоме

        Это аналог Timer.cancel() + Timer.start() в старом коде:
        - Каждое новое медиа сбрасывает таймер
        - Альбом собирается через 20 сек после ПОСЛЕДНЕГО медиа
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

        # Сброс/запуск таймера для автоматической сборки альбома
        await self._schedule_album_build(grouped_id)

        logger.debug(f"⏱️  Альбом {grouped_id}: таймер сброшен")

    async def _schedule_album_build(self, grouped_id: int):
        """
        Сбрасывает и перезапускает таймер сборки альбома

        Аналог Timer.cancel() + Timer.start() из старого кода
        """
        # Отменяем старый таймер (если есть)
        if grouped_id in self._album_timers:
            old_task = self._album_timers[grouped_id]
            if not old_task.done():
                old_task.cancel()
                logger.debug(f"⏱️  Альбом {grouped_id}: старый таймер отменен")

        # Создаем новый таймер
        async def build_album_after_timeout():
            try:
                await asyncio.sleep(self.ALBUM_BUILD_TIMEOUT)
                # Таймер сработал - НЕ пришло новое медиа за 20 секунд
                # Триггерим сборку через флаг (background_post_builder подхватит)
                logger.info(f"⏰ Альбом {grouped_id}: timeout истёк, готов к сборке")

                # Можно установить флаг или просто обновить collected_at
                # Background task подхватит при следующей проверке
            except asyncio.CancelledError:
                # Таймер отменен (пришло новое медиа)
                logger.debug(f"⏱️  Альбом {grouped_id}: таймер отменен (новое медиа)")

        task = asyncio.create_task(build_album_after_timeout())
        self._album_timers[grouped_id] = task

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
