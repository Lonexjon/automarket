"""
Разбирает сырые посты из telegram_raw через Claude Haiku в структурированные
объявления и складывает в таблицу listings (см. db/schema.sql).

Посты без цены пропускаются -- по ним нельзя посчитать deal_score, а зря
жечь токены на разбор бессмысленно.

Использование:
  python parsers/llm_extract.py            # разобрать всё новое
  python parsers/llm_extract.py 50         # ограничить 50 постами (тест)
"""
import hashlib
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")
MODEL = "claude-haiku-4-5-20251001"

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

EXTRACT_TOOL = {
    "name": "extract_listing",
    "description": "Извлекает структурированные данные объявления о продаже авто из текста поста в Telegram-канале.",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_car_listing": {
                "type": "boolean",
                "description": "true, если это объявление о продаже конкретного автомобиля с ценой. false для рекламы, новостей, вопросов, аренды, объявлений без цены.",
            },
            "brand": {"type": "string", "description": "марка, латиницей в нижнем регистре, напр. chevrolet"},
            "model": {"type": "string", "description": "модель, латиницей в нижнем регистре, напр. cobalt"},
            "year": {"type": "integer"},
            "mileage_km": {"type": "integer"},
            "price_usd": {"type": "number", "description": "цена в долларах, если указана в $ или y.e./у.е."},
            "price_uzs": {"type": "number", "description": "цена в сумах, если указана в сумах, а не в $"},
            "city": {"type": "string", "description": "город/область, как в тексте, напр. Ташкент"},
            "transmission": {"type": "string", "enum": ["automatic", "manual"]},
            "position": {"type": "integer", "description": "номер комплектации/позиции, если указан"},
            "customs_cleared": {"type": "boolean"},
            "phone": {"type": "string", "description": "телефон продавца как в тексте, если есть"},
            "flags": {
                "type": "array",
                "description": "короткие пометки из текста: авария/ДТП, требует ремонта, юридические проблемы и т.п.",
                "items": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "snake_case код, напр. accident_mentioned"},
                        "label": {"type": "string", "description": "короткая подпись по-русски"},
                        "severity": {"type": "string", "enum": ["info", "warning", "negative"]},
                    },
                    "required": ["code", "label", "severity"],
                },
            },
        },
        "required": ["is_car_listing"],
    },
}

SYSTEM_PROMPT = (
    "Ты разбираешь объявления о продаже авто из Telegram-каналов Узбекистана. "
    "Текст может быть на узбекском (латиница или кириллица) и русском вперемешку "
    "в одном посте -- это нормально, разбирай оба языка одинаково внимательно. "
    "Извлекай только то, что реально написано в тексте, не додумывай. "
    "Если это не объявление о продаже конкретной машины с ценой -- ставь "
    "is_car_listing=false и больше ничего не заполняй."
)


def phone_hash(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) < 7:
        return None
    return hashlib.sha256(digits.encode()).hexdigest()


def extract_one(text: str) -> dict | None:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "extract_listing"},
        messages=[{"role": "user", "content": text}],
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    return None


def fetch_unprocessed(con: sqlite3.Connection, limit: int | None):
    query = """
        SELECT r.channel, r.message_id, r.posted_at, r.text
        FROM telegram_raw r
        LEFT JOIN listings l ON l.source = 'telegram' AND l.source_id = (r.channel || ':' || r.message_id)
        WHERE l.id IS NULL AND r.text IS NOT NULL AND r.text != ''
        ORDER BY r.posted_at DESC
    """
    if limit:
        query += f" LIMIT {int(limit)}"
    return con.execute(query).fetchall()


def main(limit: int | None):
    con = sqlite3.connect(DB_PATH)
    rows = fetch_unprocessed(con, limit)
    print(f"К разбору: {len(rows)} постов\n")

    saved, skipped, failed = 0, 0, 0
    now = datetime.now(timezone.utc).isoformat()

    for channel, message_id, posted_at, text in rows:
        try:
            data = extract_one(text)
        except Exception as e:
            print(f"FAIL {channel}:{message_id} -- ошибка API: {e}")
            failed += 1
            time.sleep(2)
            continue

        if not data or not data.get("is_car_listing"):
            skipped += 1
            continue

        price_usd = data.get("price_usd")
        price_uzs = data.get("price_uzs")
        if not price_usd and not price_uzs:
            skipped += 1  # без цены не считаем deal_score, объявление бесполезно
            continue

        listing_id = f"tg_{uuid.uuid4().hex[:10]}"
        source_id = f"{channel}:{message_id}"
        source_url = f"https://t.me/{channel}/{message_id}"

        con.execute(
            """INSERT INTO listings (
                id, source, source_id, source_url, category, title,
                price_usd, price_uzs, city, brand, model, year, mileage_km,
                transmission, position, customs_cleared, description_raw,
                phone_hash, posted_at, first_seen_at, last_seen_at
            ) VALUES (?, 'telegram', ?, ?, 'cars', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, source_id) DO NOTHING""",
            (
                listing_id, source_id, source_url,
                f"{data.get('brand', '')} {data.get('model', '')}".strip() or "Без названия",
                price_usd, price_uzs, data.get("city"),
                data.get("brand"), data.get("model"), data.get("year"), data.get("mileage_km"),
                data.get("transmission"), data.get("position"), data.get("customs_cleared"),
                text, phone_hash(data.get("phone")), posted_at, now, now,
            ),
        )
        con.commit()
        saved += 1
        print(f"OK   {channel}:{message_id} -> {data.get('brand')} {data.get('model')} {data.get('year')} "
              f"${price_usd or ''} {price_uzs or ''} сум")

    con.close()
    print(f"\nИтого: сохранено {saved}, пропущено (не объявление/без цены) {skipped}, ошибок {failed}")


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    main(limit)
