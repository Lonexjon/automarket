"""
Разовый ремонт: удаляет из listings записи с source='telegram' и без цены
вообще -- их создал NBSP-баг в regex_extract.py (см. коммит с фиксом).
Сырой текст остаётся в telegram_raw нетронутым, так что после удаления
следующий запуск regex_extract.py разберёт их заново уже с исправленным
regex и, если в тексте правда есть цена, найдёт её.

Использование:
  python3 tools/reprocess_no_price.py
  python3 parsers/regex_extract.py   # пересобрать удалённые
"""
import os
import sqlite3

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")


def main():
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT id FROM listings WHERE source = 'telegram' AND price_usd IS NULL AND price_uzs IS NULL"
    ).fetchall()
    ids = [r[0] for r in rows]

    if ids:
        con.executemany("DELETE FROM listings WHERE id = ?", [(i,) for i in ids])
        con.commit()

    con.close()
    print(f"Удалено (пойдут на пересборку): {len(ids)} -- {ids}")


if __name__ == "__main__":
    main()
