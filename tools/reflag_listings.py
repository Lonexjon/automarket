"""
Разовый пересчёт flags для УЖЕ вставленных объявлений -- нужен каждый раз,
когда меняются FLAG_PATTERNS/NEGATION_RE в regex_extract.py, потому что
regex_extract.py сам флаги проставляет только при первой вставке строки,
существующие объявления не трогает.

Использование:
  python3 tools/reflag_listings.py
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parsers"))
from regex_extract import detect_flags  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")


def main():
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT id, description_raw, flags FROM listings WHERE description_raw IS NOT NULL"
    ).fetchall()

    changed = 0
    for listing_id, text, old_flags_raw in rows:
        new_flags = detect_flags(text)
        new_flags_raw = json.dumps(new_flags, ensure_ascii=False) if new_flags else None
        if new_flags_raw != old_flags_raw:
            con.execute("UPDATE listings SET flags = ? WHERE id = ?", (new_flags_raw, listing_id))
            changed += 1
    con.commit()
    con.close()
    print(f"Проверено: {len(rows)}")
    print(f"Флаги изменились: {changed}")


if __name__ == "__main__":
    main()
