-- SQLite. Один writer (планировщик), простая схема для старта.
-- Postgres рассматриваем позже, когда появится реальный объём и нужен pgvector под дедуп.

CREATE TABLE IF NOT EXISTS listings (
    id              TEXT PRIMARY KEY,      -- наш внутренний id, например "cl_8f3a1e"
    source          TEXT NOT NULL,         -- olx | avtoelon | telegram
    source_id       TEXT NOT NULL,         -- id/URL-часть у источника, для апдейта по месту
    source_url      TEXT NOT NULL,
    category        TEXT NOT NULL DEFAULT 'cars',

    title           TEXT,
    price_usd       REAL,
    price_uzs       REAL,
    currency_raw    TEXT,                  -- как было в объявлении, до конвертации

    city            TEXT,

    brand           TEXT,
    model           TEXT,
    year            INTEGER,
    mileage_km      INTEGER,
    transmission    TEXT,                  -- automatic | manual
    position        INTEGER,               -- комплектация, если применимо
    customs_cleared INTEGER,               -- 0/1/NULL

    description_raw TEXT,
    photo_urls      TEXT,                  -- JSON-массив строк

    phone_hash      TEXT,                  -- sha256 телефона, для дедупа; сырой телефон не храним
    duplicate_of    TEXT REFERENCES listings(id),  -- NULL = каноническое объявление; иначе id самого раннего дубля (тот же phone_hash+brand+year+price+mileage, репост в другой канал)

    deal_score          REAL,              -- % ниже медианы сегмента; положительное = дешевле рынка, отрицательное = дороже
    segment_median_usd  REAL,              -- медиана price_usd по сегменту (brand+year) на момент последнего расчёта
    segment_sample_size INTEGER,           -- сколько объявлений вошло в медиану -- мало объявлений = не доверять deal_score

    posted_at       TEXT,                  -- ISO8601, дата публикации по данным источника
    first_seen_at   TEXT NOT NULL,         -- когда мы впервые увидели
    last_seen_at    TEXT NOT NULL,         -- когда видели последний раз (жива ли ещё)
    removed_at      TEXT,                  -- когда пропало из источника, NULL = ещё висит

    UNIQUE (source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_listings_duplicate_of
    ON listings (duplicate_of);

CREATE INDEX IF NOT EXISTS idx_listings_segment
    ON listings (category, brand, model, year);

CREATE INDEX IF NOT EXISTS idx_listings_active
    ON listings (removed_at, category);

CREATE INDEX IF NOT EXISTS idx_listings_phone_hash
    ON listings (phone_hash);

-- Снимок цены при каждом проходе парсера — история для графика и "цену снизили".
CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id  TEXT NOT NULL REFERENCES listings(id),
    price_usd   REAL NOT NULL,
    observed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_price_history_listing
    ON price_history (listing_id, observed_at);
