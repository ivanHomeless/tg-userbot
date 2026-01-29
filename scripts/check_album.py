#!/usr/bin/env python3
"""Проверка сообщений альбома напрямую из канала"""
import asyncio
import sys
from telethon import TelegramClient
from app.config import API_ID, API_HASH, SESSION_NAME

async def main():
    if len(sys.argv) < 3:
        print("Usage: python scripts/check_album.py <chat_id> <message_id_start>")
        sys.exit(1)

    chat_id = int(sys.argv[1])
    msg_start = int(sys.argv[2])

    # Используем отдельную сессию для чтения
    client = TelegramClient(f"{SESSION_NAME}_readonly", API_ID, API_HASH)
    await client.start()

    print(f"Проверка сообщений {msg_start}-{msg_start+10} из канала {chat_id}:")
    print("=" * 80)

    messages = await client.get_messages(
        chat_id,
        ids=list(range(msg_start, msg_start + 11))
    )

    grouped_albums = {}

    for msg in messages:
        if msg:
            grouped_id = msg.grouped_id
            has_media = msg.media is not None

            print(f"msg_id={msg.id:5d} | grouped_id={grouped_id or 'None':20s} | media={has_media}")

            if grouped_id:
                if grouped_id not in grouped_albums:
                    grouped_albums[grouped_id] = []
                grouped_albums[grouped_id].append(msg.id)

    if grouped_albums:
        print("\n" + "=" * 80)
        print("Найденные альбомы:")
        for gid, msg_ids in grouped_albums.items():
            print(f"  grouped_id={gid}: {len(msg_ids)} сообщений - {msg_ids}")

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
