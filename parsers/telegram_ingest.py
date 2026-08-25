"""
Читает последние сообщения из списка Telegram-каналов (telegram_channels.md)
и складывает их как есть в SQLite -- сырой текст, без разбора полей.
LLM-экстрактор (текст -> {brand, model, year, price, ...}) это отдельный
следующий шаг, тут только сбор и дедуп по (channel, message_id).

Использование:
  python parsers/telegram_ingest.py            # все каналы, по 50 постов
  python parsers/telegram_ingest.py 200        # все каналы, по 200 постов
"""
import asyncio
import os
import sqlite3
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
ROOT = os.path.join(os.path.dirname(__file__), "..")
SESSION_PATH = os.path.join(ROOT, "automarket_session")
DB_PATH = os.path.join(ROOT, "automarket.db")

CHANNELS = [
    "moshina_bozorim",
    "vodiybozor_mashinabozor", "rasmiyavtobozor", "Vodiymashina_Toshkent",
    "xolis_mashina_bozor", "inamarka_mashinalar", "andijon_moshina_elonlarimi",
    "vodiy_bozori_avtoelon", "moshina_elonlarim", "qarshi_mashinalarim",
    "Vodiy_Avto_7", "malibu_impala_mashina", "qoqon_moshina_quqon1",
    "samarkand_mashinalari", "Avtobozor_Avtoelon_Moshinalar", "fargona_moshinas",
    "eng_arzoni_ma", "oniks_mashina", "MoshinaBozorchasi", "moshinabozor",
    "namangan_andijon_fargona1", "surxandaryo_termiz_mashinalari",
    "novoiy_moshina", "horazm_moshina", "Captiva_Malibu_Kaptiva_Bozor",
    "avtoelon",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_raw (
    channel      TEXT NOT NULL,
    message_id   INTEGER NOT NULL,
    posted_at    TEXT,
    text         TEXT,
    has_photo    INTEGER,
    fetched_at   TEXT NOT NULL,
    PRIMARY KEY (channel, message_id)
);
"""


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    con.commit()


async def ingest_channel(client: TelegramClient, con: sqlite3.Connection, channel: str, limit: int) -> int:
    try:
        entity = await client.get_entity(channel)
    except Exception as e:
        print(f"SKIP {channel}: не удалось открыть канал ({e})")
        return 0

    saved = 0
    now = datetime.now(timezone.utc).isoformat()
    async for message in client.iter_messages(entity, limit=limit):
        if not message.text and not message.photo:
            continue  # служебные сообщения (закреп и т.п.)
        con.execute(
            """INSERT INTO telegram_raw (channel, message_id, posted_at, text, has_photo, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(channel, message_id) DO UPDATE SET
                 text=excluded.text, has_photo=excluded.has_photo, fetched_at=excluded.fetched_at""",
            (channel, message.id, message.date.isoformat() if message.date else None,
             message.text, 1 if message.photo else 0, now),
        )
        saved += 1
    con.commit()
    print(f"OK   {channel}: {saved} постов")
    return saved


async def main(limit: int):
    con = sqlite3.connect(DB_PATH)
    ensure_schema(con)

    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    await client.start()

    total = 0
    for channel in CHANNELS:
        total += await ingest_channel(client, con, channel, limit)
        await asyncio.sleep(1.5)  # не долбим API Телеграма быстрее необходимого

    await client.disconnect()
    con.close()
    print(f"\nИтого сохранено/обновлено: {total} постов из {len(CHANNELS)} каналов -> {DB_PATH}")


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    asyncio.run(main(limit))
