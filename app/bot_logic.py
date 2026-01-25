import time
import asyncio
import logging

from pathlib import Path

from telethon.errors import FloodWaitError
from telethon import TelegramClient, events
from telethon.tl.types import MessageMediaWebPage
from telethon.errors import UserAlreadyParticipantError
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest


from app.config import API_ID, API_HASH, PHONE, SOURCES_LINKS, SOURCES_IDS, DEST, TEMP_DIR, POST_DELAY, SESSION_NAME
from app.database import db_init, is_seen, mark_seen
from app.utils import split_text, save_source_id, remove_link_from_file
from app import ai

logger = logging.getLogger(__name__)

ALBUM_SILENCE_TIMEOUT = 10.0



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
        """Отправка медиа и текста без скачивания с фильтрацией WebPage"""
        try:
            # 1. Фильтруем медиа: оставляем только реальные файлы
            valid_media = []
            for m in (media_messages or []):
                if m and getattr(m, "media", None):
                    # Пропускаем превью ссылок
                    if isinstance(m.media, MessageMediaWebPage):
                        logger.info("🔗 Найдено превью ссылки (WebPage) — игнорируем")
                        continue

                    # ВАЖНО: Добавляем только m.media.
                    # Это гарантирует, что ваша подпись (caption) применится корректно,
                    # так как Telethon будет формировать новое сообщение, а не пытаться
                    # "умно" переслать старое.
                    valid_media.append(m.media)

            if valid_media:
                # 2. Отправляем медиа
                # Проверяем длину текста для подписи
                caption_to_send = text if text and len(text) <= 1024 else None

                # Логируем для отладки, что мы вообще пытаемся отправить
                logger.info(
                    f"📤 Отправка медиа: {len(valid_media)} объектов. Текст подписи: {len(caption_to_send) if caption_to_send else 0} симв.")

                result = await self.client.send_file(
                    DEST,
                    valid_media,
                    caption=caption_to_send,
                    force_document=False  # Чтобы фото оставались фото, а не файлами
                )

                # Проверка ответа сервера
                sent_msgs = result if isinstance(result, list) else [result]
                # Проверяем наличие медиа в ответе
                is_success = any(msg and getattr(msg, "media", None) for msg in sent_msgs)

                if is_success:
                    logger.info(f"✅ Медиа доставлено.")
                    # 3. Если текст длинный — шлем его после успешного медиа
                    if text and len(text) > 1024:
                        await asyncio.sleep(0.5)
                        chunks = list(split_text(text))
                        for chunk in chunks:
                            await self.client.send_message(DEST, chunk)
                            await asyncio.sleep(0.3)
                else:
                    logger.error("❌ Сервер вернул успешный ответ, но в сообщениях нет медиа")

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

            # Если в этом куске альбома есть медиа (и это не ссылка) — сохраняем
            if msg.media and not isinstance(msg.media, MessageMediaWebPage):
                self.albums[gid]['messages'].append(msg)

            # Если в этом куске есть текст — сохраняем в список
            if text:
                self.albums[gid]['texts'].append(text)
                logger.info(f"📥 Добавлен текст к альбому {gid} (всего фрагментов: {len(self.albums[gid]['texts'])})")

            # Перезапуск таймера (Debounce)
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