"""
API поверх automarket.db, по контракту openapi.yaml.

deal_score в базе хранится в процентах (61.0 = на 61% дешевле рынка) --
контракт openapi.yaml описывает его как долю (0.61), конвертация происходит
здесь, на границе API, чтобы сама база оставалась в человекочитаемых процентах.

Запуск:
  uvicorn api.main:app --host 0.0.0.0 --port 8000
"""
import json
import os
import sqlite3
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")
WEB_DIR = os.path.join(ROOT, "web")

app = FastAPI(title="Automarket API", version="0.1.0")

SORT_COLUMNS = {
    "deal_score_desc": "deal_score DESC NULLS LAST",
    "price_asc": "price_usd ASC NULLS LAST",
    "price_desc": "price_usd DESC NULLS LAST",
    "posted_at_desc": "posted_at DESC NULLS LAST",
}


def get_con() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def to_summary(row: sqlite3.Row) -> dict:
    flags = json.loads(row["flags"]) if row["flags"] else []
    # photo_urls хранит прямые ссылки на CDN источника (Telegram/Avtoelon/OLX),
    # мы их не скачиваем и не хостим -- см. tools/fetch_telegram_photos.py.
    photo_urls = json.loads(row["photo_urls"]) if row["photo_urls"] else []
    return {
        "id": row["id"],
        "source": row["source"],
        "source_url": row["source_url"],
        "category": row["category"],
        "title": row["title"],
        "photo_url": photo_urls[0] if photo_urls else None,
        "price_usd": row["price_usd"],
        "price_uzs": row["price_uzs"],
        "deal_score": row["deal_score"] / 100 if row["deal_score"] is not None else None,
        "segment_median_usd": row["segment_median_usd"],
        "segment_sample_size": row["segment_sample_size"],
        "city": row["city"],
        "posted_at": row["posted_at"],
        "flags": flags,
    }


def to_detail(row: sqlite3.Row) -> dict:
    summary = to_summary(row)
    summary["attrs"] = {
        "brand": row["brand"],
        "model": row["model"],
        "year": row["year"],
        "mileage_km": row["mileage_km"],
        "transmission": row["transmission"],
        "position": row["position"],
        "customs_cleared": bool(row["customs_cleared"]) if row["customs_cleared"] is not None else None,
    }
    summary["price_history"] = []  # price_history пока не заполняется -- см. TODO.md
    summary["photos"] = json.loads(row["photo_urls"]) if row["photo_urls"] else []
    summary["description_raw"] = row["description_raw"]
    return summary


@app.get("/v1/listings")
def list_listings(
    category: str = "cars",
    brand: str | None = None,
    model: str | None = None,
    city: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    deal_min: float | None = Query(None, description="0.2 = минимум на 20% дешевле рынка"),
    sort: Literal["deal_score_desc", "price_asc", "price_desc", "posted_at_desc"] = "deal_score_desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    where = ["duplicate_of IS NULL", "removed_at IS NULL", "category = ?"]
    params: list = [category]

    if brand:
        where.append("brand = ?")
        params.append(brand.lower())
    if model:
        where.append("model = ?")
        params.append(model.lower())
    if city:
        where.append("city = ?")
        params.append(city)
    if price_min is not None:
        where.append("price_usd >= ?")
        params.append(price_min)
    if price_max is not None:
        where.append("price_usd <= ?")
        params.append(price_max)
    if year_min is not None:
        where.append("year >= ?")
        params.append(year_min)
    if year_max is not None:
        where.append("year <= ?")
        params.append(year_max)
    if deal_min is not None:
        where.append("deal_score >= ?")
        params.append(deal_min * 100)

    where_sql = " AND ".join(where)
    order_sql = SORT_COLUMNS[sort]

    con = get_con()
    total = con.execute(f"SELECT COUNT(*) FROM listings WHERE {where_sql}", params).fetchone()[0]
    rows = con.execute(
        f"""SELECT * FROM listings WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?""",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    con.close()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [to_summary(r) for r in rows],
    }


@app.get("/v1/facets")
def facets(category: str = "cars"):
    """Список различающихся brand/city среди активных объявлений -- для фильтров на фронте."""
    con = get_con()
    brands = con.execute(
        """SELECT DISTINCT brand FROM listings
           WHERE duplicate_of IS NULL AND removed_at IS NULL AND category = ? AND brand IS NOT NULL
           ORDER BY brand""",
        (category,),
    ).fetchall()
    cities = con.execute(
        """SELECT DISTINCT city FROM listings
           WHERE duplicate_of IS NULL AND removed_at IS NULL AND category = ? AND city IS NOT NULL
           ORDER BY city""",
        (category,),
    ).fetchall()
    con.close()
    return {"brands": [r["brand"] for r in brands], "cities": [r["city"] for r in cities]}


@app.get("/v1/listings/{listing_id}")
def get_listing(listing_id: str):
    con = get_con()
    row = con.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    con.close()

    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    return to_detail(row)


# смонтировано последним -- маршруты /v1/... объявлены выше и матчатся раньше
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
