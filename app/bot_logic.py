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
                    remove_link_from_file(src)
                    try:
                        updates = await self.client(ImportChatInviteRequest(invite_hash))
                        if updates.chats:
                            entity = updates.chats[0]
                            logger.info(f"✅ Вступил в приватный: {entity.title} (ID: {entity.id})")
                    except UserAlreadyParticipantError:
                        entity = await self.client.get_entity(src)
                        remove_link_from_file(src)
                        logger.info(f"ℹ️ Уже в чате: {entity.title} (ID: {entity.id})")

                else:
                    entity = await self.client.get_entity(src)
                    await self.client(JoinChannelRequest(entity))
                    logger.info(f"✅ Подписан на: {src}")

                if entity:
                    save_source_id(entity.id)
                await asyncio.sleep(2)
            except FloodWaitError as e:
                logger.warning(f"⏳ Слишком много запросов! Ждем {e.seconds} сек...")
                await asyncio.sleep(e.seconds)
            except Exception as e:
                logger.error(f"⚠️ Ошибка {src}: {e}")

    async def _wait_smart_delay(self):
        now = time.time()
        wait = (self.last_post_time + POST_DELAY) - now
        if wait > 0:
            logger.info(f"⏳ Пауза перед постом: {int(wait)}с")
            await asyncio.sleep(wait)

    async def _send_to_dest(self, paths, text):
        """Унифицированная отправка медиа и текста"""
        try:
            if paths:
                if len(text) > 1024:
                    await self.client.send_file(DEST, paths)
                    if text:
                        await asyncio.sleep(1)
                        for chunk in split_text(text):
                            await self.client.send_message(DEST, chunk)
                            await asyncio.sleep(0.5)
                else:
                    await self.client.send_file(DEST, paths, caption=text or None)
            elif text:
                for chunk in split_text(text):
                    await self.client.send_message(DEST, chunk)
                    await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке в DEST: {e}")

    async def send_album(self, gid):
        """Сборка и отправка медиа-группы"""
        try:
            await asyncio.sleep(ALBUM_SILENCE_TIMEOUT)
        except asyncio.CancelledError:
            return

        data = self.albums.pop(gid, None)
        if not data:
            return

        paths = await asyncio.gather(*data['tasks'], return_exceptions=True)
        valid_paths = [p for p in paths if isinstance(p, str) and Path(p).exists()]

        full_text = "\n".join([t for t in data['texts'] if t]).strip()
        rewritten = ai.rewrite_text(full_text) if full_text else ""

        async with self.post_lock:
            await self._wait_smart_delay()
            await self._send_to_dest(valid_paths, rewritten)
            self.last_post_time = time.time()

        for p in valid_paths:
            Path(p).unlink(missing_ok=True)

    async def process_message(self, event):
        """Обработка входящих сообщений"""
        chat_id = event.chat_id
        # Быстрая проверка по нормализованным ID
        if chat_id not in SOURCES_IDS:
            return

        if is_seen(chat_id, event.id):
            return
        mark_seen(chat_id, event.id)

        msg = event.message
        gid = msg.grouped_id
        text = (msg.message or "").strip()
        dl_task = asyncio.create_task(self.client.download_media(msg, file=TEMP_DIR))

        if gid:
            # Альбом (Debounce)
            if gid not in self.albums:
                self.albums[gid] = {'tasks': [], 'texts': [], 'timer_task': None}

            self.albums[gid]['tasks'].append(dl_task)
            if text:
                self.albums[gid]['texts'].append(text)

            if self.albums[gid]['timer_task']:
                self.albums[gid]['timer_task'].cancel()

            self.albums[gid]['timer_task'] = asyncio.create_task(self.send_album(gid))
        else:
            # Одиночное сообщение
            path = await dl_task
            rewritten = ai.rewrite_text(text) if text else ""

            async with self.post_lock:
                await self._wait_smart_delay()
                try:
                    paths = [path] if path and Path(path).exists() else []
                    await self._send_to_dest(paths, rewritten)
                    self.last_post_time = time.time()
                except Exception as e:
                    logger.error(f"Ошибка одиночного поста: {e}")
                finally:
                    if path:
                        Path(path).unlink(missing_ok=True)

    async def run(self):
        await self.client.start(phone=PHONE)
        await self.setup()
        await self.join_sources()
        self.client.add_event_handler(self.process_message, events.NewMessage())
        print("🚀 Бот запущен и слушает каналы...")
        await self.client.run_until_disconnected()