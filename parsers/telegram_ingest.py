"""
Читает последние сообщения из списка Telegram-каналов (telegram_channels.md)
и складывает их как есть в SQLite -- сырой текст, без разбора полей.
LLM-экстрактор (текст -> {brand, model, year, price, ...}) это отдельный
следующий шаг, тут только сбор и дедуп по (channel, message_id).

Инкрементально: для канала, который уже когда-то собирали, тянем только
посты НОВЕЕ самого большого уже сохранённого message_id (min_id в Telethon) --
не важно, 5 их за день вышло или 200, лишнего не перекачиваем. Для канала,
которого в базе ещё нет, берём стартовый снимок в INITIAL_LIMIT постов.

Использование:
  python parsers/telegram_ingest.py             # инкрементально (обычный режим)
  python parsers/telegram_ingest.py 500         # свой стартовый лимит для новых каналов
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


async def ingest_channel(client: TelegramClient, con: sqlite3.Connection, channel: str, initial_limit: int) -> int:
    try:
        entity = await client.get_entity(channel)
    except Exception as e:
        print(f"SKIP {channel}: не удалось открыть канал ({e})")
        return 0

    last_seen_id = con.execute(
        "SELECT MAX(message_id) FROM telegram_raw WHERE channel = ?", (channel,)
    ).fetchone()[0]

    if last_seen_id:
        # уже собирали этот канал раньше -- только то, что новее
        iter_kwargs = {"min_id": last_seen_id, "limit": None}
    else:
        # новый канал -- стартовый снимок
        iter_kwargs = {"limit": initial_limit}

    saved = 0
    now = datetime.now(timezone.utc).isoformat()
    async for message in client.iter_messages(entity, **iter_kwargs):
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
    mode = "новые" if last_seen_id else "стартовый снимок"
    print(f"OK   {channel}: {saved} постов ({mode})")
    return saved


async def main(initial_limit: int):
    con = sqlite3.connect(DB_PATH)
    ensure_schema(con)

    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    await client.start()

    total = 0
    for channel in CHANNELS:
        total += await ingest_channel(client, con, channel, initial_limit)
        await asyncio.sleep(1.5)  # не долбим API Телеграма быстрее необходимого

    await client.disconnect()
    con.close()
    print(f"\nИтого сохранено/обновлено: {total} постов из {len(CHANNELS)} каналов -> {DB_PATH}")


if __name__ == "__main__":
    initial_limit = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    asyncio.run(main(initial_limit))
