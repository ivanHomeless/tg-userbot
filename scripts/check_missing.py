#!/usr/bin/env python3
"""Проверка пропущенных сообщений в альбоме"""
import asyncio
import sys
import os

# Добавляем корень проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telethon import TelegramClient
from app.config import API_ID, API_HASH

async def main():
    # Создаем временного клиента (используем другое имя сессии)
    client = TelegramClient('check_session', API_ID, API_HASH)
    await client.start()

    print("Проверка сообщений 20320-20335 из канала -1001942206626:")
    print("=" * 80)

    messages = []
    albums = {}

    async for msg in client.iter_messages(-1001942206626, limit=30):
        if msg.id >= 20320 and msg.id <= 20335:
            grouped_id = msg.grouped_id
            has_media = "✓" if msg.media else "✗"
            text = msg.message[:30] + "..." if msg.message and len(msg.message) > 30 else (msg.message or "")

            messages.append({
                'id': msg.id,
                'grouped_id': grouped_id,
                'media': has_media,
                'text': text
            })

            if grouped_id:
                if grouped_id not in albums:
                    albums[grouped_id] = []
                albums[grouped_id].append(msg.id)

    # Сортируем по ID
    messages.sort(key=lambda m: m['id'])

    for m in messages:
        gid = str(m['grouped_id']) if m['grouped_id'] else "None"
        print(f"msg_id={m['id']:5d} | grouped_id={gid:20s} | media={m['media']} | text={m['text']}")

    if albums:
        print("\n" + "=" * 80)
        print("Найденные альбомы:")
        for gid, ids in sorted(albums.items()):
            print(f"  grouped_id={gid}: {len(ids)} фото - {sorted(ids)}")

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
