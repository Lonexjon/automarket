"""
Проверка целостности базы: дубли, битые ссылки duplicate_of, некорректные
значения. Не чинит ничего сама -- только находит и печатает, что чинить.

Код выхода 0 = всё чисто, 1 = есть находки (удобно дергать из cron/CI).

Использование:
  python3 tools/health_check.py
"""
import json
import os
import re
import sqlite3

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")

MIN_PLAUSIBLE_PRICE_USD = 1000
OUTLIER_RATIO = 0.35

# tag'и-модели, которые НЕ должны встречаться в поле brand после миграции
# (см. tools/migrate_brand_model.py) -- если встречаются, миграция не
# применилась или новый код где-то пишет по-старому.
STALE_MODEL_AS_BRAND = {
    "nexia", "cobalt", "malibu", "gentra", "spark", "damas", "tracker",
    "onix", "captiva", "equinox", "lacetti", "matiz", "orlando",
    "trailblazer", "tahoe", "optra",
}


def check(con: sqlite3.Connection) -> list[str]:
    problems: list[str] = []

    # 1. Дубли (source, source_id) -- не должно быть в принципе (UNIQUE
    # constraint), но проверяем на случай если constraint когда-то снимут.
    dupe_source_ids = con.execute(
        """SELECT source, source_id, COUNT(*) c FROM listings
           GROUP BY source, source_id HAVING c > 1"""
    ).fetchall()
    if dupe_source_ids:
        problems.append(f"Дубли (source, source_id): {len(dupe_source_ids)} штук -- {dupe_source_ids[:5]}")

    # 2. duplicate_of указывает сам на себя
    self_ref = con.execute(
        "SELECT id FROM listings WHERE duplicate_of = id"
    ).fetchall()
    if self_ref:
        problems.append(f"duplicate_of = сам на себя: {len(self_ref)} штук -- {[r[0] for r in self_ref[:5]]}")

    # 3. duplicate_of указывает на несуществующий id
    dangling = con.execute(
        """SELECT l.id, l.duplicate_of FROM listings l
           WHERE l.duplicate_of IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM listings t WHERE t.id = l.duplicate_of)"""
    ).fetchall()
    if dangling:
        problems.append(f"duplicate_of на несуществующий id: {len(dangling)} штук -- {dangling[:5]}")

    # 4. Цепочки дублей (A->B, B->C) -- ожидаем, что duplicate_of всегда
    # указывает прямо на каноническую запись (у которой самой duplicate_of IS NULL).
    chained = con.execute(
        """SELECT l.id, l.duplicate_of FROM listings l
           JOIN listings t ON t.id = l.duplicate_of
           WHERE l.duplicate_of IS NOT NULL AND t.duplicate_of IS NOT NULL"""
    ).fetchall()
    if chained:
        problems.append(f"Цепочки дублей (не указывает на каноническую запись): {len(chained)} штук -- {chained[:5]}")

    # 5. brand хранит на самом деле модель (миграция не применилась/регресс)
    stale = con.execute(
        f"""SELECT id, brand FROM listings
            WHERE lower(brand) IN ({','.join('?' * len(STALE_MODEL_AS_BRAND))})""",
        tuple(STALE_MODEL_AS_BRAND),
    ).fetchall()
    if stale:
        problems.append(f"brand хранит модель (миграция не применилась): {len(stale)} штук -- {stale[:5]}")

    # 6. flags -- невалидный JSON
    flag_rows = con.execute("SELECT id, flags FROM listings WHERE flags IS NOT NULL").fetchall()
    bad_flags = []
    for listing_id, flags_raw in flag_rows:
        try:
            parsed = json.loads(flags_raw)
            if not isinstance(parsed, list) or not all(
                isinstance(f, dict) and {"code", "label", "severity"} <= f.keys() for f in parsed
            ):
                bad_flags.append(listing_id)
        except (json.JSONDecodeError, TypeError):
            bad_flags.append(listing_id)
    if bad_flags:
        problems.append(f"flags -- невалидный JSON/структура: {len(bad_flags)} штук -- {bad_flags[:5]}")

    # 7. deal_score проставлен при цене, которая не должна была его получить
    # (ниже абсолютного или относительного порога -- признак регресса в deal_score.py)
    bad_deal_score = con.execute(
        f"""SELECT id, price_usd, deal_score, segment_median_usd FROM listings
            WHERE deal_score IS NOT NULL
              AND (price_usd IS NULL OR price_usd < {MIN_PLAUSIBLE_PRICE_USD}
                   OR price_usd < segment_median_usd * {OUTLIER_RATIO})"""
    ).fetchall()
    if bad_deal_score:
        problems.append(f"deal_score проставлен при подозрительной цене: {len(bad_deal_score)} штук -- {bad_deal_score[:5]}")

    # 8. Цена не распознана вообще (оба поля пустые) -- try_extract такого
    # не должен пропускать, значит где-то в пути данные потерялись.
    no_price = con.execute(
        "SELECT id FROM listings WHERE source = 'telegram' AND price_usd IS NULL AND price_uzs IS NULL"
    ).fetchall()
    if no_price:
        problems.append(f"Telegram-объявления без цены вообще: {len(no_price)} штук -- {[r[0] for r in no_price[:5]]}")

    # 9. phone_hash не похож на sha256 (64 hex символа)
    bad_hash = con.execute(
        "SELECT id, phone_hash FROM listings WHERE phone_hash IS NOT NULL"
    ).fetchall()
    bad_hash = [(i, h) for i, h in bad_hash if not re.fullmatch(r"[0-9a-f]{64}", h or "")]
    if bad_hash:
        problems.append(f"phone_hash не похож на sha256: {len(bad_hash)} штук -- {bad_hash[:5]}")

    # 10. Год вне разумного диапазона
    bad_year = con.execute(
        "SELECT id, year FROM listings WHERE year IS NOT NULL AND (year < 2000 OR year > 2029)"
    ).fetchall()
    if bad_year:
        problems.append(f"Год вне диапазона 2000-2029: {len(bad_year)} штук -- {bad_year[:5]}")

    return problems


def main():
    con = sqlite3.connect(DB_PATH)
    total = con.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    canonical = con.execute("SELECT COUNT(*) FROM listings WHERE duplicate_of IS NULL").fetchone()[0]
    with_deal_score = con.execute("SELECT COUNT(*) FROM listings WHERE deal_score IS NOT NULL").fetchone()[0]
    with_flags = con.execute("SELECT COUNT(*) FROM listings WHERE flags IS NOT NULL").fetchone()[0]

    print(f"Всего объявлений: {total}")
    print(f"Канонических (не дубль): {canonical}")
    print(f"С deal_score: {with_deal_score}")
    print(f"С флагами: {with_flags}\n")

    problems = check(con)
    con.close()

    if not problems:
        print("Проблем не найдено.")
        return 0

    print(f"НАЙДЕНЫ ПРОБЛЕМЫ ({len(problems)}):")
    for p in problems:
        print(f" - {p}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
