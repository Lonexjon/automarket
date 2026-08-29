"""
Regression-тесты на parsers/money.py -- каждый кейс воспроизводит реальную
ошибку, найденную на production-базе (или явно требуемый сценарий из
спецификации), а не выдуманный синтетический пример.

Использование:
  python3 -m unittest tools/test_money.py
  python3 tools/test_money.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parsers"))
import money  # noqa: E402


class RealProductionLeaks(unittest.TestCase):
    """Каждый кейс здесь -- реальный текст (или его минимальная форма) из
    production-базы automarket.db, который раньше давал неверную цену."""

    def test_boshiga_oyga_no_narxi_line_never_becomes_price(self):
        # реальный пост без единой строки "Narxi:" -- раньше PRICE_USD_RE
        # хватал первое число (2000, первый взнос) как полную цену.
        r = money.resolve_price("Boshiga 2,000$ — 13 oyga 500$ boshqa yana bor")
        self.assertIsNone(r.price_usd)
        self.assertEqual(r.price_type, "installment")
        self.assertTrue(r.needs_review)

    def test_installment_price_mentioned_flag_pattern_still_a_down_payment(self):
        r = money.resolve_price("Boshiga 2,500$ 13 oy 300$")
        self.assertIsNone(r.price_usd)
        self.assertIn(r.price_type, ("down_payment", "installment"))
        self.assertTrue(r.needs_review)

    def test_dollar_sign_before_number(self):
        # xolis_mashina_bozor писал "$22.700 kami bor" -- $ ПЕРЕД числом,
        # старый PRICE_USD_RE такое не ловил вообще.
        r = money.resolve_price("Full pozitsiyada. Narxi: $22.700 kami bor")
        self.assertEqual(r.price_usd, 22700.0)
        self.assertEqual(r.price_type, "full_price")

    def test_uzs_million_word_no_currency_suffix(self):
        # "Narxi: 950ming oxiri" -- ни одной цифры суммы, только слово-
        # множитель, старый regex требовал 5-15 цифр перед сум/so'm.
        r = money.resolve_price("Narxi: 950ming oxiri")
        self.assertEqual(r.price_uzs, 950_000.0)
        self.assertEqual(r.price_type, "full_price")

    def test_uzs_million_word_with_currency(self):
        r = money.resolve_price("Narxi: 315 mln so'm (kelishiladi)")
        self.assertEqual(r.price_uzs, 315_000_000.0)

    def test_sold_addendum_after_real_labeled_price_keeps_labeled_price(self):
        # "Narxi: 15,600$ | Tel: #Sotildi 15300$" -- реальный шаблон, где
        # админ дописывает цену продажи в поле телефона. Помеченная Narxi:
        # цена остаётся ценой ОБЪЯВЛЕНИЯ; sold-тег -- отдельная забота
        # regex_extract.py (флаг sold_mentioned), не money.py.
        r = money.resolve_price("Narxi: 15,600$ | Tel: #Sotildi 15300$")
        self.assertEqual(r.price_usd, 15600.0)

    def test_two_unlabeled_usd_amounts_is_ambiguous(self):
        # "12,000$ ками бор ... 16,500$ га кридитини ечиб берамиз" --
        # два РАЗНЫХ доллара без явной метки "Narxi:" на каждый -- не
        # угадываем, какой из них цена.
        r = money.resolve_price(
            "12,000$ ками бор 10 ой кридити бор 5,700,000 сумдан "
            "| 16,500$ га кридитини ечиб берамиз"
        )
        self.assertIsNone(r.price_usd)
        self.assertEqual(r.price_type, "unknown")
        self.assertTrue(r.needs_review)


class SpecRequiredCases(unittest.TestCase):
    """Кейсы, явно перечисленные в задании как обязательные к покрытию."""

    def test_15000_with_5000_down_payment(self):
        r = money.resolve_price("Машина за $15000, первоначальный взнос $5000")
        self.assertEqual(r.price_usd, 15000.0)
        self.assertEqual(r.price_type, "full_price")

    def test_monthly_payment_alone(self):
        r = money.resolve_price("Ежемесячный платёж 500$, 24 месяца")
        self.assertIsNone(r.price_usd)
        self.assertEqual(r.price_type, "monthly_payment")
        self.assertTrue(r.needs_review)

    def test_installment_negation_leaves_real_price_intact(self):
        for text in [
            "Не рассрочка, не насия, цена 8500$ окончательная",
            "bo'lib to'lash va nasiya yo'q, faqat naqdga, narxi 8500$",
            "банк йўқ, насия йўқ, нархи 8500$",
        ]:
            with self.subTest(text=text):
                r = money.resolve_price(text)
                self.assertEqual(r.price_usd, 8500.0)
                self.assertEqual(r.price_type, "full_price")

    def test_multiple_prices_in_one_message(self):
        r = money.resolve_price("Narxi: 8000$ yoki 90.000.000 so'm")
        self.assertEqual(r.price_usd, 8000.0)
        self.assertEqual(r.price_uzs, 90_000_000.0)
        self.assertEqual(r.price_type, "full_price")

    def test_exchange_with_addition(self):
        r = money.resolve_price(
            "BYD сотилади, Нархи: 21.500$, Onexга обмен бор (доплата билан)"
        )
        self.assertEqual(r.price_usd, 21500.0)
        self.assertEqual(r.price_type, "full_price")

    def test_no_full_price_available(self):
        r = money.resolve_price("Cobalt, mexanika, avtomat emas")
        self.assertIsNone(r.price_usd)
        self.assertIsNone(r.price_uzs)
        self.assertEqual(r.price_type, "unknown")

    def test_cyrillic_latin_and_mixed_text(self):
        r = money.resolve_price(
            "🚘Moshina modeli: #Nexia 3\n👣 Пробег: 97000 km\n"
            "💲Narxi: 8,700$\n☎️Tel: +998901234567"
        )
        self.assertEqual(r.price_usd, 8700.0)
        self.assertEqual(r.price_type, "full_price")


class NormalizationCases(unittest.TestCase):
    def test_apostrophe_variants_all_normalize(self):
        variants = ["so'm", "so‘m", "so’m", "soʻm", "soʼm", "so`m"]
        for v in variants:
            with self.subTest(variant=v):
                r = money.resolve_price(f"Narxi: 30.000.000 {v}")
                self.assertEqual(r.price_uzs, 30_000_000.0)

    def test_thousands_separator_variants(self):
        for text, expected in [
            ("Narxi: 8,700$", 8700.0),
            ("Narxi: 8.700$", 8700.0),
            ("Narxi: 8 700$", 8700.0),
            ("Narxi 8'700$", 8700.0),
        ]:
            with self.subTest(text=text):
                r = money.resolve_price(text)
                self.assertEqual(r.price_usd, expected)

    def test_negotiable_with_no_number(self):
        r = money.resolve_price("Нархи: келишамиз")
        self.assertIsNone(r.price_usd)
        self.assertEqual(r.price_type, "negotiable")
        self.assertFalse(r.needs_review)


if __name__ == "__main__":
    unittest.main()
