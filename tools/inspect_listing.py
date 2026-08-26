"""
Печатает описание конкретного объявления по id -- для ручной проверки
подозрительных находок health_check.py.

Использование:
  python3 tools/inspect_listing.py tg_58c7bb35eb
"""
import os
import sqlite3
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")

listing_id = sys.argv[1]
con = sqlite3.connect(DB_PATH)
row = con.execute(
    "SELECT brand, model, year, price_usd, description_raw, source_url FROM listings WHERE id = ?",
    (listing_id,),
).fetchone()
con.close()

if not row:
    print("не найдено")
else:
    brand, model, year, price, text, url = row
    print(f"brand={brand} model={model} year={year} price_usd={price}")
    print(f"url={url}")
    print("---")
    print(text)
