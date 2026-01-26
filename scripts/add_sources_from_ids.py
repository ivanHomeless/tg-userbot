"""
Массовое добавление источников по ID из файла

Файл должен содержать ID (по одному на строку):
-1001234567890
-1009876543210
...

Использование:
  python -m scripts.add_sources_from_ids data/sources_ids.txt
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from telethon import TelegramClient
from app.config import API_ID, API_HASH, SESSION_NAME
from app.database.engine import SessionLocal
from app.models.source import Source
from sqlalchemy import select
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


async def add_source_by_id(client: TelegramClient, chat_id: int):
    """Добавляет источник по ID (если userbot уже участник)"""
    try:
        entity = await client.get_entity(chat_id)

        info = {
            'chat_id': entity.id,
            'title': getattr(entity, 'title', None),
            'username': getattr(entity, 'username', None),
            'access_hash': getattr(entity, 'access_hash', None),
        }

        if info['username']:
            join_link = f"https://t.me/{info['username']}"
        else:
            join_link = f"private:{info['access_hash']}"

        async with SessionLocal() as session:
            stmt = select(Source).where(Source.chat_id == chat_id)
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                logger.info(f"⏭️ {chat_id} уже в БД: {existing.title}")
                return False

            new_source = Source(
                chat_id=info['chat_id'],
                username=info['username'],
                title=info['title'],
                join_link=join_link,
                is_active=True
            )
            session.add(new_source)
            await session.commit()

            logger.info(f"✅ Добавлен: {chat_id} - {info['title']}")
            return True

    except ValueError as e:
        logger.error(f"❌ {chat_id}: userbot не является участником")
        return None
    except Exception as e:
        logger.error(f"❌ {chat_id}: {e}")
        return None


async def add_from_file(filepath: str):
    """Читает ID из файла и добавляет в БД"""
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start()

    path = Path(filepath)
    if not path.exists():
        logger.error(f"❌ Файл не найден: {filepath}")
        return

    chat_ids = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and line.lstrip('-').isdigit():
                chat_ids.append(int(line))

    logger.info(f"📋 Найдено {len(chat_ids)} ID в файле")
    logger.info("=" * 80)

    added = 0
    skipped = 0
    errors = 0

    for chat_id in chat_ids:
        result = await add_source_by_id(client, chat_id)
        if result is True:
            added += 1
        elif result is False:
            skipped += 1
        else:
            errors += 1

        await asyncio.sleep(1)

    logger.info("=" * 80)
    logger.info(f"📊 Итого:")
    logger.info(f"   ✅ Добавлено: {added}")
    logger.info(f"   ⏭️ Пропущено (дубликаты): {skipped}")
    logger.info(f"   ❌ Ошибок: {errors}")

    await client.disconnect()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Использование: python -m scripts.add_sources_from_ids <filepath>")
        print("Пример: python -m scripts.add_sources_from_ids data/sources_ids.txt")
        sys.exit(1)

    asyncio.run(add_from_file(sys.argv[1]))
