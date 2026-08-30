"""
Разовый (безопасно перезапускаемый) вывод из ленты уже вставленных
объявлений об АРЕНДЕ, а не продаже -- see regex_extract.is_rental_listing().

try_extract() теперь пропускает такие посты при первой вставке (не
вставляет вовсе), но это не трогает уже накопленные на проде строки --
для них нужен этот отдельный backfill, тот же паттерн, что и
reprocess_prices.py.

Мягкое удаление через removed_at (как и остальной пайплайн -- API уже
фильтрует WHERE removed_at IS NULL), а не DELETE: обратимо, ничего не
теряется физически, только перестаёт показываться на сайте.

Использование:
  python3 tools/remove_rental_listings.py
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
    removed = 0
    for listing_id, text in rows:
        if rx.is_rental_listing(text):
            con.execute("UPDATE listings SET removed_at = ? WHERE id = ?", (now, listing_id))
            removed += 1

    con.commit()
    con.close()
    print(f"Проверено: {len(rows)}")
    print(f"Помечено как аренда (не продажа), скрыто с сайта: {removed}")


if __name__ == "__main__":
    main()
