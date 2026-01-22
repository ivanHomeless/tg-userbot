import asyncio
import time
import logging
from pathlib import Path
from telethon import TelegramClient, events
from telethon.tl.types import Channel
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.errors import UserAlreadyParticipantError

from app.config import API_ID, API_HASH, PHONE, SOURCES, DEST, TEMP_DIR, POST_DELAY, SESSION_NAME
from app.database import db_init, is_seen, mark_seen
from app.utils import split_text
from app import ai


logger = logging.getLogger(__name__)

# Время тишины, после которого считаем альбом собранным (в секундах)
ALBUM_SILENCE_TIMEOUT = 3.0

class TGBot:
    def __init__(self):
        self.client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
        self.post_lock = asyncio.Lock()
        self.last_post_time = 0.0

        # Структура: { group_id: { 'tasks': [Future], 'texts': [], 'timer_task': Task } }
        self.albums = {}

    async def setup(self):
        db_init()
        Path(TEMP_DIR).mkdir(exist_ok=True)
        logger.info("Компоненты готовы.")

    async def join_sources(self):
        """Подписка с обработкой 'уже участник'"""
        logger.info(f"🔄 Подписка на {len(SOURCES)} источников...")
        success_count = 0

        for src in SOURCES:
            try:
                clean_src = src.strip()

                # Приватная ссылка t.me/+hash
                if clean_src.startswith('https://t.me/+'):
                    invite_hash = clean_src.split('+')[-1].split('/')[0]

                    try:
                        # Пробуем импортировать
                        result = await self.client(ImportChatInviteRequest(invite_hash))
                        logger.info(f"✅ Новый приватный: {invite_hash}")
                        success_count += 1
                    except UserAlreadyParticipantError:
                        logger.info(f"ℹ️ Уже участник: {invite_hash}")  # ← Не warning!
                    except Exception as e:
                        logger.warning(f"⚠️ Приватный {invite_hash}: {e}")

                # Username
                elif clean_src.startswith('t.me/') or '@' in clean_src or clean_src.isalpha():
                    clean_username = clean_src.replace('t.me/', '').replace('@', '').strip('/')
                    await self.client(JoinChannelRequest(clean_username))
                    logger.info(f"✅ Username: @{clean_username}")
                    success_count += 1

                # ID
                elif clean_src.startswith('-100'):
                    entity = await self.client.get_entity(int(clean_src))
                    await self.client(JoinChannelRequest(entity))
                    logger.info(f"✅ ID: {clean_src}")
                    success_count += 1

                await asyncio.sleep(2)

            except Exception as e:
                logger.debug(f"Пропуск {src}: {e}")  # debug, а не warning

        logger.info(f"✅ Подписки завершены. Новых: {success_count}")

    async def _wait_smart_delay(self):
        """Умная задержка между постами, чтобы не спамить"""
        now = time.time()
        wait = (self.last_post_time + POST_DELAY) - now
        if wait > 0:
            logger.info(f"⏳ Жду {wait:.1f} сек перед отправкой...")
            await asyncio.sleep(wait)

    async def send_album_final(self, gid):
        """Отправка альбома: Сначала МЕДИА, потом ТЕКСТ"""
        try:
            await asyncio.sleep(ALBUM_SILENCE_TIMEOUT)
        except asyncio.CancelledError:
            return

        data = self.albums.pop(gid, None)
        if not data: return

        logger.info(f"📦 Сборка альбома {gid} завершена. Скачивание...")

        # 1. Скачивание
        paths = await asyncio.gather(*data['tasks'], return_exceptions=True)
        valid_paths = [p for p in paths if isinstance(p, str) and Path(p).exists()]

        # 2. Текст
        full_text = "\n".join([t for t in data['texts'] if t]).strip()

        if not valid_paths and not full_text: return

        # 3. Рерайт
        rewritten = ai.rewrite_text(full_text) if full_text else ""

        async with self.post_lock:
            await self._wait_smart_delay()

            try:
                if valid_paths:
                    # --- ВАРИАНТ С МЕДИА ---
                    if len(rewritten) > 1024:
                        # 1. Сначала отправляем сам альбом (без текста)
                        await self.client.send_file(DEST, valid_paths)

                        # 2. Ждем секунду и отправляем текст отдельным сообщением
                        if rewritten:
                            await asyncio.sleep(1.0)
                            # Если текст огромный (>4096), режем его, иначе ошибка
                            for chunk in split_text(rewritten):
                                await self.client.send_message(DEST, chunk)
                                await asyncio.sleep(0.5)
                    else:
                        # Если текст короткий, шлем в подписи (это тоже "сначала фото")
                        await self.client.send_file(DEST, valid_paths, caption=rewritten)

                elif rewritten:
                    # --- ВАРИАНТ БЕЗ МЕДИА (только текст) ---
                    for chunk in split_text(rewritten):
                        await self.client.send_message(DEST, chunk)
                        await asyncio.sleep(0.5)

                self.last_post_time = time.time()
                logger.info(f"✅ Альбом {gid} отправлен")

            except Exception as e:
                logger.error(f"❌ Ошибка отправки альбома {gid}: {e}")
            finally:
                for p in valid_paths:
                    Path(p).unlink(missing_ok=True)

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
            path = await dl_task if msg.media else None
            rewritten = ai.rewrite_text(text) if text else ""

            async with self.post_lock:
                await self._wait_smart_delay()
                try:
                    if path:
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
        logger.info("Запуск...")
        await self.client.start(phone=PHONE)
        await self.setup()
        await self.join_sources()

        self.client.add_event_handler(self.process_message, events.NewMessage())

        print("Бот работает...")
        await self.client.run_until_disconnected()
