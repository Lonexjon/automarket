"""
Дымовой тест: читает последние N постов из одного канала через уже
авторизованную сессию Telethon. Ничего не пишет в БД -- только печатает,
чтобы глазами убедиться, что чтение реально работает и текст приходит
как есть (кириллица/латиница/узбекский вперемешку, эмодзи, переносы).
"""
import asyncio
import os
import sys

from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
SESSION_PATH = os.path.join(os.path.dirname(__file__), "..", "automarket_session")


async def main(channel: str, limit: int):
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    await client.start()

    entity = await client.get_entity(channel)
    print(f"Канал: {entity.title} (id={entity.id})\n")

    async for message in client.iter_messages(entity, limit=limit):
        print(f"--- post {message.id} | {message.date} ---")
        print((message.text or "(без текста / только фото)")[:500])
        print(f"фото: {'да' if message.photo else 'нет'}")
        print()

    await client.disconnect()


if __name__ == "__main__":
    channel = sys.argv[1] if len(sys.argv) > 1 else "avtoelon"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    asyncio.run(main(channel, limit))
