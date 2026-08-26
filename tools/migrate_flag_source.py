"""
Разовый бэкфилл: добавляет "source": "text" в уже сохранённые flags, у
которых его ещё нет (детекция флагов до этого коммита не проставляла source,
хотя openapi.yaml его требует -- фронт различает text/photo_heuristic визуально).
Все существующие флаги пришли из текстового regex-детекта, так что source
везде "text", без потери информации.

Использование:
  python3 tools/migrate_flag_source.py
"""
import json
import os
import sqlite3

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")


def main():
    con = sqlite3.connect(DB_PATH)
    rows = con.execute("SELECT id, flags FROM listings WHERE flags IS NOT NULL").fetchall()

    updated = 0
    for listing_id, flags_raw in rows:
        flags = json.loads(flags_raw)
        changed = False
        for f in flags:
            if "source" not in f:
                f["source"] = "text"
                changed = True
        if changed:
            con.execute(
                "UPDATE listings SET flags = ? WHERE id = ?",
                (json.dumps(flags, ensure_ascii=False), listing_id),
            )
            updated += 1

    con.commit()
    con.close()
    print(f"Обновлено объявлений: {updated}")


if __name__ == "__main__":
    main()
