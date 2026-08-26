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
import json
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

# Флаги повреждений/аварий -- только по явным упоминаниям в тексте
# (source="text" в терминах openapi.yaml). Отсутствие совпадения НЕ значит
# "не битая" -- значит просто "не упомянуто", это не подтверждение чистоты.
FLAG_PATTERNS = [
    ("accident_mentioned", "Упоминается авария/ДТП", "warning", re.compile(
        r"avariya|авари|после\s*дтп|\bдтп\b", re.I)),
    ("painted_mentioned", "Упоминается покраска/крашеные элементы", "warning", re.compile(
        r"boyalgan|бўялган|крашен|перекраш", re.I)),
    ("hit_mentioned", "Упоминается удар/повреждение кузова", "warning", re.compile(
        r"urilgan|урилган|\bбит[аоы]\b|битый", re.I)),
    ("needs_repair_mentioned", "Упоминается требуемый ремонт", "warning", re.compile(
        r"ta'mirtalab|ремонт\s*треб|требует\s*ремонта", re.I)),
]

# Отрицание рядом со словом переворачивает смысл ("avariyaga uchramagan" =
# НЕ была в аварии, "не крашена" = НЕ крашена) -- если рядом с совпадением
# есть один из этих маркеров, флаг не ставим вообще (не знаем точно, что
# там было, но точно не positive-утверждение о повреждении).
NEGATION_RE = re.compile(
    r"\bне\s|\bбез\s|uchramagan|bo'?lmagan|bulmagan|emas\b|yo'?q\b|siz\b", re.I
)
NEGATION_WINDOW = 20  # символов до/после совпадения, где ищем отрицание


def detect_flags(text: str) -> list[dict]:
    flags = []
    for code, label, severity, pattern in FLAG_PATTERNS:
        for m in pattern.finditer(text):
            window = text[max(0, m.start() - NEGATION_WINDOW): m.end() + NEGATION_WINDOW]
            if NEGATION_RE.search(window):
                continue  # отрицание рядом -- пропускаем это совпадение
            flags.append({"code": code, "label": label, "severity": severity})
            break  # одного совпадения достаточно, дальше не ищем по этому паттерну
    return flags

# tag -> (brand, model). Большинство тегов в каналах -- это на самом деле
# название МОДЕЛИ (Cobalt, Nexia, Malibu -- всё это модели Chevrolet в
# узбекской линейке), а не марки. Раньше это писалось прямо в поле "brand",
# что путало сегментацию (объявления с #Cobalt не находились по фильтру
# "Chevrolet"). Теперь разносим по обоим полям.
BRAND_MODEL_MAP: dict[str, tuple[str, str | None]] = {
    "chevrolet": ("chevrolet", None),
    "kia": ("kia", None),
    "hyundai": ("hyundai", None),
    "daewoo": ("daewoo", None),
    "nexia": ("chevrolet", "nexia"),
    "cobalt": ("chevrolet", "cobalt"),
    "malibu": ("chevrolet", "malibu"),
    "gentra": ("chevrolet", "gentra"),
    "spark": ("chevrolet", "spark"),
    "damas": ("chevrolet", "damas"),
    "tracker": ("chevrolet", "tracker"),
    "onix": ("chevrolet", "onix"),
    "captiva": ("chevrolet", "captiva"),
    "equinox": ("chevrolet", "equinox"),
    "lacetti": ("chevrolet", "lacetti"),
    "matiz": ("chevrolet", "matiz"),
    "orlando": ("chevrolet", "orlando"),
    "trailblazer": ("chevrolet", "trailblazer"),
    "tahoe": ("chevrolet", "tahoe"),
    "optra": ("chevrolet", "optra"),
}

# Города Узбекистана -- по хэштегу/тексту, для фильтра на сайте. Список не
# исчерпывающий, покрывает области, где сидят каналы из telegram_channels.md.
CITY_MAP: dict[str, str] = {
    "toshkent": "Ташкент", "ташкент": "Ташкент",
    "samarqand": "Самарканд", "самарканд": "Самарканд",
    "andijon": "Андижан", "андижан": "Андижан",
    "fargona": "Фергана", "фаргона": "Фергана", "фергана": "Фергана",
    "namangan": "Наманган", "наманган": "Наманган",
    "buxoro": "Бухара", "бухара": "Бухара",
    "xorazm": "Хорезм", "хоразм": "Хорезм", "urganch": "Ургенч",
    "qarshi": "Карши", "карши": "Карши",
    "termiz": "Термез", "термез": "Термез",
    "navoiy": "Навои", "навои": "Навои",
    "jizzax": "Джизак", "джизак": "Джизак",
    "guliston": "Гулистан", "гулистан": "Гулистан",
    "nukus": "Нукус", "нукус": "Нукус",
    "qoqon": "Коканд", "коканд": "Коканд",
}


def normalize_number(raw: str) -> float:
    digits = re.sub(r"[^\d]", "", raw)
    return float(digits) if digits else 0.0


def guess_brand_model(text: str) -> tuple[str | None, str | None]:
    for tag in HASHTAG_RE.findall(text):
        low = tag.lower()
        if low in BRAND_MODEL_MAP:
            return BRAND_MODEL_MAP[low]
    low_text = text.lower()
    for tag, (brand, model) in BRAND_MODEL_MAP.items():
        if tag in low_text:
            return brand, model
    return None, None


def guess_city(text: str) -> str | None:
    for tag in HASHTAG_RE.findall(text):
        low = tag.lower()
        if low in CITY_MAP:
            return CITY_MAP[low]
    low_text = text.lower()
    for tag, city in CITY_MAP.items():
        if tag in low_text:
            return city
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
    brand, model = guess_brand_model(text)
    city = guess_city(text)

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
        "model": model,
        "city": city,
        "year": int(year_m.group(1)) if year_m else None,
        "mileage_km": int(normalize_number(mileage_m.group(1))) if mileage_m else None,
        "price_usd": normalize_number(price_usd_m.group(1)) if price_usd_m else None,
        "price_uzs": normalize_number(price_uzs_m.group(1)) if price_uzs_m else None,
        "transmission": transmission,
        "phone": phone_m.group(1) if phone_m else None,
    }


def ensure_schema(con: sqlite3.Connection) -> None:
    try:
        con.execute("ALTER TABLE listings ADD COLUMN flags TEXT")
    except sqlite3.OperationalError:
        pass  # уже есть, или таблицы ещё нет (создастся ниже)
    con.commit()

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

        flags = detect_flags(text)

        con.execute(
            """INSERT INTO listings (
                id, source, source_id, source_url, category, title,
                price_usd, price_uzs, city, brand, model, year, mileage_km,
                transmission, description_raw, flags, phone_hash,
                posted_at, first_seen_at, last_seen_at
            ) VALUES (?, 'telegram', ?, ?, 'cars', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, source_id) DO NOTHING""",
            (
                listing_id, source_id, source_url,
                f"{data.get('brand') or ''} {data.get('model') or ''} {data.get('year') or ''}".strip() or "Без названия",
                data["price_usd"] or None, data["price_uzs"] or None, data["city"],
                data["brand"], data["model"], data["year"], data["mileage_km"],
                data["transmission"], text, json.dumps(flags, ensure_ascii=False) if flags else None,
                phone_hash(data["phone"]), posted_at, now, now,
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
