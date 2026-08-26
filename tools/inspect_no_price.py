"""
Разовая диагностика: показывает source/description_raw для объявлений без
цены -- нужно понять, откуда они взялись (LLM-путь до перехода на regex,
или баг с price_usd=0.0 схлопывающимся в NULL через "or None").

Использование:
  python3 tools/inspect_no_price.py
"""
import os
import sqlite3

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")

con = sqlite3.connect(DB_PATH)
rows = con.execute(
    """SELECT id, source_id, first_seen_at, description_raw FROM listings
       WHERE source = 'telegram' AND price_usd IS NULL AND price_uzs IS NULL"""
).fetchall()

for listing_id, source_id, first_seen_at, text in rows:
    print(f"--- {listing_id} ({source_id}, {first_seen_at}) ---")
    print((text or "")[:300])
    print()

con.close()
