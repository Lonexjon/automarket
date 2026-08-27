"""
Разовый ремонт: находит объявления, смерженные СТАРОЙ версией dedupe.py
(до того, как model попал в ключ дедупа) с канонической записью ДРУГОЙ
модели -- и расцепляет их (duplicate_of = NULL), чтобы новый прогон
dedupe.py пересчитал группы правильно, с учётом model.

Использование:
  python3 tools/fix_cross_model_merges.py
  python3 tools/dedupe.py   # пересчитать дубли уже правильно
"""
import os
import sqlite3

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")


def main():
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        """SELECT l.id, l.model, t.model FROM listings l
           JOIN listings t ON t.id = l.duplicate_of
           WHERE l.duplicate_of IS NOT NULL
             AND l.model IS NOT t.model"""
    ).fetchall()

    for listing_id, own_model, canonical_model in rows:
        con.execute("UPDATE listings SET duplicate_of = NULL WHERE id = ?", (listing_id,))

    con.commit()
    con.close()
    print(f"Расцеплено ошибочных кросс-модельных дублей: {len(rows)}")
    if rows:
        print("Примеры:", rows[:5])


if __name__ == "__main__":
    main()
