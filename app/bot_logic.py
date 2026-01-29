import asyncio
import logging
from pathlib import Path

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, UserAlreadyParticipantError
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest

from app.config import API_ID, API_HASH, PHONE, DEST, TEMP_DIR, SESSION_NAME
from app.database.engine import SessionLocal, init_db
from app.services.collector import MessageCollector
from app.services.processor import MessageProcessor
from app.services.publisher import PostPublisher
from app.models.source import Source

logger = logging.getLogger(__name__)


class TGBot:
    """
    Главный класс бота
    
    Координирует работу всех сервисов:
    - Collector: сбор сообщений
    - Processor: рерайт и сборка постов
    - Publisher: публикация в канал
    """
    
    def __init__(self):
        self.client = TelegramClient(
            SESSION_NAME,
            API_ID,
            API_HASH,
            catch_up=True  # Догонять пропущенные updates
        )
        self.dest_chat_id = None  # Будет заполнено при старте
    
    async def setup(self):
        """Инициализация БД и папок"""
        await init_db()
        Path(TEMP_DIR).mkdir(exist_ok=True)
        logger.info("✅ База и папки готовы")
    
    async def join_sources(self):
        """
        Вступление в источники
        
        Загружает источники из БД и вступает в новые
        """
        async with SessionLocal() as session:
            from sqlalchemy import select
            
            # Загружаем все активные источники
            stmt = select(Source).where(Source.is_active == True)
            result = await session.execute(stmt)
            sources = result.scalars().all()
            
            logger.info(f"🔄 Проверка подписок ({len(sources)} источников)...")
            
            for source in sources:
                try:
                    # Проверяем доступность канала
                    entity = await self.client.get_entity(source.chat_id)
                    logger.info(f"✓ Источник доступен: {entity.title}")
                    
                    # Обновляем информацию
                    source.title = getattr(entity, 'title', source.title)
                    source.username = getattr(entity, 'username', source.username)
                    await session.commit()
                    
                except Exception as e:
                    logger.warning(f"⚠️ Проблема с источником {source.chat_id}: {e}")
                
                await asyncio.sleep(2)
    
    async def add_source_by_link(self, link: str):
        """
        Добавление нового источника по ссылке
        
        Поддерживает:
        - Публичные каналы: @channel или t.me/channel
        - Приватные: t.me/joinchat/XXX или t.me/+XXX
        """
        try:
            entity = None
            
            # Приватная ссылка
            if '+' in link or 'joinchat' in link:
                invite_hash = link.split('/')[-1].replace('+', '')
                try:
                    updates = await self.client(ImportChatInviteRequest(invite_hash))
                    if updates.chats:
                        entity = updates.chats[0]
                        logger.info(f"✅ Вступил в приватный: {entity.title}")
                except UserAlreadyParticipantError:
                    entity = await self.client.get_entity(link)
                    logger.info(f"ℹ️ Уже в чате: {entity.title}")
            else:
                # Публичный канал
                entity = await self.client.get_entity(link)
                await self.client(JoinChannelRequest(entity))
                logger.info(f"✅ Подписан на: {link}")
            
            # Сохраняем в БД
            if entity:
                async with SessionLocal() as session:
                    from sqlalchemy import select
                    
                    # Проверяем, нет ли уже
                    stmt = select(Source).where(Source.chat_id == entity.id)
                    result = await session.execute(stmt)
                    existing = result.scalar_one_or_none()
                    
                    if not existing:
                        new_source = Source(
                            chat_id=entity.id,
                            username=getattr(entity, 'username', None),
                            title=getattr(entity, 'title', None),
                            join_link=link,
                            is_active=True
                        )
                        session.add(new_source)
                        await session.commit()
                        logger.info(f"✅ Источник добавлен в БД: {entity.id}")
                    else:
                        logger.info(f"ℹ️ Источник уже в БД: {entity.id}")
        
        except FloodWaitError as e:
            logger.warning(f"⏳ FloodWait: {e.seconds} сек")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.error(f"❌ Ошибка добавления источника {link}: {e}")
    
    async def is_source_active(self, chat_id: int) -> bool:
        """
        Проверка активности источника

        Возвращает True, если источник есть в БД и активен
        """
        async with SessionLocal() as session:
            from sqlalchemy import select

            stmt = select(Source).where(Source.chat_id == chat_id)
            result = await session.execute(stmt)
            source = result.scalar_one_or_none()

            if not source or not source.is_active:
                return False

            return True

    async def shutdown(self):
        """Корректное завершение работы бота"""
        logger.info("🔄 Закрытие соединений...")

        # Закрываем Telethon
        if self.client.is_connected():
            await self.client.disconnect()
            logger.info("✅ Telethon отключён")

        # Закрываем пул соединений PostgreSQL
        from app.database.engine import engine
        await engine.dispose()
        logger.info("✅ Пул соединений PostgreSQL закрыт")
    
    async def run(self):
        """Запуск бота"""
        await self.client.start(phone=PHONE)
        await self.setup()

        # Получаем ID канала назначения (может быть username или ID)
        try:
            dest_entity = await self.client.get_entity(DEST)
            self.dest_chat_id = dest_entity.id
            logger.info(f"✅ Канал назначения: {dest_entity.title} (ID: {self.dest_chat_id})")
        except Exception as e:
            logger.error(f"❌ Не удалось получить канал назначения {DEST}: {e}")
            raise

        await self.join_sources()
        
        # ============================================
        # ОБРАБОТЧИК СООБЩЕНИЙ
        # ============================================

        # Буферы для сборки альбомов
        album_buffers = {}
        album_timers = {}

        @self.client.on(events.NewMessage())
        async def message_handler(event):
            """Единственный обработчик для ВСЕХ сообщений"""

            # DEST filtering
            if self.dest_chat_id and event.chat_id == self.dest_chat_id:
                return

            # Source validation
            if not await self.is_source_active(event.chat_id):
                return

            msg = event.message
            grouped_id = msg.grouped_id

            # АЛЬБОМ: буферизуем 3 секунды
            if grouped_id:
                if grouped_id not in album_buffers:
                    album_buffers[grouped_id] = []
                    logger.info(f"📥 ПЕРВОЕ фото альбома: msg_id={msg.id} grouped_id={grouped_id}")

                album_buffers[grouped_id].append(msg)
                current_count = len(album_buffers[grouped_id])

                logger.info(
                    f"📥 Фото альбома: msg_id={msg.id} grouped_id={grouped_id} | "
                    f"В буфере: {current_count} фото | Таймер: сброшен, новый отсчет 3с"
                )

                # Cancel previous timer
                if grouped_id in album_timers:
                    album_timers[grouped_id].cancel()

                # Start new 3-second timer
                async def process_album():
                    await asyncio.sleep(3.0)

                    if grouped_id in album_buffers:
                        messages = album_buffers.pop(grouped_id)
                        album_timers.pop(grouped_id, None)

                        msg_ids = [m.id for m in messages]
                        logger.info(
                            f"⏰ ТАЙМЕР ИСТЁК (3с): grouped_id={grouped_id} | "
                            f"Собрано {len(messages)} фото | msg_ids={msg_ids}"
                        )

                        # Create album event
                        class AlbumEvent:
                            def __init__(self, chat_id, messages):
                                self.chat_id = chat_id
                                self.messages = messages

                        album_event = AlbumEvent(event.chat_id, messages)

                        try:
                            async with SessionLocal() as session:
                                collector = MessageCollector(session)
                                await collector.collect_album(album_event)
                        except Exception as e:
                            logger.error(f"❌ Ошибка обработки альбома {grouped_id}: {e}", exc_info=True)

                album_timers[grouped_id] = asyncio.create_task(process_album())

            # ОДИНОЧНОЕ СООБЩЕНИЕ: обрабатываем сразу
            else:
                try:
                    async with SessionLocal() as session:
                        collector = MessageCollector(session)
                        await collector.collect_message(event)
                except Exception as e:
                    logger.error(f"❌ Ошибка обработки сообщения: {e}", exc_info=True)

        # ============================================
        # ФОНОВЫЕ ЗАДАЧИ
        # ============================================

        post_builder_lock = asyncio.Lock()

        async def background_rewriter():
            """Фоновый рерайт текстов (каждые 30 сек)"""
            while True:
                try:
                    async with SessionLocal() as session:
                        processor = MessageProcessor(session)
                        await processor.process_pending_rewrites()
                except asyncio.CancelledError:
                    logger.info("🛑 Остановка rewriter...")
                    break
                except Exception as e:
                    logger.error(f"❌ Ошибка в rewriter: {e}", exc_info=True)
                await asyncio.sleep(30)
        
        async def background_awaiting_closer():
            """Закрытие ожидающих текст медиа (каждые 15 сек)"""
            while True:
                try:
                    async with SessionLocal() as session:
                        processor = MessageProcessor(session)
                        await processor.close_expired_awaiting()
                except asyncio.CancelledError:
                    logger.info("🛑 Остановка awaiting_closer...")
                    break
                except Exception as e:
                    logger.error(f"❌ Ошибка в awaiting_closer: {e}", exc_info=True)
                await asyncio.sleep(15)
        
        async def background_post_builder():
            """Сборка постов из обработанных сообщений (каждые 3 сек)"""
            while True:
                try:
                    async with post_builder_lock:
                        async with SessionLocal() as session:
                            processor = MessageProcessor(session)
                            await processor.build_posts_from_messages()
                except asyncio.CancelledError:
                    logger.info("🛑 Остановка post_builder...")
                    break
                except Exception as e:
                    logger.error(f"❌ Ошибка в post_builder: {e}", exc_info=True)
                await asyncio.sleep(3)
        
        async def background_publisher():
            """Публикация готовых постов (каждые 15 секунд)"""
            while True:
                try:
                    async with SessionLocal() as session:
                        publisher = PostPublisher(self.client, session)
                        await publisher.publish_scheduled_posts()
                except asyncio.CancelledError:
                    logger.info("🛑 Остановка publisher...")
                    break
                except Exception as e:
                    logger.error(f"❌ Ошибка в publisher: {e}", exc_info=True)
                await asyncio.sleep(15)

        # ============================================
        # ЗАПУСК ВСЕХ ЗАДАЧ ПАРАЛЛЕЛЬНО
        # ============================================
        
        logger.info("🚀 Бот запущен и слушает каналы...")
        
        try:
            await asyncio.gather(
                self.client.run_until_disconnected(),
                background_rewriter(),
                background_awaiting_closer(),
                background_post_builder(),
                background_publisher()
            )
        except asyncio.CancelledError:
            logger.info("⚠️  Получен сигнал остановки, завершаем задачи...")
        finally:
            # Корректное завершение
            await self.shutdown()
            logger.info("✅ Все задачи завершены")
