"""
Разовая проверка здравого смысла: топ-10 объявлений по deal_score среди
сегментов с достаточной выборкой (>=3), чтобы отсечь случайные выбросы
из-за regex-мусора в цене/годе.

Использование:
  python3 tools/check_top_deals.py
"""
import os
import sqlite3

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")

con = sqlite3.connect(DB_PATH)
rows = con.execute(
    """SELECT brand, year, price_usd, mileage_km, deal_score, segment_median_usd,
              segment_sample_size, source_url
       FROM listings
       WHERE duplicate_of IS NULL AND deal_score IS NOT NULL
         AND segment_sample_size >= 3
       ORDER BY deal_score DESC LIMIT 10"""
).fetchall()

for brand, year, price, mileage, score, median, sample, url in rows:
    print(f"{brand} {year} -- ${price} (медиана ${median}, n={sample}) -- "
          f"deal_score {score}% -- {mileage} km -- {url}")

con.close()
