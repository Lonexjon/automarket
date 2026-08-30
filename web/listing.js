initTheme();
document.getElementById("themeToggle").addEventListener("click", toggleTheme);

const ATTR_LABELS = {
  brand: "Марка",
  model: "Модель",
  year: "Год",
  mileage_km: "Пробег",
  transmission: "Коробка",
  customs_cleared: "Растаможен",
};

function attrValue(key, attrs) {
  const v = attrs[key];
  if (v === null || v === undefined || v === "") return null;
  if (key === "mileage_km") return `${fmtNum(v)} км`;
  if (key === "transmission") return v === "automatic" ? "Автомат" : v === "manual" ? "Механика" : null;
  if (key === "customs_cleared") return v === true ? "Да" : v === false ? "Нет" : null;
  if (key === "brand" || key === "model") return v.charAt(0).toUpperCase() + v.slice(1);
  return v;
}

function galleryNode(item) {
  const photos = (item.photos || []).map(safeUrl).filter(Boolean);
  if (!photos.length) {
    return el("div", { class: "detail-photo" }, el("div", { class: "media-placeholder media-placeholder-lg" }, "🚘"));
  }
  const wrap = el("div", { class: "detail-photo" });
  let index = 0;
  const img = el("img", { alt: "", loading: "eager", decoding: "async" });
  img.addEventListener("error", () => {
    clear(wrap);
    wrap.appendChild(el("div", { class: "media-placeholder media-placeholder-lg" }, "🚘"));
  });
  img.src = photos[0];
  wrap.appendChild(img);

  if (photos.length > 1) {
    const dots = el(
      "div",
      { class: "gallery-dots" },
      photos.map((_, i) => el("span", { class: i === 0 ? "dot active" : "dot" }))
    );
    const prev = el("button", { class: "gallery-nav gallery-prev", "aria-label": "Предыдущее фото" }, "‹");
    const next = el("button", { class: "gallery-nav gallery-next", "aria-label": "Следующее фото" }, "›");
    const show = (i) => {
      index = (i + photos.length) % photos.length;
      img.src = photos[index];
      dots.querySelectorAll(".dot").forEach((d, di) => d.classList.toggle("active", di === index));
    };
    prev.addEventListener("click", () => show(index - 1));
    next.addEventListener("click", () => show(index + 1));
    wrap.appendChild(prev);
    wrap.appendChild(next);
    wrap.appendChild(dots);
  }
  return wrap;
}

function dealExplanationNode(item) {
  const trust = dealTrust(item);
  const badge = dealBadge(item);
  const box = el("div", { class: "deal-box" });
  box.appendChild(el("span", { class: `deal-pill deal-pill-lg ${badge.cls}` }, badge.text));

  if (trust.ok) {
    box.appendChild(
      el("p", { class: "deal-explain" }, [
        `Медиана похожих объявлений: `,
        el("strong", {}, `$${fmtNum(item.segment_median_usd)}`),
        `. Сравнение по ${item.segment_sample_size} объявлениям (та же марка, модель и близкий год).`,
      ])
    );
  } else if (trust.reason === "not_full_price") {
    box.appendChild(el("p", { class: "deal-explain" }, "В тексте объявления это не полная цена машины — сравнивать не с чем."));
  } else if (trust.reason === "needs_review" || trust.reason === "low_confidence") {
    box.appendChild(el("p", { class: "deal-explain" }, "В тексте несколько похожих на цену сумм — проверьте оригинал, прежде чем доверять числу."));
  } else if (trust.reason === "no_segment" || trust.reason === "small_segment") {
    box.appendChild(el("p", { class: "deal-explain" }, "Похожих объявлений (та же марка, модель и год) пока слишком мало для честного сравнения."));
  } else if (trust.reason === "critical_flag") {
    box.appendChild(el("p", { class: "deal-explain" }, "У объявления есть предупреждение ниже — сравнение с рынком по такой цене не показываем."));
  }

  box.appendChild(
    el("p", { class: "deal-tooltip" }, "Оценка рассчитана сравнением с автомобилями той же модели и близкого года — на фронтенде ничего не пересчитывается.")
  );
  return box;
}

function attrsTable(item) {
  const attrs = item.attrs || {};
  const rows = Object.keys(ATTR_LABELS)
    .map((key) => [ATTR_LABELS[key], attrValue(key, attrs)])
    .filter(([, v]) => v !== null);
  if (!rows.length) return null;
  const dl = el("dl", { class: "attrs" });
  for (const [label, value] of rows) {
    dl.appendChild(el("dt", {}, label));
    dl.appendChild(el("dd", {}, String(value)));
  }
  return dl;
}

