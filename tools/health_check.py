"""
Проверка целостности базы: дубли, битые ссылки duplicate_of, некорректные
значения, нарушения правил ценообразования. Не чинит ничего сама -- только
находит и печатает, что чинить.

Находки делятся на critical (реально ломает корректность данных -- деньги,
дедуп, deal_score) и warning (стоит посмотреть, но не искажает то, что уже
показано пользователю). Код выхода 0 = critical-находок нет, 1 = есть хотя
бы одна critical (удобно дёргать из cron/CI) -- warning код выхода не меняет.

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

# Типы, при которых price_usd/price_uzs ДОЛЖНЫ быть NULL -- см. money.py.
# Если у записи с одним из этих price_type цена всё-таки проставлена, это
# прямое нарушение главного правила ("первый взнос никогда не становится
# полной ценой") -- critical, не warning.
NON_FULL_PRICE_TYPES = {"down_payment", "monthly_payment", "exchange_addition", "installment"}

# tag'и-модели, которые НЕ должны встречаться в поле brand после миграции
# (см. tools/migrate_brand_model.py) -- если встречаются, миграция не
# применилась или новый код где-то пишет по-старому.
STALE_MODEL_AS_BRAND = {
    "nexia", "cobalt", "malibu", "gentra", "spark", "damas", "tracker",
    "onix", "captiva", "equinox", "lacetti", "matiz", "orlando",
    "trailblazer", "tahoe", "optra",
}


def check(con: sqlite3.Connection) -> list[dict]:
    """Возвращает список {"severity": "critical"|"warning", "message": str}."""
    findings: list[dict] = []

    def crit(msg):
        findings.append({"severity": "critical", "message": msg})

    def warn(msg):
        findings.append({"severity": "warning", "message": msg})

    # 1. Дубли (source, source_id) -- не должно быть в принципе (UNIQUE
    # constraint), но проверяем на случай если constraint когда-то снимут.
    dupe_source_ids = con.execute(
        """SELECT source, source_id, COUNT(*) c FROM listings
           GROUP BY source, source_id HAVING c > 1"""
    ).fetchall()
    if dupe_source_ids:
        crit(f"Дубли (source, source_id): {len(dupe_source_ids)} штук -- {dupe_source_ids[:5]}")

    # 2. duplicate_of указывает сам на себя
    self_ref = con.execute(
        "SELECT id FROM listings WHERE duplicate_of = id"
    ).fetchall()
    if self_ref:
        crit(f"duplicate_of = сам на себя: {len(self_ref)} штук -- {[r[0] for r in self_ref[:5]]}")

    # 3. duplicate_of указывает на несуществующий id
    dangling = con.execute(
        """SELECT l.id, l.duplicate_of FROM listings l
           WHERE l.duplicate_of IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM listings t WHERE t.id = l.duplicate_of)"""
    ).fetchall()
    if dangling:
        crit(f"duplicate_of на несуществующий id: {len(dangling)} штук -- {dangling[:5]}")

    # 4. Цепочки дублей (A->B, B->C) -- ожидаем, что duplicate_of всегда
    # указывает прямо на каноническую запись (у которой самой duplicate_of IS NULL).
    chained = con.execute(
        """SELECT l.id, l.duplicate_of FROM listings l
           JOIN listings t ON t.id = l.duplicate_of
           WHERE l.duplicate_of IS NOT NULL AND t.duplicate_of IS NOT NULL"""
    ).fetchall()
    if chained:
        crit(f"Цепочки дублей (не указывает на каноническую запись): {len(chained)} штук -- {chained[:5]}")

    # 5. brand хранит на самом деле модель (миграция не применилась/регресс)
    stale = con.execute(
        f"""SELECT id, brand FROM listings
            WHERE lower(brand) IN ({','.join('?' * len(STALE_MODEL_AS_BRAND))})""",
        tuple(STALE_MODEL_AS_BRAND),
    ).fetchall()
    if stale:
        warn(f"brand хранит модель (миграция не применилась): {len(stale)} штук -- {stale[:5]}")

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
        crit(f"flags -- невалидный JSON/структура: {len(bad_flags)} штук -- {bad_flags[:5]}")

    # 7. deal_score проставлен при цене, которая не должна была его получить.
    # Раньше эта проверка знала только про абсолютный/относительный порог
    # цены -- теперь дополнительно проверяет price_type/needs_review: это и
    # есть исходный баг сессии (down_payment получал deal_score, если
    # забыть про has_installment_flag где-то в коде).
    bad_deal_score = con.execute(
        f"""SELECT id, price_usd, price_type, needs_review, deal_score, segment_median_usd
            FROM listings
            WHERE deal_score IS NOT NULL
              AND (
                    price_usd IS NULL
                    OR price_usd < {MIN_PLAUSIBLE_PRICE_USD}
                    OR price_usd < segment_median_usd * {OUTLIER_RATIO}
                    OR price_type IS NOT 'full_price'
                    OR needs_review = 1
              )"""
    ).fetchall()
    if bad_deal_score:
        crit(f"deal_score проставлен при недостоверной цене: {len(bad_deal_score)} штук -- {bad_deal_score[:5]}")

    # 8. Прямое нарушение главного правила: у down_payment/monthly_payment/
    # exchange_addition/installment ЕСТЬ цифра в price_usd или price_uzs --
    # этого не должно происходить никогда, независимо от deal_score.
    money_leak = con.execute(
        f"""SELECT id, price_type, price_usd, price_uzs FROM listings
            WHERE price_type IN ({','.join('?' * len(NON_FULL_PRICE_TYPES))})
              AND (price_usd IS NOT NULL OR price_uzs IS NOT NULL)""",
        tuple(NON_FULL_PRICE_TYPES),
    ).fetchall()
    if money_leak:
        crit(f"Первый взнос/платёж/доплата попали в price_usd/uzs: {len(money_leak)} штук -- {money_leak[:5]}")

    # 9. phone_hash не похож на sha256 (64 hex символа)
    bad_hash = con.execute(
        "SELECT id, phone_hash FROM listings WHERE phone_hash IS NOT NULL"
    ).fetchall()
    bad_hash = [(i, h) for i, h in bad_hash if not re.fullmatch(r"[0-9a-f]{64}", h or "")]
    if bad_hash:
        crit(f"phone_hash не похож на sha256: {len(bad_hash)} штук -- {bad_hash[:5]}")

    # 10. Год вне разумного диапазона.
    bad_year = con.execute(
        "SELECT id, year FROM listings WHERE year IS NOT NULL AND (year < 1970 OR year > 2029)"
    ).fetchall()
    if bad_year:
        warn(f"Год вне диапазона 1970-2029: {len(bad_year)} штук -- {bad_year[:5]}")

    # 11. Telegram-объявления без price_type вообще (NULL) -- значит строка
    # вставлена ДО этой миграции и ещё не прогнана через
    # tools/reprocess_prices.py. Не критично для работы сайта прямо сейчас
    # (deal_score.py такие строки просто не берёт в расчёт), но предупреждает,
    # что нужен реprocessing, иначе они навсегда останутся без deal_score.
    unmigrated = con.execute(
        "SELECT COUNT(*) FROM listings WHERE source = 'telegram' AND price_type IS NULL"
    ).fetchone()[0]
    if unmigrated:
        warn(f"Объявлений без price_type (нужен tools/reprocess_prices.py): {unmigrated} штук")

    # 12. sold_mentioned объявления без removed_at -- информационно: они
    # физически ещё показываются как активные (никто их не скрывает
    # автоматически), но уже отмечены проданными. Не critical -- фронтенд
    # решает, показывать ли их (по флагу), это не искажение данных самих по себе.
    sold_active = con.execute(
        "SELECT COUNT(*) FROM listings WHERE flags LIKE '%sold_mentioned%' AND removed_at IS NULL AND duplicate_of IS NULL"
    ).fetchone()[0]
    if sold_active:
        warn(f"Объявлений с флагом sold_mentioned, всё ещё активных: {sold_active} штук")

    return findings


def main():
    con = sqlite3.connect(DB_PATH)
    total = con.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    canonical = con.execute("SELECT COUNT(*) FROM listings WHERE duplicate_of IS NULL").fetchone()[0]
    with_deal_score = con.execute("SELECT COUNT(*) FROM listings WHERE deal_score IS NOT NULL").fetchone()[0]
    with_flags = con.execute("SELECT COUNT(*) FROM listings WHERE flags IS NOT NULL").fetchone()[0]
    with_full_price = con.execute("SELECT COUNT(*) FROM listings WHERE price_type = 'full_price'").fetchone()[0]
    needs_review = con.execute("SELECT COUNT(*) FROM listings WHERE needs_review = 1").fetchone()[0]
    price_type_counts = con.execute(
        "SELECT price_type, COUNT(*) FROM listings WHERE price_type IS NOT NULL GROUP BY price_type ORDER BY 2 DESC"
    ).fetchall()

    print(f"Всего объявлений: {total}")
    print(f"Канонических (не дубль): {canonical}")
    print(f"С deal_score: {with_deal_score}")
    print(f"С флагами: {with_flags}")
    print(f"С уверенной полной ценой (price_type=full_price): {with_full_price}")
    print(f"needs_review: {needs_review}")
    if price_type_counts:
        print("Разбивка по price_type:", ", ".join(f"{t}={c}" for t, c in price_type_counts))
    print()

    findings = check(con)
    con.close()

    criticals = [f for f in findings if f["severity"] == "critical"]
    warnings = [f for f in findings if f["severity"] == "warning"]

    if not findings:
        print("Проблем не найдено.")
        return 0

    if criticals:
        print(f"CRITICAL ({len(criticals)}):")
        for f in criticals:
            print(f" - {f['message']}")
    if warnings:
        print(f"WARNING ({len(warnings)}):")
        for f in warnings:
            print(f" - {f['message']}")

    return 1 if criticals else 0


if __name__ == "__main__":
    raise SystemExit(main())
