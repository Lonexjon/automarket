"""
Разовая миграция уже загруженных объявлений под новую схему brand/model.

До этого regex_extract.py писал в поле "brand" то, что на самом деле часто
модель (Cobalt, Nexia, Malibu -- модели Chevrolet, не марки). Раздваиваем
существующие строки по BRAND_MODEL_MAP из regex_extract.py -- значения там
совпадают со старыми "brand" один в один, так что миграция без потерь.

Заодно бэкфиллит city из уже сохранённого description_raw для строк, где
город ещё не извлекался (regex_extract.py раньше его не извлекал вообще).

Использование:
  python3 tools/migrate_brand_model.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parsers"))
import sqlite3  # noqa: E402

from regex_extract import BRAND_MODEL_MAP, guess_city  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")


def main():
    con = sqlite3.connect(DB_PATH)

    rows = con.execute(
        "SELECT id, brand, model FROM listings WHERE brand IS NOT NULL"
    ).fetchall()

    remapped = 0
    for listing_id, old_brand, model in rows:
        mapping = BRAND_MODEL_MAP.get((old_brand or "").lower())
        if not mapping:
            continue  # уже нормальная марка (chevrolet/kia/...) или неизвестный тег -- не трогаем
        new_brand, new_model = mapping
        if new_brand == old_brand and (model or None) == new_model:
            continue  # уже смигрировано (повторный запуск)
        con.execute(
            "UPDATE listings SET brand = ?, model = COALESCE(model, ?) WHERE id = ?",
            (new_brand, new_model, listing_id),
        )
        remapped += 1

    city_rows = con.execute(
        "SELECT id, description_raw FROM listings WHERE city IS NULL AND description_raw IS NOT NULL"
    ).fetchall()
    city_filled = 0
    for listing_id, text in city_rows:
        city = guess_city(text)
        if city:
            con.execute("UPDATE listings SET city = ? WHERE id = ?", (city, listing_id))
            city_filled += 1

    con.commit()
    con.close()
    print(f"Brand/model переразнесено: {remapped}")
    print(f"Город проставлен (бэкфилл): {city_filled}")


if __name__ == "__main__":
    main()
