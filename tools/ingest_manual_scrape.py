"""
Заливает в базу JSON, собранный tools/collect_olx_avtoelon.py (тот же файл
отдаётся владельцу -- см. письмо/чат) на его домашнем IP -- с VPS/песочницы
разработки OLX.uz и Avtoelon.uz недоступны (403/сброс соединения по IP
дата-центра), а с домашнего IP владельца оба сайта открываются нормально,
подтверждено вручную (см. docs/quality-project/README.md).

Это НЕ автоматический регулярный сбор -- владелец решил (сессия 2026-09-01)
делать это вручную, время от времени: запускает сборщик у себя, присылает
получившийся JSON в чат, дальше этот скрипт заливает его в базу тем же
путём, что и Telegram-пайплайн (тот же UNIQUE(source, source_id), то же
ON CONFLICT DO NOTHING -- безопасно перезапускать/заливать один и тот же
файл повторно).

Цена и город здесь -- СТРУКТУРНЫЕ поля с самого сайта (не вытащены из
свободного текста регуляркой), поэтому price_type='full_price' и
needs_review=False сразу, без прогона через parsers/money.py -- в отличие
от Telegram-объявлений, тут нет риска перепутать первый взнос с ценой:
сайт сам показывает это как ЦЕНУ объявления, только договорную или нет.

Использование:
  python3 tools/ingest_manual_scrape.py path/to/olx_avtoelon_YYYYMMDD_HHMM.json
"""
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parsers"))
import regex_extract as rx  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")
SCHEMA_PATH = os.path.join(ROOT, "db", "schema.sql")

RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
RU_DATE_RE = re.compile(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})", re.I)
RELATIVE_TIME_RE = re.compile(r"(сегодня|вчера)\s+в\s+(\d{1,2}):(\d{2})", re.I)


