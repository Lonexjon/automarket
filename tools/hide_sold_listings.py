"""
Разовый (безопасно перезапускаемый) вывод из ленты уже вставленных
объявлений с флагом sold_mentioned -- владелец решил, что такие
объявления (структурный пост, к которому позже дописали "продано") не
нужно показывать покупателю в живой ленте, только держать в базе (цена
всё ещё участвует в истории/медиане сегмента).

regex_extract.py теперь сам ставит removed_at при вставке новых
объявлений с этим флагом (см. main()), но это не трогает уже накопленные
на проде строки -- для них нужен этот отдельный backfill, тот же паттерн,
что remove_rental_listings.py/reprocess_prices.py.

Мягкое скрытие через removed_at (API уже фильтрует WHERE removed_at IS
NULL), а не DELETE -- обратимо, ничего не теряется физически.

Использование:
  python3 tools/hide_sold_listings.py
"""
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parsers"))
import regex_extract as rx  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")


def main():
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        """SELECT id, description_raw FROM listings
           WHERE description_raw IS NOT NULL AND removed_at IS NULL"""
    ).fetchall()

    now = datetime.now(timezone.utc).isoformat()
    hidden = 0
    for listing_id, text in rows:
        flags = rx.detect_flags(text)
        if any(f["code"] == "sold_mentioned" for f in flags):
            con.execute("UPDATE listings SET removed_at = ? WHERE id = ?", (now, listing_id))
            hidden += 1

    con.commit()
    con.close()
    print(f"Проверено: {len(rows)}")
    print(f"Помечено как sold_mentioned, скрыто с сайта: {hidden}")


if __name__ == "__main__":
    main()