function flagsList(item) {
  const flags = sortedFlags(item.flags);
  if (!flags.length) return null;
  return el(
    "div",
    { class: "flags-list" },
    flags.map((f) => {
      const srcNote = FLAG_SOURCE_LABEL[f.source];
      return el("div", { class: `flag-item ${FLAG_SEVERITY_CLASS[f.severity] || "flag-info"}` }, [
        el("span", { class: "flag-icon" }, f.severity === "negative" ? "⛔" : f.severity === "warning" ? "⚠️" : "ℹ️"),
        el("span", { class: "flag-text" }, f.label || ""),
        srcNote ? el("span", { class: "flag-source" }, `(${srcNote})`) : null,
      ]);
    })
  );
}

function priceHistoryNode(item) {
  const points = item.price_history || [];
  if (points.length < 2) return null;
  const values = points.map((p) => p.price_usd);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const bars = el(
    "div",
    { class: "price-history-bars" },
    points.map((p) => {
      const h = 12 + ((p.price_usd - min) / range) * 48;
      const bar = el("div", { class: "price-history-bar", style: `height:${h}px` });
      bar.title = `${p.date}: $${fmtNum(p.price_usd)}`;
      return bar;
    })
  );
  return el("section", { class: "detail-section" }, [el("h2", {}, "История цены"), bars]);
}

function needsReviewNote(item) {
  if (!item.needs_review) return null;
  return el("div", { class: "flag-item flag-warning" }, [
    el("span", { class: "flag-icon" }, "⚠️"),
    el(
      "span",
      { class: "flag-text" },
      "Цена в тексте объявления неоднозначна (несколько похожих на цену сумм) — проверьте по оригиналу, прежде чем доверять ей."
    ),
  ]);
}

function render(item) {
  const content = document.getElementById("content");
  clear(content);

  const price = priceParts(item);
  const priceNode = price.length
    ? el("p", { class: "detail-price" }, price.map((p, i) => el("span", { class: i === 0 ? "price-usd" : "price-uzs" }, p)))
    : el("p", { class: "detail-price price-unknown" }, PRICE_TYPE_LABEL[item.price_type] || "Полная цена не определена");

  const specs = [item.year, item.attrs && item.attrs.mileage_km ? `${fmtNum(item.attrs.mileage_km)} км` : null]
    .filter(Boolean)
    .join(" · ");

  const safeSourceUrl = safeUrl(item.source_url);

  const ctaButton = safeSourceUrl
    ? el("a", { class: "cta", href: safeSourceUrl, target: "_blank", rel: "noopener noreferrer" }, "Открыть оригинальное объявление →")
    : null;

  const blocks = [
    galleryNode(item),
    el("h1", {}, item.title || "Без названия"),
    el("p", { class: "card-meta" }, [item.city || "Город не указан", specs].filter(Boolean).join(" · ") + " · " + timeAgo(item.posted_at)),
    priceNode,
    el("div", { class: "row" }, [el("span", { class: "source-tag" }, SOURCE_LABEL[item.source] || item.source)]),
    dealExplanationNode(item),
    attrsTable(item),
    needsReviewNote(item),
    flagsList(item),
    priceHistoryNode(item),
    item.description_raw
      ? el("section", { class: "detail-section" }, [
          el("h2", {}, "Исходный текст объявления"),
          el("div", { class: "desc-raw" }, item.description_raw),
        ])
      : null,
  ];

  for (const b of blocks) if (b) content.appendChild(b);

  if (ctaButton) {
    content.appendChild(ctaButton);
    // На мобильном дублируем в закреплённую нижнюю панель -- кнопка всегда
    // на виду, не нужно долистывать длинное описание, чтобы её найти.
    const stickyBar = el("div", { class: "sticky-cta" }, [
      el("a", { class: "cta", href: safeSourceUrl, target: "_blank", rel: "noopener noreferrer" }, "Открыть оригинал →"),
    ]);
    document.body.appendChild(stickyBar);
    document.body.classList.add("has-sticky-cta");
  }
}

function renderNotFound() {
  const content = document.getElementById("content");
  clear(content);
  content.appendChild(
    el("div", { class: "error-state" }, [
      el("span", { class: "big-emoji" }, "🚫"),
      el("p", {}, "Объявление не найдено или уже удалено"),
      el("a", { class: "btn-ghost", href: "index.html" }, "Ко всем объявлениям"),
    ])
  );
}

async function main() {
  const id = new URLSearchParams(location.search).get("id");
  if (!id) {
    renderNotFound();
    return;
  }
  try {
    const item = await apiGet(`/v1/listings/${encodeURIComponent(id)}`);
    render(item);
  } catch (e) {
    renderNotFound();
  }
}

main();
