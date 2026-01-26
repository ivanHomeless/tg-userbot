"""
Массовое добавление источников по ссылкам и username

Поддерживаемые форматы:
- @username
- t.me/username
- https://t.me/username
- https://t.me/+XXX (приватные)
- https://t.me/joinchat/XXX (приватные)

Использование:
  python -m scripts.add_sources_from_links data/sources_links.txt
  python -m scripts.add_sources_from_links @channel1 @channel2
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from telethon import TelegramClient
from telethon.errors import FloodWaitError, UserAlreadyParticipantError, ChannelPrivateError, InviteHashExpiredError
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from app.config import API_ID, API_HASH, SESSION_NAME
from app.database.engine import SessionLocal
from app.models.source import Source
from sqlalchemy import select
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


async def add_source_by_link(client: TelegramClient, link: str):
    """
    Добавляет источник по ссылке/username

    Поддерживает:
    - @username
    - t.me/username
    - https://t.me/username
    - https://t.me/+XXX
    - https://t.me/joinchat/XXX
    """
    try:
        entity = None
        original_link = link

        # Нормализация ссылки
        link = link.strip()

        # Убираем @ в начале
        if link.startswith('@'):
            link = link[1:]

        # СЛУЧАЙ 1: Приватный канал (invite link)
        if '+' in link or 'joinchat' in link:
            # Извлекаем invite hash
            if '/+' in link:
                invite_hash = link.split('/+')[-1].split('?')[0]
            elif 'joinchat/' in link:
                invite_hash = link.split('joinchat/')[-1].split('?')[0]
            else:
                invite_hash = link.replace('+', '')

            try:
                # Вступаем через invite hash
                updates = await client(ImportChatInviteRequest(invite_hash))
                if updates.chats:
                    entity = updates.chats[0]
                    logger.info(f"✅ Вступил в приватный канал: {entity.title}")

            except UserAlreadyParticipantError:
                # Уже участник — получаем entity по invite hash
                entity = await client.get_entity(original_link)
                logger.info(f"ℹ️ Уже участник: {entity.title}")

            except InviteHashExpiredError:
                logger.error(f"❌ Ссылка истекла: {original_link}")
                return None

        # СЛУЧАЙ 2: Публичный канал (username)
        else:
            # Убираем протокол и домен, оставляем только username
            if 'https://' in link or 'http://' in link:
                link = link.replace('https://', '').replace('http://', '')
            if 't.me/' in link:
                link = link.split('t.me/')[-1].split('?')[0]

            # Получаем entity
            entity = await client.get_entity(link)

            # Если это канал — вступаем
            if hasattr(entity, 'broadcast') or hasattr(entity, 'megagroup'):
                try:
                    await client(JoinChannelRequest(entity))
                    logger.info(f"✅ Подписался на канал: {entity.title}")
                except UserAlreadyParticipantError:
                    logger.info(f"ℹ️ Уже подписан: {entity.title}")
            else:
                logger.info(f"✅ Получен entity: {entity.title if hasattr(entity, 'title') else 'N/A'}")

        # Сохраняем в БД
        if entity:
            info = {
                'chat_id': entity.id,
                'title': getattr(entity, 'title', None),
                'username': getattr(entity, 'username', None),
                'access_hash': getattr(entity, 'access_hash', None),
            }

            # Формируем join_link
            if info['username']:
                join_link = f"https://t.me/{info['username']}"
            else:
                join_link = f"private:{info['access_hash']}"

            async with SessionLocal() as session:
                # Проверяем дубликат
                stmt = select(Source).where(Source.chat_id == info['chat_id'])
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    logger.info(f"⏭️ {info['chat_id']} уже в БД: {existing.title}")
                    return False

                # Добавляем новый
                new_source = Source(
                    chat_id=info['chat_id'],
                    username=info['username'],
                    title=info['title'],
                    join_link=join_link,
                    is_active=True
                )
                session.add(new_source)
                await session.commit()

                logger.info(f"✅ Добавлен в БД: {info['chat_id']} - {info['title']}")
                return True

        return None

    except ChannelPrivateError:
        logger.error(f"❌ Канал приватный или доступ запрещен: {original_link}")
        return None

    except FloodWaitError as e:
        logger.warning(f"⏳ FloodWait {e.seconds} секунд для {original_link}")
        await asyncio.sleep(e.seconds)
        return None

    except ValueError as e:
        logger.error(f"❌ Не удалось найти: {original_link} ({e})")
        return None

    except Exception as e:
        logger.error(f"❌ Ошибка при добавлении {original_link}: {e}")
        return None


async def add_from_file(filepath: str):
    """Читает ссылки/username из файла и добавляет в БД"""
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start()

    path = Path(filepath)
    if not path.exists():
        logger.error(f"❌ Файл не найден: {filepath}")
        return

    # Читаем ссылки
    links = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            # Игнорируем пустые строки и комментарии
            if line and not line.startswith('#'):
                links.append(line)

    logger.info(f"📋 Найдено {len(links)} ссылок в файле")
    logger.info("=" * 80)

    added = 0
    skipped = 0
    errors = 0

    for link in links:
        result = await add_source_by_link(client, link)
        if result is True:
            added += 1
        elif result is False:
            skipped += 1
        else:
            errors += 1

        await asyncio.sleep(2)  # Защита от FloodWait

    logger.info("=" * 80)
    logger.info(f"📊 Итого:")
    logger.info(f"   ✅ Добавлено: {added}")
    logger.info(f"   ⏭️ Пропущено (дубликаты): {skipped}")
    logger.info(f"   ❌ Ошибок: {errors}")

    await client.disconnect()


async def add_from_args(links: list[str]):
    """Добавляет источники из аргументов командной строки"""
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start()

    logger.info(f"📋 Добавление {len(links)} источников")
    logger.info("=" * 80)

    added = 0
    skipped = 0
    errors = 0

    for link in links:
        result = await add_source_by_link(client, link)
        if result is True:
            added += 1
        elif result is False:
            skipped += 1
        else:
            errors += 1

        await asyncio.sleep(2)

    logger.info("=" * 80)
    logger.info(f"📊 Итого:")
    logger.info(f"   ✅ Добавлено: {added}")
    logger.info(f"   ⏭️ Пропущено (дубликаты): {skipped}")
    logger.info(f"   ❌ Ошибок: {errors}")

    await client.disconnect()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Использование:")
        print("  python -m scripts.add_sources_from_links <filepath>      # Из файла")
        print("  python -m scripts.add_sources_from_links @ch1 @ch2       # Из аргументов")
        print()
        print("Примеры:")
        print("  python -m scripts.add_sources_from_links data/sources_links.txt")
        print("  python -m scripts.add_sources_from_links @channel_name")
        print("  python -m scripts.add_sources_from_links t.me/channel_name")
        print("  python -m scripts.add_sources_from_links https://t.me/+XXX")
        sys.exit(1)

    # Если первый аргумент — файл
    if Path(sys.argv[1]).exists():
        asyncio.run(add_from_file(sys.argv[1]))
    else:
        # Иначе — список ссылок
        asyncio.run(add_from_args(sys.argv[1:]))
