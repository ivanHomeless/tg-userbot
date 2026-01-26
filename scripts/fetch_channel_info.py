"""
Скрипт для получения информации о каналах по их ID
Использует существующую сессию userbot

Использование:
  python -m scripts.fetch_channel_info --list          # Показать все диалоги
  python -m scripts.fetch_channel_info --all           # Обновить метаданные из БД
  python -m scripts.fetch_channel_info -100123 -100456 # Проверить конкретные ID
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat, User
from app.config import API_ID, API_HASH, SESSION_NAME
from app.database.engine import SessionLocal
from app.models.source import Source
from sqlalchemy import select
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


async def get_channel_info(client: TelegramClient, chat_id: int):
    """
    Получает полную информацию о канале/группе по ID

    Возвращает dict с полями:
    - chat_id: int
    - title: str
    - username: str | None
    - is_private: bool
    - participants_count: int | None
    - type: 'channel' | 'megagroup' | 'chat' | 'user'
    - access_hash: int (для приватных)
    """
    try:
        entity = await client.get_entity(chat_id)

        info = {
            'chat_id': entity.id,
            'title': getattr(entity, 'title', None),
            'username': getattr(entity, 'username', None),
            'access_hash': getattr(entity, 'access_hash', None),
            'is_private': not hasattr(entity, 'username') or entity.username is None,
            'participants_count': getattr(entity, 'participants_count', None),
        }

        # Определяем тип
        if isinstance(entity, Channel):
            if entity.broadcast:
                info['type'] = 'channel'
            else:
                info['type'] = 'megagroup'
        elif isinstance(entity, Chat):
            info['type'] = 'chat'
        elif isinstance(entity, User):
            info['type'] = 'user'
        else:
            info['type'] = 'unknown'

        return info

    except ValueError as e:
        logger.error(f"❌ Не удалось получить entity для {chat_id}: {e}")
        logger.warning(f"💡 Возможно, userbot не является участником этого канала")
        return None

    except Exception as e:
        logger.error(f"❌ Ошибка при получении информации о {chat_id}: {e}")
        return None


async def fetch_all_from_db():
    """Получает метаданные для всех источников из БД"""
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start()

    async with SessionLocal() as session:
        stmt = select(Source)
        result = await session.execute(stmt)
        sources = result.scalars().all()

        logger.info(f"📋 Найдено {len(sources)} источников в БД")
        logger.info("=" * 80)

        for source in sources:
            info = await get_channel_info(client, source.chat_id)

            if info:
                source.title = info['title']
                source.username = info['username']

                logger.info(f"✅ {source.chat_id}")
                logger.info(f"   Название: {info['title']}")
                logger.info(f"   Username: @{info['username']}" if info['username'] else "   Username: (приватный)")
                logger.info(f"   Тип: {info['type']}")
                logger.info(f"   Приватный: {'Да' if info['is_private'] else 'Нет'}")
                logger.info(f"   Участников: {info['participants_count'] or 'N/A'}")
                logger.info(f"   Access Hash: {info['access_hash']}")
                logger.info(f"   Активен: {'Да' if source.is_active else 'Нет'}")

                if info['is_private'] and not source.join_link:
                    source.join_link = f"private:{info['access_hash']}"

            else:
                logger.warning(f"⚠️ {source.chat_id} - не удалось получить данные")

            logger.info("-" * 80)

        await session.commit()
        logger.info("💾 Изменения сохранены в БД")

    await client.disconnect()


async def fetch_specific_ids(chat_ids: list[int]):
    """Получает информацию о конкретных каналах (не сохраняет в БД)"""
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start()

    logger.info(f"📋 Получение информации о {len(chat_ids)} каналах")
    logger.info("=" * 80)

    results = []

    for chat_id in chat_ids:
        info = await get_channel_info(client, chat_id)

        if info:
            results.append(info)

            logger.info(f"✅ {chat_id}")
            logger.info(f"   Название: {info['title']}")
            logger.info(f"   Username: @{info['username']}" if info['username'] else "   Username: (приватный)")
            logger.info(f"   Тип: {info['type']}")
            logger.info(f"   Приватный: {'Да' if info['is_private'] else 'Нет'}")
            logger.info(f"   Участников: {info['participants_count'] or 'N/A'}")

            if info['is_private']:
                join_link = f"private:{info['access_hash']}"
            else:
                join_link = f"https://t.me/{info['username']}" if info['username'] else None

            logger.info("")
            logger.info("   📝 SQL для добавления в БД:")
            logger.info(f"   INSERT INTO source (chat_id, username, title, join_link, is_active)")
            logger.info(f"   VALUES ({chat_id}, {repr(info['username'])}, {repr(info['title'])}, {repr(join_link)}, true);")
        else:
            logger.warning(f"⚠️ {chat_id} - не удалось получить данные")

        logger.info("-" * 80)

    await client.disconnect()

    return results


async def list_all_dialogs():
    """Показывает ВСЕ каналы/группы, где userbot является участником"""
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start()

    logger.info("📋 Получение списка ВСЕХ диалогов...")
    logger.info("=" * 80)

    dialogs = await client.get_dialogs()

    channels = []
    groups = []
    private_chats = []

    for dialog in dialogs:
        entity = dialog.entity

        if isinstance(entity, Channel):
            if entity.broadcast:
                channels.append({
                    'id': entity.id,
                    'title': entity.title,
                    'username': entity.username,
                    'is_private': not entity.username
                })
            else:
                groups.append({
                    'id': entity.id,
                    'title': entity.title,
                    'username': entity.username,
                    'is_private': not entity.username
                })
        elif isinstance(entity, Chat):
            groups.append({
                'id': entity.id,
                'title': entity.title,
                'username': None,
                'is_private': True
            })
        elif isinstance(entity, User):
            private_chats.append({
                'id': entity.id,
                'title': entity.first_name or 'Unknown',
                'username': entity.username,
                'is_private': False
            })

    logger.info(f"📢 КАНАЛЫ ({len(channels)}):")
    for ch in channels:
        status = "(приватный)" if ch['is_private'] else f"@{ch['username']}"
        logger.info(f"   {ch['id']:15} | {ch['title'][:40]:40} | {status}")

    logger.info("")
    logger.info(f"👥 ГРУППЫ ({len(groups)}):")
    for gr in groups:
        status = "(приватная)" if gr['is_private'] else f"@{gr['username']}"
        logger.info(f"   {gr['id']:15} | {gr['title'][:40]:40} | {status}")

    logger.info("")
    logger.info(f"💬 Личные чаты: {len(private_chats)}")

    await client.disconnect()


def main():
    """Точка входа"""
    import argparse

    parser = argparse.ArgumentParser(description='Получение информации о Telegram каналах')
    parser.add_argument('chat_ids', nargs='*', type=int, help='ID каналов для проверки')
    parser.add_argument('--all', action='store_true', help='Обновить метаданные ВСЕХ источников из БД')
    parser.add_argument('--list', action='store_true', help='Показать ВСЕ диалоги userbot')

    args = parser.parse_args()

    if args.list:
        asyncio.run(list_all_dialogs())
    elif args.all:
        asyncio.run(fetch_all_from_db())
    elif args.chat_ids:
        asyncio.run(fetch_specific_ids(args.chat_ids))
    else:
        print("Использование:")
        print("  python -m scripts.fetch_channel_info --list          # Показать все диалоги")
        print("  python -m scripts.fetch_channel_info --all           # Обновить метаданные из БД")
        print("  python -m scripts.fetch_channel_info -100123 -100456 # Проверить конкретные ID")


if __name__ == '__main__':
    main()
