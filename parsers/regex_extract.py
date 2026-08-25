"""
Бесплатный разбор объявлений из telegram_raw через regex -- без обращения
к LLM вообще. Покрывает посты с понятным эмодзи-шаблоном полей, который
используют почти все каналы в выборке:

    #Nexia 3
    📅 Йили: 2020   (или "Yili:", "Yil:")
    👣 Пробег: 97000 km   (или "Probeg:")
    💰 Narxi: 8700$   (или просто "8,700$" / "195000000 сум")
    ☎️ Тел: +998...   (или "Tel:")

Посты, которые НЕ разбираются этим парсером (нет цены/года/пробега в
понятном формате), остаются необработанными -- их можно позже прогнать
через llm_extract.py как fallback, когда будет бюджет, или просто
пропустить, если regex уже покрывает достаточно.

Использование:
  python parsers/regex_extract.py            # разобрать всё новое
  python parsers/regex_extract.py 50         # ограничить (тест)
"""
import hashlib
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")
SCHEMA_PATH = os.path.join(ROOT, "db", "schema.sql")

# год: 2000-2029, отдельным словом/после Йили:/Yili:/Yil:
YEAR_RE = re.compile(r"(?:йили|yili|yil|год|г\.в\.?)\s*[:\-]?\s*(20[0-2]\d)", re.I)
YEAR_FALLBACK_RE = re.compile(r"\b(20[0-2]\d)\b")

# пробег: "97000 km" / "97,000 км" / "Probeg: 97.000 km"
MILEAGE_RE = re.compile(
    r"(?:probeg|пробег)\s*[:\-]?\s*([\d][\d,. ]{2,10})\s*(?:km|км)", re.I
)

# цена в $: "8,700$" / "8700 $" / "8 700 у.е." / "narxi: 15000$"
PRICE_USD_RE = re.compile(
    r"([\d][\d,. ]{2,10})\s*(?:\$|у\.?\s?е\.?|y\.?\s?e\.?)", re.I
)
# цена в сумах: "195000000 сум" / "195 000 000 so'm"
PRICE_UZS_RE = re.compile(
    r"([\d][\d,. ]{5,15})\s*(?:сум|so.?m)", re.I
)

# телефон: +998 followed by 9 digits, с разделителями или без
PHONE_RE = re.compile(r"(\+?998[\s\-]?\d{2}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2})")

# марка/модель из первого хэштега: "#Nexia 3" / "#Chevrolet_Cobalt"
HASHTAG_RE = re.compile(r"#([A-Za-zА-Яа-яЎўҚқҒғҲҳ]+)")

TRANSMISSION_AUTO_RE = re.compile(r"\bavtomat\b|\bавтомат\b", re.I)
TRANSMISSION_MANUAL_RE = re.compile(r"\bmexanika\b|\bмеханика\b|\bмех\.?\b", re.I)

KNOWN_BRANDS = {
    "chevrolet", "kia", "hyundai", "daewoo", "nexia", "cobalt", "malibu",
    "gentra", "spark", "damas", "tracker", "onix", "captiva", "equinox",
    "lacetti", "matiz", "orlando", "trailblazer", "tahoe", "optra",
}


def normalize_number(raw: str) -> float:
    digits = re.sub(r"[^\d]", "", raw)
    return float(digits) if digits else 0.0


def guess_brand(text: str) -> str | None:
    for tag in HASHTAG_RE.findall(text):
        low = tag.lower()
        if low in KNOWN_BRANDS:
            return low
    low_text = text.lower()
    for brand in KNOWN_BRANDS:
        if brand in low_text:
            return brand
    return None


def phone_hash(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"[^\d]", "", phone)
    if len(digits) < 7:
        return None
    return hashlib.sha256(digits.encode()).hexdigest()


def try_extract(text: str) -> dict | None:
    """Возвращает словарь полей, если удалось распарсить, иначе None."""
    price_usd_m = PRICE_USD_RE.search(text)
    price_uzs_m = PRICE_UZS_RE.search(text)
    if not price_usd_m and not price_uzs_m:
        return None  # без цены объявление бесполезно, не пытаемся

    year_m = YEAR_RE.search(text) or YEAR_FALLBACK_RE.search(text)
    mileage_m = MILEAGE_RE.search(text)
    phone_m = PHONE_RE.search(text)
    brand = guess_brand(text)

    transmission = None
    if TRANSMISSION_AUTO_RE.search(text):
        transmission = "automatic"
    elif TRANSMISSION_MANUAL_RE.search(text):
        transmission = "manual"

    # без бренда и без года -- слишком неуверенно, отдаём на LLM
    if not brand and not year_m:
        return None

    return {
        "brand": brand,
        "model": None,  # regex не выделяет модель надёжно -- оставляем пусто
        "year": int(year_m.group(1)) if year_m else None,
        "mileage_km": int(normalize_number(mileage_m.group(1))) if mileage_m else None,
        "price_usd": normalize_number(price_usd_m.group(1)) if price_usd_m else None,
        "price_uzs": normalize_number(price_uzs_m.group(1)) if price_uzs_m else None,
        "transmission": transmission,
        "phone": phone_m.group(1) if phone_m else None,
    }


def ensure_schema(con: sqlite3.Connection) -> None:
    with open(SCHEMA_PATH) as f:
        con.executescript(f.read())
    con.commit()


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
    ensure_schema(con)
    rows = fetch_unprocessed(con, limit)
    print(f"К разбору: {len(rows)} постов (бесплатно, без LLM)\n")

    saved, unmatched = 0, 0
    now = datetime.now(timezone.utc).isoformat()

    for channel, message_id, posted_at, text in rows:
        data = try_extract(text)
        if not data:
            unmatched += 1
            continue

        listing_id = f"tg_{uuid.uuid4().hex[:10]}"
        source_id = f"{channel}:{message_id}"
        source_url = f"https://t.me/{channel}/{message_id}"

        con.execute(
            """INSERT INTO listings (
                id, source, source_id, source_url, category, title,
                price_usd, price_uzs, brand, model, year, mileage_km,
                transmission, description_raw, phone_hash,
                posted_at, first_seen_at, last_seen_at
            ) VALUES (?, 'telegram', ?, ?, 'cars', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, source_id) DO NOTHING""",
            (
                listing_id, source_id, source_url,
                f"{data.get('brand') or ''} {data.get('year') or ''}".strip() or "Без названия",
                data["price_usd"] or None, data["price_uzs"] or None,
                data["brand"], data["model"], data["year"], data["mileage_km"],
                data["transmission"], text, phone_hash(data["phone"]),
                posted_at, now, now,
            ),
        )
        con.commit()
        saved += 1

    con.close()
    print(f"Разобрано regex'ом: {saved}")
    print(f"Не подошли под шаблон (кандидаты для LLM позже): {unmatched}")


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    main(limit)
