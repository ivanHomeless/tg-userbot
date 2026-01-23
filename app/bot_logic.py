import asyncio
import time
import logging
from pathlib import Path

from telethon.errors import FloodWaitError
from telethon import TelegramClient, events
from telethon.errors import UserAlreadyParticipantError
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest

from app.config import API_ID, API_HASH, PHONE, SOURCES, SOURCES_LINKS, SOURCES_IDS, DEST, TEMP_DIR, POST_DELAY, SESSION_NAME
from app.database import db_init, is_seen, mark_seen
from app.utils import split_text, save_source_id
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
        print("✅ База и папки готовы.")

    async def join_sources(self):
        """Универсальное вступление в каналы"""
        print(f"🔄 Проверка подписок ({len(SOURCES_LINKS)} источников)...")
        for src in SOURCES_LINKS:
            try:
                src = src.strip()
                entity = None  # Сюда сохраним объект чата

                if '+' in src or 'joinchat' in src:
                    invite_hash = src.split('/')[-1].replace('+', '')
                    try:
                        # Метод возвращает объект Updates, где в .chats лежит список чатов
                        updates = await self.client(ImportChatInviteRequest(invite_hash))
                        if updates.chats:
                            entity = updates.chats[0]
                            print(f"✅ Вступил в приватный: {entity.title} (ID: {entity.id})")
                    except UserAlreadyParticipantError:
                        # Если уже участник, просто запрашиваем информацию о чате
                        entity = await self.client.get_entity(src)
                        print(f"ℹ️ Уже в чате: {entity.title} (ID: {entity.id})")

                # Если получили entity — записываем ID в файл
                if entity:
                    save_source_id(entity.id)

                # 2. Публичные каналы (по ID, username или ссылке)
                else:
                    entity = await self.client.get_entity(src)
                    await self.client(JoinChannelRequest(entity))
                    print(f"✅ Подписан на: {src}")

                await asyncio.sleep(2)  # Анти-спам задержка
            except FloodWaitError as e:
                logger.warning(f"⏳ Слишком много запросов! Ждем {e.seconds} сек...")
                await asyncio.sleep(e.seconds)
                # После ожидания можно попробовать вступить снова или просто продолжить цикл
            except Exception as e:
                logger.error(f"⚠️ Ошибка {src}: {e}")

    async def _wait_smart_delay(self):
        now = time.time()
        wait = (self.last_post_time + POST_DELAY) - now
        if wait > 0:
            print(f"⏳ Пауза перед постом: {int(wait)}с")
            await asyncio.sleep(wait)

    async def send_album_final(self, gid):
        """Сборка и отправка медиа-группы"""
        try:
            await asyncio.sleep(ALBUM_SILENCE_TIMEOUT)
        except asyncio.CancelledError:
            return

        data = self.albums.pop(gid, None)
        if not data: return

        # Скачиваем файлы
        paths = await asyncio.gather(*data['tasks'], return_exceptions=True)
        valid_paths = [p for p in paths if isinstance(p, str) and Path(p).exists()]

        full_text = "\n".join([t for t in data['texts'] if t]).strip()
        rewritten = ai.rewrite_text(full_text) if full_text else ""

        async with self.post_lock:
            await self._wait_smart_delay()
            await self._send_to_dest(valid_paths, rewritten)
            self.last_post_time = time.time()

        for p in valid_paths: Path(p).unlink(missing_ok=True)

    async def _send_to_dest(self, paths, text):
        """Логика отправки: сначала медиа, потом текст (если длинный)"""
        try:
            if paths:
                if len(text) > 1024:
                    # Сначала альбом, потом текст отдельно
                    await self.client.send_file(DEST, paths)
                    await asyncio.sleep(1)
                    for chunk in split_text(text):
                        await self.client.send_message(DEST, chunk)
                else:
                    # Текст влезает в описание
                    await self.client.send_file(DEST, paths, caption=text)
            elif text:
                # Только текст
                for chunk in split_text(text):
                    await self.client.send_message(DEST, chunk)
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке в DEST: {e}")

    async def process_message(self, event):
        if not (event.is_channel or event.is_group):
            return

        # ← ВОЗВРАЩЕНА ПРОВЕРКА ИСТОЧНИКОВ (как было изначально)
        chat = await event.get_chat()
        username = getattr(chat, "username", None)
        src_id = f"@{username}".lower() if username else str(event.chat_id)

        if not any(s.strip().lower() in src_id for s in SOURCES):
            return

        chat_id = event.chat_id
        if is_seen(chat_id, event.id):
            return
        mark_seen(chat_id, event.id)

        msg = event.message
        gid = msg.grouped_id
        text = (msg.message or "").strip()

        # Запускаем скачивание сразу
        dl_task = asyncio.create_task(self.client.download_media(msg, file=TEMP_DIR))

        if gid:
            # Логика альбома (Debounce)
            if gid not in self.albums:
                self.albums[gid] = {'tasks': [], 'texts': [], 'timer_task': None}

            self.albums[gid]['tasks'].append(dl_task)
            if text:
                self.albums[gid]['texts'].append(text)

            if self.albums[gid]['timer_task']:
                self.albums[gid]['timer_task'].cancel()

            self.albums[gid]['timer_task'] = asyncio.create_task(self.send_album_final(gid))

        else:
            # Одиночное сообщение
            # ⚠️ СНАЧАЛА ЖДЕМ ЗАГРУЗКУ МЕДИА
            path = await dl_task

            # ✅ ТОЛЬКО ПОТОМ рерайтим текст
            rewritten = ai.rewrite_text(text) if text else ""

            async with self.post_lock:
                await self._wait_smart_delay()
                try:
                    if path and Path(path).exists():  # Проверяем существование
                        # --- Сначала ФОТО/ВИДЕО ---
                        if len(rewritten) > 1024:
                            await self.client.send_file(DEST, path)

                            # --- Потом ТЕКСТ ---
                            if rewritten:
                                await asyncio.sleep(1.0)
                                for chunk in split_text(rewritten):
                                    await self.client.send_message(DEST, chunk)
                                    await asyncio.sleep(0.5)
                        else:
                            await self.client.send_file(DEST, path, caption=rewritten)
                    elif rewritten:
                        for chunk in split_text(rewritten):
                            await self.client.send_message(DEST, chunk)
                            await asyncio.sleep(0.5)

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