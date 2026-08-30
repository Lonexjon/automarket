"""
Regression-тесты на parsers/regex_extract.py (try_extract/detect_flags/
is_pure_sold_confirmation) -- уровень выше money.py: здесь проверяется
весь пайплайн разбора поста целиком, включая отбраковку "проданных"
постов-поздравлений и год вне диапазона 2000-2029.

Использование:
  python3 -m unittest tools/test_regex_extract.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parsers"))
import regex_extract as rx  # noqa: E402


class TryExtractRealCases(unittest.TestCase):
    def test_full_price_ad_matches(self):
        d = rx.try_extract(
            "🚘Moshina modeli: #Nexia 3\n📶Yil: 2020\n🆙Probeg: 97000 km\n"
            "💲Narxi: 8,700$\n📞Tel: +998901234567"
        )
        self.assertIsNotNone(d)
        self.assertEqual(d["brand"], "chevrolet")
        self.assertEqual(d["model"], "nexia")
        self.assertEqual(d["year"], 2020)
        self.assertEqual(d["price_usd"], 8700.0)
        self.assertEqual(d["price_type"], "full_price")
        self.assertFalse(d["needs_review"])

    def test_pure_sold_confirmation_is_dropped_entirely(self):
        # пост-поздравление без единого структурного поля -- это НЕ
        # объявление, try_extract должен вернуть None (не вставлять вообще).
        d = rx.try_extract("2012 yil Nexia2 40 mlnga baraka bo'pti tabriklaymiz")
        self.assertIsNone(d)

    def test_rental_ad_is_dropped_entirely(self):
        # Реальные прод-баги, найдены владельцем в браузере: "Матиз Арендага
        # берилади", "Дамас Арендага берилади", "Нексия 3 Арендага
        # берилади" -- это объявления об АРЕНДЕ, не о продаже. Депозит и
        # посуточная ставка ("Нархи: 300$ залог бор толов олдиндан")
        # раньше становились price_usd, будто это честная цена продажи
        # машины. Сайт про продажу, у аренды нет "цены покупки" как
        # понятия -- try_extract должен пропускать такие посты целиком, а
        # не пытаться выдумать им цену.
        for text in [
            "Дамас Арендага берилади! Йили- 2026 Кунига 100 мингдан "
            "200$ залог бор Тел: +998957172111",
            "Нексия 3 Арендага берилади Йили- 2018/2019 Нархи- 300$ "
            "залок бор толов олдиндан Тел: +998907967575",
        ]:
            with self.subTest(text=text):
                self.assertIsNone(rx.try_extract(text))

    def test_rental_wanted_hashtag_post_is_dropped_entirely(self):
        # Реальные прод-данные (владелец попросил проверить базу шире):
        # "#Arendaga Нексия 3 Автомат оламан❗️" -- это пост "ИЩУ машину в
        # аренду" ("оламан" = "возьму"), канал сам маркирует его хэштегом
        # #Arendaga. Не объявление о продаже вообще (и не "сдаётся", как в
        # test_rental_ad_is_dropped_entirely -- обратный смысл, но та же
        # категория: нет цены покупки, не место в маркетплейсе продажи).
        for text in [
            "#Arendaga Нексия 3 Автомат оламан❗️ Аёл киши минади, "
            "Узокрок муддатга мингани, Нархини келишамиз",
            "#Arendaga Лабо оламан❗️ Тел: +998882711144",
        ]:
            with self.subTest(text=text):
                self.assertIsNone(rx.try_extract(text))

    def test_normal_sale_ad_not_mistaken_for_rental(self):
        # "не в аренде/такси" в тексте не должно ложно исключать обычное
        # объявление о продаже -- RENTAL_RE требует пару "аренда"+"берилади"
        # ("сдаётся"), не голое слово "аренда".
        d = rx.try_extract(
            "#Cobalt 2019 yil, taksida yoki arendada bo'lmagan, shaxsiy, "
            "Narxi: 9500$ | Tel: +998901234567"
        )
        self.assertIsNotNone(d)
        self.assertEqual(d["price_usd"], 9500.0)

    def test_accessory_price_in_parentheses_never_becomes_car_price(self):
        # Реальный прод-баг (tg_25b655d033, Gentra): "— қиммат полик (70$)"
        # -- цена ковриков в списке комплектации, "16,900 ками бор" (без
        # знака валюты) -- настоящая цена машины. Сайт показывал $70 как
        # честную цену идеальной Gentra 2023 года. Число в скобках без
        # метки "Нархи:" не должно резолвиться как уверенная цена машины.
        d = rx.try_extract(
            "#Gentra 3-позиция Full Tuning қилинган сотилади!\nЙили: 2023\n"
            "— қиммат полик (70$)\n16,900 ками бор\n+998331989999"
        )
        self.assertIsNotNone(d)  # марка+год есть, объявление сохраняется
        self.assertIsNone(d["price_usd"])
        self.assertNotEqual(d["price_type"], "full_price")

    def test_real_ad_with_later_sold_addendum_is_kept_and_flagged(self):
        text = "#Cobalt 2019 yil Narxi: 9500$ | Tel: #Sotildi 9200$"
        d = rx.try_extract(text)
        self.assertIsNotNone(d)
        self.assertEqual(d["price_usd"], 9500.0)
        flags = {f["code"] for f in rx.detect_flags(text)}
        self.assertIn("sold_mentioned", flags)

    def test_price_missing_but_brand_and_year_present_still_saved(self):
        # раньше: без единой цены -- пост отбрасывался целиком. Теперь --
        # сохраняется с price_usd=NULL (тут это "явно договорная", а не
        # ambiguous -- needs_review=False, price_type='negotiable').
        d = rx.try_extract("#Cobalt 2019 yil, mexanika, kelishiladi")
        self.assertIsNotNone(d)
        self.assertIsNone(d["price_usd"])
        self.assertEqual(d["price_type"], "negotiable")

    def test_pre_2000_year_now_captured(self):
        # health_check.py всегда принимал 1970-2029, а regex раньше
        # ограничивался 2000-2029 -- GAZ 53 1990 года не проходил вообще.
        d = rx.try_extract("GAZ 53 sotiladi, Yil: 1990, Narxi: 60.000.000 so'm")
        self.assertIsNotNone(d)
        self.assertEqual(d["year"], 1990)
        self.assertEqual(d["price_uzs"], 60_000_000.0)

    def test_no_brand_no_year_no_money_is_rejected(self):
        d = rx.try_extract("Ассалому алайкум, качество супер, звоните")
        self.assertIsNone(d)

    def test_installment_leak_never_becomes_price_end_to_end(self):
        d = rx.try_extract("#Gentra Boshiga 2,000$ — 13 oyga 500$ boshqa yana bor")
        self.assertIsNotNone(d)
        self.assertIsNone(d["price_usd"])
        self.assertTrue(d["needs_review"])
        flags = {f["code"] for f in rx.detect_flags(
            "#Gentra Boshiga 2,000$ — 13 oyga 500$ boshqa yana bor"
        )}
        self.assertIn("installment_price_mentioned", flags)


class NegationCases(unittest.TestCase):
    def test_cyrillic_negation_yoq_clears_flag(self):
        flags = {f["code"] for f in rx.detect_flags("банк йўқ, насия йўқ, нархи 8500$")}
        self.assertNotIn("installment_price_mentioned", flags)

    def test_latin_negation_still_works(self):
        flags = {f["code"] for f in rx.detect_flags("bo'lib to'lash va nasiya yo'q, faqat naqdga")}
        self.assertNotIn("installment_price_mentioned", flags)

    def test_real_accident_flag_not_broken_by_negation_changes(self):
        flags = {f["code"] for f in rx.detect_flags("avariya bo'lgan, ehtiyot bo'ling")}
        self.assertIn("accident_mentioned", flags)

    def test_negated_accident_not_flagged(self):
        flags = {f["code"] for f in rx.detect_flags("avariyaga uchramagan, toza")}
        self.assertNotIn("accident_mentioned", flags)

    def test_cyrillic_painted_flag_matches(self):
        # регрессия на порчу кодировки "бўялган" -> "бџялган" из более
        # раннего пуша (см. docs/PROJECT_OVERVIEW.md, найдено и починено).
        flags = {f["code"] for f in rx.detect_flags("рама бўялган, кузов тоза")}
        self.assertIn("painted_mentioned", flags)


class SoldConfirmationDetection(unittest.TestCase):
    def test_pure_confirmation_detected(self):
        self.assertTrue(rx.is_pure_sold_confirmation(
            "Aka mashina sotildi. Rahmat. E'lonni olib tashesizmi?"
        ))

    def test_structured_ad_with_sold_tag_not_pure(self):
        self.assertFalse(rx.is_pure_sold_confirmation(
            "Narxi: 15,600$ | Tel: #Sotildi 15300$"
        ))

    def test_normal_ad_without_sold_words_not_pure(self):
        self.assertFalse(rx.is_pure_sold_confirmation(
            "#Nexia 3 2018 yil Narxi: 8700$"
        ))


if __name__ == "__main__":
    unittest.main()
