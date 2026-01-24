import asyncio
import time
import logging
from pathlib import Path

from telethon.errors import FloodWaitError
from telethon import TelegramClient, events
from telethon.errors import UserAlreadyParticipantError
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest

from app.config import API_ID, API_HASH, PHONE, SOURCES_LINKS, SOURCES_IDS, DEST, TEMP_DIR, POST_DELAY, SESSION_NAME
from app.database import db_init, is_seen, mark_seen
from app.utils import split_text, save_source_id, remove_link_from_file
from app import ai

logger = logging.getLogger(__name__)

ALBUM_SILENCE_TIMEOUT = 3.0



class TGBot:
    def __init__(self):
        self.client = TelegramClient(SESSION_NAME, API_ID, API_HASH, sequential_updates=True)
        self.post_lock = asyncio.Lock()
        self.last_post_time = 0.0
        self.albums = {}

    async def setup(self):
        db_init()
        Path(TEMP_DIR).mkdir(exist_ok=True)
        logger.info("✅ База и папки готовы.")

    async def join_sources(self):
        """Универсальное вступление в каналы"""
        logger.info(f"🔄 Проверка подписок ({len(SOURCES_IDS)} источников)...")
        for src in SOURCES_LINKS:
            try:
                src = src.strip()
                entity = None

                if '+' in src or 'joinchat' in src:
                    invite_hash = src.split('/')[-1].replace('+', '')
                    try:
                        updates = await self.client(ImportChatInviteRequest(invite_hash))
                        if updates.chats:
                            entity = updates.chats[0]
                            logger.info(f"✅ Вступил в приватный: {entity.title} (ID: {entity.id})")
                    except UserAlreadyParticipantError:
                        entity = await self.client.get_entity(src)
                        logger.info(f"ℹ️ Уже в чате: {entity.title} (ID: {entity.id})")
                else:
                    entity = await self.client.get_entity(src)
                    await self.client(JoinChannelRequest(entity))
                    logger.info(f"✅ Подписан на: {src}")

                if entity:
                    save_source_id(entity.id)
                    remove_link_from_file(src)
                await asyncio.sleep(2)
            except FloodWaitError as e:
                logger.warning(f"⏳ Слишком много запросов! Ждем {e.seconds} сек...")
                await asyncio.sleep(e.seconds)
            except Exception as e:
                remove_link_from_file(src)
                logger.error(f"⚠️ Ошибка {src}: {e}")

    async def _wait_smart_delay(self):
        """Умная задержка между постами"""
        now = time.time()
        wait = (self.last_post_time + POST_DELAY) - now
        if wait > 0:
            logger.info(f"⏳ Пауза перед постом: {int(wait)}с")
            await asyncio.sleep(wait)

    async def _send_to_dest(self, media_messages, text):
        """Отправка медиа (через объекты Message) и текста без скачивания"""
        try:
            # Очищаем список от пустых объектов
            valid_media = [m for m in (media_messages or []) if m and getattr(m, "media", None)]

            if valid_media:
                # 1. Отправляем медиа
                # Если текст длинный, caption=None, если короткий - сразу с текстом
                caption_to_send = text if text and len(text) <= 1024 else None
                result = await self.client.send_file(DEST, valid_media, caption=caption_to_send)
                
                # Быстрая проверка ответа сервера
                sent_msgs = result if isinstance(result, list) else [result]
                is_success = any(m and m.media for m in sent_msgs)

                if is_success:
                    logger.info(f"✅ Медиа доставлено (объектов: {len(sent_msgs)})")
                    # 2. Если текст длинный — шлем его после успешного медиа
                    if text and len(text) > 1024:
                        await asyncio.sleep(0.5)
                        chunks = list(split_text(text))
                        for chunk in chunks:
                            await self.client.send_message(DEST, chunk)
                            await asyncio.sleep(0.3)
                else:
                    logger.error("❌ Сервер не подтвердил доставку медиа")

            elif text:
                # Только текст без медиа
                chunks = list(split_text(text))
                for chunk in chunks:
                    await self.client.send_message(DEST, chunk)
                    await asyncio.sleep(0.3)
                    
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке в DEST: {e}", exc_info=True)
            raise

    async def send_album(self, gid):
        """Сборка и отправка медиа-группы"""
        try:
            await asyncio.sleep(ALBUM_SILENCE_TIMEOUT)
        except asyncio.CancelledError:
            return

        data = self.albums.pop(gid, None)
        if not data: return

        media_messages = [m for m in data.get('messages', []) if m and getattr(m, "media", None)]
        full_text = "\n".join([t for t in data['texts'] if t]).strip()
        rewritten = ai.rewrite_text(full_text) if full_text else ""

        async with self.post_lock:
            await self._wait_smart_delay()
            try:
                await self._send_to_dest(media_messages, rewritten)
                self.last_post_time = time.time()
            except Exception as e:
                logger.error(f"❌ Альбом {gid}: ошибка при отправке: {e}")

    async def process_message(self, event):
        """Обработка входящих сообщений"""
        chat_id = event.chat_id
        if chat_id not in SOURCES_IDS: return

        if is_seen(chat_id, event.id): return
        mark_seen(chat_id, event.id)

        msg = event.message
        gid = msg.grouped_id
        text = (msg.message or "").strip()

        if gid:
            if gid not in self.albums:
                self.albums[gid] = {'messages': [], 'texts': [], 'timer_task': None}

            if getattr(msg, "media", None):
                self.albums[gid]['messages'].append(msg)
            if text:
                self.albums[gid]['texts'].append(text)

            if self.albums[gid]['timer_task']:
                self.albums[gid]['timer_task'].cancel()
            self.albums[gid]['timer_task'] = asyncio.create_task(self.send_album(gid))
        else:
            rewritten = ai.rewrite_text(text) if text else ""
            async with self.post_lock:
                await self._wait_smart_delay()
                try:
                    media_messages = [msg] if getattr(msg, "media", None) else []
                    await self._send_to_dest(media_messages, rewritten)
                    self.last_post_time = time.time()
                except Exception as e:
                    logger.error(f"❌ Ошибка одиночного поста: {e}")

    async def run(self):
        await self.client.start(phone=PHONE)
        await self.setup()
        await self.join_sources()
        self.client.add_event_handler(self.process_message, events.NewMessage())
        print("🚀 Бот запущен и слушает каналы...")
        await self.client.run_until_disconnected()