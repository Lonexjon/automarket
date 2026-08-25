"""
Разовый прогон detect_flags() по уже сохранённым объявлениям, у которых
description_raw есть, а flags ещё нет -- нужен после того, как детекция
флагов появилась в regex_extract.py уже после первого прохода.

Использование:
  python3 tools/backfill_flags.py
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parsers"))
from regex_extract import detect_flags, ensure_schema  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")


def main():
    con = sqlite3.connect(DB_PATH)
    ensure_schema(con)
    rows = con.execute(
        """SELECT id, description_raw FROM listings
           WHERE flags IS NULL AND description_raw IS NOT NULL AND description_raw != ''"""
    ).fetchall()

    updated, with_flags = 0, 0
    for listing_id, text in rows:
        flags = detect_flags(text)
        if flags:
            con.execute(
                "UPDATE listings SET flags = ? WHERE id = ?",
                (json.dumps(flags, ensure_ascii=False), listing_id),
            )
            with_flags += 1
        updated += 1

    con.commit()
    con.close()
    print(f"Проверено объявлений: {updated}")
    print(f"Найдены флаги (авария/крашена/удар/ремонт): {with_flags}")


if __name__ == "__main__":
    main()