def parse_posted_at(raw: str | None, collected_at: str) -> str | None:
    """"31 августа 2026 г." -> ISO. "сегодня в 13:45" / "вчера в 13:45" --
    большинство свежих OLX-объявлений размечены именно так (не полной датой),
    отсчитываем от даты сбора (collected_at), а не от текущего времени сервера
    -- сбор и заливка могут разойтись по дню. Не гадаем формат дальше этого --
    если не совпало, просто NULL, а не мусорная строка, которую фронт не
    сможет разобрать (new Date() на нераспознанной строке даёт Invalid Date)."""
    if not raw:
        return None
    raw_low = raw.lower()

    rel = RELATIVE_TIME_RE.search(raw_low)
    if rel:
        word, hour, minute = rel.groups()
        base = datetime.fromisoformat(collected_at)
        if word == "вчера":
            from datetime import timedelta
            base -= timedelta(days=1)
        try:
            return base.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0).isoformat()
        except ValueError:
            return None

    m = RU_DATE_RE.search(raw_low)
    if not m:
        return None
    day, month_name, year = m.groups()
    month = RU_MONTHS.get(month_name)
    if not month:
        return None
    try:
        return datetime(int(year), month, int(day), tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def resolve_price_fields(item: dict) -> dict:
    price_usd = item.get("price_usd")
    price_uzs = item.get("price_uzs")
    if price_usd or price_uzs:
        return dict(price_type="full_price", price_confidence="high", needs_review=0, price_reason="structured_source_field")
    if item.get("attrs", {}).get("negotiable") or item.get("attrs", {}).get("negotiable_raw") == "Да":
        return dict(price_type="negotiable", price_confidence="high", needs_review=0, price_reason="negotiable_no_number")
    return dict(price_type="unknown", price_confidence="low", needs_review=1, price_reason="no_price_field")


def resolve_year(item: dict) -> int | None:
    raw = item.get("attrs", {}).get("year_raw")
    if not raw:
        return None
    m = re.search(r"\d{4}", raw)
    return int(m.group(0)) if m else None


def resolve_mileage(item: dict) -> int | None:
    raw = item.get("attrs", {}).get("mileage_raw")
    if not raw:
        return None
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else None


def resolve_transmission(item: dict) -> str | None:
    """Сайты отдают готовую метку ("Автоматическая", "Механика"), а не
    свободный текст -- rx.TRANSMISSION_*_RE рассчитаны на слово "автомат"
    в тексте объявления и не матчат "Автоматическая" (\\b рвётся на
    границе "автомат|ическая"), поэтому здесь просто ищем подстроку."""
    raw = item.get("attrs", {}).get("transmission_raw", "").lower()
    if "автомат" in raw or "avtomat" in raw:
        return "automatic"
    if "механ" in raw or "mexanika" in raw or "mechanika" in raw:
        return "manual"
    return None


def main(json_path: str):
    with open(json_path, encoding="utf-8") as f:
        payload = json.load(f)
    items = payload.get("items", payload if isinstance(payload, list) else [])
    collected_at = payload.get("collected_at") or datetime.now(timezone.utc).isoformat()

    con = sqlite3.connect(DB_PATH)
    rx.ensure_schema(con)

    now = datetime.now(timezone.utc).isoformat()
    inserted, skipped_dup, skipped_no_data = 0, 0, 0

    for item in items:
        source = item.get("source")
        source_id = item.get("source_id")
        if not source or not source_id:
            skipped_no_data += 1
            continue

        title = item.get("title") or ""
        description_raw = item.get("description_raw") or ""
        brand, model = rx.guess_brand_model(f"{title} {description_raw}")
        flags = rx.detect_flags(description_raw) if description_raw else []
        price_fields = resolve_price_fields(item)

        # Наблюдалось на реальной первой партии (2026-09-01): часть страниц
        # Avtoelon отдаёт другую вёрстку карточки (10 из 40 в первой партии --
        # ни title, ни атрибутов, только цена/город/год), парсер их недобирает.
        # Без title карточка на сайте рендерится пустой -- прячем сразу тем же
        # removed_at, каким уже прячутся sold_mentioned (см. regex_extract.py),
        # а не показываем пользователю сломанную плитку.
        removed_at = now if not title else None

        listing_id = f"{source}_{uuid.uuid4().hex[:10]}"
        cur = con.execute(
            """INSERT INTO listings (
                id, source, source_id, source_url, category, title,
                price_usd, price_uzs, currency_raw,
                price_type, price_confidence, needs_review, price_reason,
                city, brand, model, year, mileage_km, transmission,
                description_raw, photo_urls, flags,
                posted_at, first_seen_at, last_seen_at, removed_at
            ) VALUES (?, ?, ?, ?, 'cars', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, source_id) DO NOTHING""",
            (
                listing_id, source, source_id, item.get("source_url", ""),
                title or None,
                item.get("price_usd"), item.get("price_uzs"), item.get("currency_raw"),
                price_fields["price_type"], price_fields["price_confidence"],
                price_fields["needs_review"], price_fields["price_reason"],
                item.get("city") or None, brand, model,
                resolve_year(item), resolve_mileage(item), resolve_transmission(item),
                description_raw or None,
                json.dumps(item.get("photo_urls") or [], ensure_ascii=False) or None,
                json.dumps(flags, ensure_ascii=False) if flags else None,
                parse_posted_at(item.get("posted_at"), collected_at), now, now, removed_at,
            ),
        )
        if cur.rowcount:
            inserted += 1
        else:
            skipped_dup += 1

    con.commit()
    con.close()

    print(f"Всего в файле: {len(items)}")
    print(f"Вставлено новых: {inserted}")
    print(f"Пропущено (уже были, тот же source+source_id): {skipped_dup}")
    if skipped_no_data:
        print(f"Пропущено (нет source/source_id в записи): {skipped_no_data}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Использование: python3 tools/ingest_manual_scrape.py path/to/file.json")
        sys.exit(1)
    main(sys.argv[1])
