"""
Regression-тесты на parsers/avtoelon.py -- HTML-фрагмент ниже не выдуман,
это вырезка из реальной страницы объявления Avtoelon.uz (сохранённой
владельцем с домашнего IP 2026-08-31, https://avtoelon.uz/a/show/7326832,
"ВАЗ (Lada) Vesta 2019"), не предположение о структуре сайта.

До этой сессии парсер считался client-rendered SPA (пустой HTML-каркас) --
это оказалось неверным предположением, сделанным из-за инфраструктурной
особенности песочницы разработки (см. docstring в avtoelon.py). Реальная
страница -- обычный server-rendered HTML.

Использование:
  python3 -m unittest tools/test_avtoelon.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parsers"))
import avtoelon  # noqa: E402

REAL_ADVERT_FRAGMENT = """
<h1 class="a-title__text">
    <span itemprop="brand"> ВАЗ (Lada)</span> <span itemprop="name">Vesta </span>
</h1>
<div class="a-price">
    <span class="a-price__text">    ~5&nbsp;084&nbsp;y.e.
</span>
</div>
<dl class="clearfix dl-horizontal description-params">
    <dt class="value-title">Город</dt>
    <dd class="value clearfix"><a href="/avto/vaz/vesta/gorod-fergana/">Фергана</a></dd>
    <dt class="value-title">Год</dt>
    <dd class="value clearfix"><a href="/avto/vaz/vesta/?year[from]=2019&amp;year[to]=2019">2019</a></dd>
    <dt class="value-title">Объем двигателя, л</dt>
    <dd class="value clearfix"><a href="/avto/vaz/vesta/?auto-fuel=3">1.6</a> (Газ-бензин)</dd>
    <dt class="value-title">Пробег</dt>
    <dd class="value clearfix">190 км</dd>
    <dt class="value-title">Коробка передач</dt>
    <dd class="value clearfix"><a href="/avto/vaz/vesta/?auto-car-transm=1">Механика</a></dd>
    <dt class="value-title">Цвет</dt>
    <dd class="value clearfix"><a href="/avto/vaz/vesta/?auto-color=15">Жёлтый</a></dd>
    <dt class="value-title">Состояние краски</dt>
    <dd class="value clearfix">Есть пятно</dd>
    <dt class="value-title">Привод</dt>
    <dd class="value clearfix">Передний</dd>
    <dt class="value-title">Торг есть</dt>
    <dd class="value clearfix">Да</dd>
</dl>
<ul class="photo-list">
    <li><a class="small-thumb" href="https://kluz-photos-tasinha.kcdn.online/webp/b3/x/10-full.webp"><img src="https://kluz-photos-tasinha.kcdn.online/webp/b3/x/10-60x45.webp"></a></li>
    <li><a class="small-thumb" href="https://kluz-photos-tasinha.kcdn.online/webp/b3/x/11-full.webp"><img src="https://kluz-photos-tasinha.kcdn.online/webp/b3/x/11-60x45.webp"></a></li>
</ul>
"""


class ParseAdvertHtmlRealCases(unittest.TestCase):
    def test_real_advert_full_extraction(self):
        r = avtoelon.parse_advert_html(REAL_ADVERT_FRAGMENT, "https://avtoelon.uz/a/show/7326832")
        self.assertIsNotNone(r)
        self.assertEqual(r.source_id, "7326832")
        self.assertEqual(r.title, "ВАЗ (Lada) Vesta")
        self.assertEqual(r.price_usd, 5084.0)
        self.assertIsNone(r.price_uzs)
        self.assertEqual(r.city, "Фергана")
        self.assertEqual(r.attrs.get("year_raw"), "2019")
        self.assertEqual(r.attrs.get("mileage_raw"), "190 км")
        self.assertEqual(r.attrs.get("transmission_raw"), "Механика")
        self.assertEqual(r.attrs.get("color_raw"), "Жёлтый")
        self.assertNotIn("city_raw", r.attrs)  # город переносится в r.city, не остаётся в attrs
        self.assertEqual(len(r.photo_urls), 2)
        self.assertTrue(all(u.endswith("-full.webp") for u in r.photo_urls))  # полное разрешение, не превью

    def test_price_in_som_not_misread_as_usd(self):
        html_som = REAL_ADVERT_FRAGMENT.replace(
            '<span class="a-price__text">    ~5&nbsp;084&nbsp;y.e.\n</span>',
            '<span class="a-price__text">124 080 600 сум</span>',
        )
        r = avtoelon.parse_advert_html(html_som, "https://avtoelon.uz/a/show/7326832")
        self.assertEqual(r.price_uzs, 124080600.0)
        self.assertIsNone(r.price_usd)

    def test_empty_page_returns_none(self):
        r = avtoelon.parse_advert_html("<html><body>error page</body></html>", "https://avtoelon.uz/a/show/1")
        self.assertIsNone(r)


class DiscoverAdvertUrlsRegex(unittest.TestCase):
    def test_matches_real_sitemap_style_urls(self):
        text = (
            "<loc>https://avtoelon.uz/a/show/7326832</loc>"
            "<loc>https://avtoelon.uz/a/show/7330282</loc>"
        )
        ids = avtoelon.ADVERT_URL_RE.findall(text)
        self.assertEqual(ids, ["7326832", "7330282"])


if __name__ == "__main__":
    unittest.main()
