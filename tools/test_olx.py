"""
Regression-тесты на parsers/olx.py -- HTML-фрагменты ниже не выдуманы, это
вырезки из реальной страницы объявления OLX.uz (сохранённой владельцем
с домашнего IP 2026-08-31, https://www.olx.uz/d/obyavlenie/gentra-2020-
toza-sotiladi-ID4qEOc.html) и страницы категории -- та же структура,
что видит реальный пользователь, не предположение о ней.

Использование:
  python3 -m unittest tools/test_olx.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parsers"))
import olx  # noqa: E402

# Минимальный, но структурно точный фрагмент реальной страницы объявления --
# те же data-testid/классы, что в живой разметке, без рекламных/навигационных
# блоков, которые не влияют на парсинг.
REAL_LISTING_FRAGMENT = """
<div data-testid="offer_title" class="css-3pg7ks"><h4 class="css-1au435n">Gentra 2020 toza sotiladi</h4></div>
<div data-testid="prices-wrapper" class="css-kii8tg"><div class="css-sxnu2o">
<div data-testid="ad-price-container" class="css-e2ir3r">
<h3 class="css-yauxmy">10 500 у.е.</h3>
<p class="css-nw4rgq">Договорная</p>
</div></div></div>
<div data-testid="ad-parameters-container" class="css-6zsv65">
<p class="css-13x8d99"><span>Частное лицо</span></p>
<p class="css-13x8d99">Модель: Lacetti / Gentra</p>
<p class="css-13x8d99">Тип кузова: Седан</p>
<p class="css-13x8d99">Условия продажи: Кредит, Рассрочка</p>
<p class="css-13x8d99">Год выпуска: 2020 </p>
<p class="css-13x8d99">Пробег: 155 000 км</p>
<p class="css-13x8d99">Коробка передач: Автоматическая</p>
<p class="css-13x8d99">Цвет: Белый</p>
</div>
<div data-cy="ad_description" data-testid="ad_description" class="css-1m8mzwg">
<h3 class="css-aql5t3">Описание</h3>
<div class="css-19duwlz">Moshinani yaqinda polni shumka qildirdim 600$</div>
</div>
<div data-testid="ad-footer-bar-section"><span class="css-ooacec">ID: <!-- -->65458744</span></div>
<div data-testid="map-aside-section" class="css-1adosif">
<p class="css-1jl0zx2">Местоположение</p>
<div class="css-1q7h1ph"><section class="css-wefbef"><div class="css-13l8eec">
<div><p class="css-9pna1a">Навои, <span class="css-1bzg5dq"></span></p>
<p class="css-3cz5o2">Навоийская область</p></div>
</div></section></div></div>
<span data-cy="ad-posted-at" data-testid="ad-posted-at" class="css-1br3d2a">Опубликовано <!-- -->31 августа 2026 г.</span>
<div data-testid="image-galery-container"><div class="swiper-slide" data-testid="ad-photo">
<img src="https://frankfurt.apollo.olxcdn.com:443/v1/files/nqkuulzqkfws-UZ/image;s=563x1000" alt="Gentra 2020 toza sotiladi">
</div></div>
"""

# Фрагмент страницы категории -- ссылки на объявления встречаются в HTML
# как обычные href, в реальной странице их 52 разных, здесь -- 3 для теста.
REAL_CATEGORY_FRAGMENT = """
<a href="/d/obyavlenie/bmw-m5-f10-535i-ID4siV1.html">BMW M5</a>
<a href="/d/obyavlenie/bmw-x5-50i-2011yil-ID4dI4n.html">BMW X5</a>
<a href="/d/obyavlenie/chevrolet-epica-2-0-mexanika-ID4sA0o.html">Epica</a>
<a href="/d/obyavlenie/bmw-m5-f10-535i-ID4siV1.html">BMW M5 duplicate link</a>
"""


class ParseListingHtmlRealCases(unittest.TestCase):
    def test_real_listing_full_extraction(self):
        r = olx.parse_listing_html(REAL_LISTING_FRAGMENT, "https://www.olx.uz/d/obyavlenie/x-ID4qEOc.html")
        self.assertIsNotNone(r)
        self.assertEqual(r.title, "Gentra 2020 toza sotiladi")
        self.assertEqual(r.price_usd, 10500.0)
        self.assertIsNone(r.price_uzs)
        # "ID: 65458744" в разметке недоступен regex'ом (см. olx.py) --
        # source_id берём из URL-слага, он стабилен и уникален на объявление.
        self.assertEqual(r.source_id, "ID4qEOc")
        self.assertTrue(r.attrs.get("negotiable"))
        self.assertEqual(r.attrs.get("model_raw"), "Lacetti / Gentra")
        self.assertEqual(r.attrs.get("year_raw"), "2020")
        self.assertEqual(r.attrs.get("mileage_raw"), "155 000 км")
        self.assertEqual(r.attrs.get("transmission_raw"), "Автоматическая")
        self.assertEqual(r.city, "Навои")  # не "Местоположение" -- см. регрессию ниже
        self.assertEqual(r.posted_at, "31 августа 2026 г.")
        self.assertEqual(len(r.photo_urls), 1)
        self.assertTrue(r.description_raw.startswith("Moshinani"))

    def test_city_regression_not_section_label(self):
        # Реальный баг найден на первой версии парсера: селектор захватывал
        # заголовок секции "Местоположение" вместо самого города, потому
        # что это был ПЕРВЫЙ <p> внутри map-aside-section.
        r = olx.parse_listing_html(REAL_LISTING_FRAGMENT, "x")
        self.assertNotEqual(r.city, "Местоположение")
        self.assertEqual(r.city, "Навои")

    def test_empty_page_returns_none(self):
        r = olx.parse_listing_html("<html><body>error page</body></html>", "x")
        self.assertIsNone(r)


class DiscoverListingUrlsRealCases(unittest.TestCase):
    def test_extracts_and_dedupes_urls_from_category_page(self):
        urls = []
        seen = set()
        for m in olx.LISTING_URL_RE.finditer(REAL_CATEGORY_FRAGMENT):
            url = m.group(0)
            full = "https://www.olx.uz" + url if not url.startswith("http") else url
            if full not in seen:
                seen.add(full)
                urls.append(full)
        self.assertEqual(len(urls), 3)  # дубликат BMW M5 схлопнулся
        self.assertTrue(all(u.startswith("https://www.olx.uz/d/obyavlenie/") for u in urls))


if __name__ == "__main__":
    unittest.main()
