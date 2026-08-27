"""
Проставляет listings.duplicate_of для репостов одного и того же объявления
в разные Telegram-каналы, и копит историю цены на канонической записи.

Дублем считаем: одинаковый phone_hash + brand + model + year + mileage_km.
Цену из ключа сознательно убрали -- частая практика: repost той же машины
с "ценой снизили", раньше это ловилось как два разных объявления. Теперь
такое мержится в одно, а разные цены из группы попадают в price_history
канонической записи (график "цена со временем" на карточке).

Компромисс: если у дилера случайно совпадут brand+model+year+mileage_km у
ДВУХ РАЗНЫХ машин (не репост) -- смержится ошибочно. Это менее вероятно,
чем раньше опасались (mileage_km обычно разный до километра у разных
физических машин), и вкупе с добавлением model в ключ (раньше его не было
вообще -- Cobalt и Nexia от одного продавца с одинаковой ценой/пробегом
могли смержиться, это был баг) new-версия сейчас строже, а не мягче.

Канонической считаем самую раннюю по posted_at запись в группе. Идемпотентно:
повторный запуск не дублирует уже проставленные duplicate_of/price_history.

Использование:
  python3 tools/dedupe.py
"""
import os
import sqlite3

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")
SCHEMA_PATH = os.path.join(ROOT, "db", "schema.sql")


def ensure_schema(con: sqlite3.Connection) -> None:
    try:
        con.execute("ALTER TABLE listings ADD COLUMN duplicate_of TEXT REFERENCES listings(id)")
        con.commit()
    except sqlite3.OperationalError:
        pass  # колонки ещё нет (свежая база) или уже есть -- оба случая ок

    with open(SCHEMA_PATH) as f:
        con.executescript(f.read())
    con.commit()


def main():
    con = sqlite3.connect(DB_PATH)
    ensure_schema(con)

    groups = con.execute(
        """SELECT phone_hash, brand, model, year, mileage_km, COUNT(*) c
           FROM listings
           WHERE length(phone_hash) > 0 AND brand IS NOT NULL AND year IS NOT NULL
             AND mileage_km IS NOT NULL
           GROUP BY phone_hash, brand, model, year, mileage_km
           HAVING c > 1"""
    ).fetchall()

    marked, price_points = 0, 0
    for phone_hash, brand, model, year, mileage_km, _ in groups:
        rows = con.execute(
            """SELECT id, posted_at, duplicate_of, price_usd FROM listings
               WHERE phone_hash = ? AND brand = ? AND model IS ? AND year = ? AND mileage_km = ?
               ORDER BY posted_at ASC""",
            (phone_hash, brand, model, year, mileage_km),
        ).fetchall()

        canonical_id, canonical_posted_at, _, canonical_price = rows[0]

        already_has_history = con.execute(
            "SELECT 1 FROM price_history WHERE listing_id = ? LIMIT 1", (canonical_id,)
        ).fetchone()
        if not already_has_history and canonical_price:
            con.execute(
                "INSERT INTO price_history (listing_id, price_usd, observed_at) VALUES (?, ?, ?)",
                (canonical_id, canonical_price, canonical_posted_at),
            )
            price_points += 1

        for listing_id, posted_at, dup_of, price_usd in rows[1:]:
            if dup_of != canonical_id:
                con.execute(
                    "UPDATE listings SET duplicate_of = ? WHERE id = ?",
                    (canonical_id, listing_id),
                )
                marked += 1
                if price_usd:
                    con.execute(
                        "INSERT INTO price_history (listing_id, price_usd, observed_at) VALUES (?, ?, ?)",
                        (canonical_id, price_usd, posted_at),
                    )
                    price_points += 1

    con.commit()
    con.close()
    print(f"Групп с дублями: {len(groups)}")
    print(f"Помечено как дубль (duplicate_of проставлен): {marked}")
    print(f"Точек истории цены добавлено: {price_points}")


if __name__ == "__main__":
    main()
