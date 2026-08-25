"""
Проставляет listings.duplicate_of для репостов одного и того же объявления
в разные Telegram-каналы.

Дублем считаем СТРОГО: одинаковый phone_hash + brand + year + price_usd +
mileage_km. Если у продавца просто ещё одна машина с теми же ценой/пробегом
случайно -- цена по факту используется для сортировки/deal_score, так что
ложный дубль на неё не влияет; если нужна другая машина того же продавца,
она с большой вероятностью отличается хоть чем-то из этих полей.

Канонической считаем самую раннюю по posted_at запись в группе -- остальным
проставляем duplicate_of = id канонической. Идемпотентно: повторный запуск
ничего не ломает, трогает только записи с ещё пустым duplicate_of.

Использование:
  python3 tools/dedupe.py
"""
import os
import sqlite3

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")
SCHEMA_PATH = os.path.join(ROOT, "db", "schema.sql")


def ensure_schema(con: sqlite3.Connection) -> None:
    with open(SCHEMA_PATH) as f:
        con.executescript(f.read())
    con.commit()
    try:
        con.execute("ALTER TABLE listings ADD COLUMN duplicate_of TEXT REFERENCES listings(id)")
        con.commit()
    except sqlite3.OperationalError:
        pass  # колонка уже есть


def main():
    con = sqlite3.connect(DB_PATH)
    ensure_schema(con)

    groups = con.execute(
        """SELECT phone_hash, brand, year, price_usd, mileage_km, COUNT(*) c
           FROM listings
           WHERE length(phone_hash) > 0 AND brand IS NOT NULL AND year IS NOT NULL
           GROUP BY phone_hash, brand, year, price_usd, mileage_km
           HAVING c > 1"""
    ).fetchall()

    marked = 0
    for phone_hash, brand, year, price_usd, mileage_km, _ in groups:
        rows = con.execute(
            """SELECT id, posted_at, duplicate_of FROM listings
               WHERE phone_hash = ? AND brand = ? AND year = ?
                 AND price_usd IS ? AND mileage_km IS ?
               ORDER BY posted_at ASC""",
            (phone_hash, brand, year, price_usd, mileage_km),
        ).fetchall()

        canonical_id = rows[0][0]
        for listing_id, _, dup_of in rows[1:]:
            if dup_of == canonical_id:
                continue  # уже проставлено на прошлом запуске
            con.execute(
                "UPDATE listings SET duplicate_of = ? WHERE id = ?",
                (canonical_id, listing_id),
            )
            marked += 1

    con.commit()
    con.close()
    print(f"Групп с дублями: {len(groups)}")
    print(f"Помечено как дубль (duplicate_of проставлен): {marked}")


if __name__ == "__main__":
    main()
