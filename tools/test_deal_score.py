"""
Regression-тесты на tools/deal_score.py -- проверяют, что deal_score
никогда не считается по недостоверной цене (первый взнос, ежемесячный
платёж, неоднозначная цена) и не выдаётся при слишком маленьком сегменте.

Использование:
  python3 -m unittest tools/test_deal_score.py
"""
import importlib.util
import os
import sqlite3
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _load_module(name, rel_path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


deal_score = _load_module("deal_score", "tools/deal_score.py")


def make_db(path, rows):
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    deal_score.SCHEMA_PATH = os.path.join(ROOT, "db", "schema.sql")
    deal_score.ensure_schema(con)  # настоящая схема прода, не урезанная копия
    for id_, brand, model, year, price_usd, price_type, needs_review in rows:
        con.execute(
            "INSERT INTO listings (id, source, source_id, source_url, brand, model, year, "
            "price_usd, price_type, needs_review, first_seen_at, last_seen_at) "
            "VALUES (?, 'telegram', ?, ?, ?, ?, ?, ?, ?, ?, '2026-01-01', '2026-01-01')",
            (id_, id_, id_, brand, model, year, price_usd, price_type, needs_review),
        )
    con.commit()
    con.close()


class DealScoreExclusions(unittest.TestCase):
    def setUp(self):
        self.db_path = "/tmp/claude_test_deal_score.db"

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_down_payment_never_gets_deal_score_or_skews_median(self):
        rows = [
            ("a", "chevrolet", "nexia", 2018, 8000.0, "full_price", 0),
            ("b", "chevrolet", "nexia", 2018, 8200.0, "full_price", 0),
            ("c", "chevrolet", "nexia", 2018, 8100.0, "full_price", 0),
            # эта запись -- первый взнос, а не цена. Если бы она попала в
            # медиану/deal_score, сегмент выглядел бы дешевле, чем есть.
            ("d", "chevrolet", "nexia", 2018, 2000.0, "down_payment", 1),
        ]
        make_db(self.db_path, rows)
        deal_score.DB_PATH = self.db_path
        deal_score.SCHEMA_PATH = os.path.join(ROOT, "db", "schema.sql")
        deal_score.main()

        con = sqlite3.connect(self.db_path)
        d = dict(zip(
            ["deal_score", "segment_median_usd", "segment_sample_size"],
            con.execute("SELECT deal_score, segment_median_usd, segment_sample_size FROM listings WHERE id='d'").fetchone(),
        ))
        self.assertIsNone(d["deal_score"])  # down_payment сама никогда не получает deal_score

        median = con.execute("SELECT segment_median_usd FROM listings WHERE id='a'").fetchone()[0]
        self.assertGreater(median, 7000)  # медиана посчитана по 8000/8100/8200, а не по 2000

    def test_needs_review_never_gets_deal_score(self):
        rows = [
            ("a", "kia", "seltos", 2021, 15000.0, "full_price", 0),
            ("b", "kia", "seltos", 2021, 15500.0, "full_price", 0),
            ("c", "kia", "seltos", 2021, 15200.0, "full_price", 0),
            ("d", "kia", "seltos", 2021, 15300.0, "full_price", 1),  # неоднозначная, needs_review
        ]
        make_db(self.db_path, rows)
        deal_score.DB_PATH = self.db_path
        deal_score.SCHEMA_PATH = os.path.join(ROOT, "db", "schema.sql")
        deal_score.main()

        con = sqlite3.connect(self.db_path)
        score = con.execute("SELECT deal_score FROM listings WHERE id='d'").fetchone()[0]
        self.assertIsNone(score)

    def test_small_segment_gets_no_deal_score_at_all(self):
        # всего 2 объявления в сегменте -- меньше MIN_SEGMENT_SIZE, ни одно
        # не должно получить deal_score, даже несмотря на честную цену.
        rows = [
            ("a", "hyundai", "solaris", 2015, 6000.0, "full_price", 0),
            ("b", "hyundai", "solaris", 2015, 6500.0, "full_price", 0),
        ]
        make_db(self.db_path, rows)
        deal_score.DB_PATH = self.db_path
        deal_score.SCHEMA_PATH = os.path.join(ROOT, "db", "schema.sql")
        deal_score.main()

        con = sqlite3.connect(self.db_path)
        scores = [r[0] for r in con.execute("SELECT deal_score FROM listings").fetchall()]
        self.assertTrue(all(s is None for s in scores))

    def test_stale_deal_score_on_duplicate_or_missing_year_is_reset(self):
        # Реальный прод-баг: строка с duplicate_of заполненным (дубль) или
        # без year, у которой ЕЩЁ ДО этого прогона был проставлен
        # deal_score/медиана (с прошлой схемы, до появления price_type) --
        # раньше main() вообще не выбирал такие строки в rows (WHERE
        # duplicate_of IS NULL AND brand/year IS NOT NULL), поэтому их
        # deal_score никогда не сбрасывался, хотя цена недостоверна
        # (price_type=down_payment). health_check().check() ловил это как
        # CRITICAL. main() теперь обязан явно обнулить обе строки.
        rows = [
            ("a", "chevrolet", "cobalt", 2019, 8000.0, "full_price", 0),
            ("b", "chevrolet", "cobalt", 2019, 8100.0, "full_price", 0),
            ("c", "chevrolet", "cobalt", 2019, 8200.0, "full_price", 0),
        ]
        make_db(self.db_path, rows)
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO listings (id, source, source_id, source_url, brand, model, year, "
            "price_usd, price_type, needs_review, duplicate_of, deal_score, segment_median_usd, "
            "segment_sample_size, first_seen_at, last_seen_at) "
            "VALUES ('dup', 'telegram', 'dup', 'dup', 'chevrolet', NULL, 2019, "
            "NULL, 'down_payment', 1, 'a', 0.0, 7000.0, 1, '2026-01-01', '2026-01-01')"
        )
        con.execute(
            "INSERT INTO listings (id, source, source_id, source_url, brand, model, year, "
            "price_usd, price_type, needs_review, deal_score, segment_median_usd, "
            "segment_sample_size, first_seen_at, last_seen_at) "
            "VALUES ('noyear', 'telegram', 'noyear', 'noyear', 'chevrolet', NULL, NULL, "
            "NULL, 'down_payment', 1, 0.0, 7000.0, 1, '2026-01-01', '2026-01-01')"
        )
        con.commit()
        con.close()

        deal_score.DB_PATH = self.db_path
        deal_score.SCHEMA_PATH = os.path.join(ROOT, "db", "schema.sql")
        deal_score.main()

        con = sqlite3.connect(self.db_path)
        for listing_id in ("dup", "noyear"):
            score = con.execute("SELECT deal_score FROM listings WHERE id=?", (listing_id,)).fetchone()[0]
            self.assertIsNone(score, f"{listing_id} should have had its stale deal_score reset to NULL")

    def test_honest_full_price_segment_gets_real_deal_score(self):
        rows = [
            ("a", "daewoo", "matiz", 2010, 3000.0, "full_price", 0),
            ("b", "daewoo", "matiz", 2010, 3200.0, "full_price", 0),
            ("c", "daewoo", "matiz", 2010, 3100.0, "full_price", 0),
            ("d", "daewoo", "matiz", 2010, 2000.0, "full_price", 0),  # реально дешёвая
        ]
        make_db(self.db_path, rows)
        deal_score.DB_PATH = self.db_path
        deal_score.SCHEMA_PATH = os.path.join(ROOT, "db", "schema.sql")
        deal_score.main()

        con = sqlite3.connect(self.db_path)
        score_d = con.execute("SELECT deal_score FROM listings WHERE id='d'").fetchone()[0]
        self.assertIsNotNone(score_d)
        self.assertGreater(score_d, 0)  # дешевле рынка -> положительный deal_score


if __name__ == "__main__":
    unittest.main()
