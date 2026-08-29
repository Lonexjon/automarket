document.getElementById("themeToggle").addEventListener("click", toggleTheme);

const FLAG_SOURCE_LABEL = {
  text: "",
  photo_heuristic: "предположение по фото",
  price_history: "по истории цены",
};

function attrRow(label, value) {
  if (value === null || value === undefined || value === "") return "";
  return `<dt>${label}</dt><dd>${value}</dd>`;
}

function flagItem(flag) {
  const srcClass = flag.source === "photo_heuristic" ? "src-heuristic" : "src-text";
  const srcNote = FLAG_SOURCE_LABEL[flag.source] || "";
  return `<div class="flag-item"><span>${flag.source === "photo_heuristic" ? "❓" : "⚠️"} ${flag.label}</span>${srcNote ? `<span class="${srcClass}">(${srcNote})</span>` : ""}</div>`;
}

function render(item) {
  const attrs = item.attrs || {};
  const photo = item.photos && item.photos.length
    ? `<img src="${item.photos[0]}" alt="" />`
    : "Фото по ссылке на оригинал";

  // Бейдж выгодной цены -- только когда есть настоящая цена (см. app.js
  // cardHtml для той же логики и почему). Иначе поясняем словами, что
  // за деньги упомянуты в тексте (первый взнос/договорная/рассрочка).
  const priceBadge = item.price_usd
    ? (() => { const b = dealBadge(item.deal_score); return `<span class="deal-badge ${b.cls}">${b.text}</span>`; })()
    : `<span class="deal-badge deal-market">Цена не определена</span>`;

  const dealExplanation = (item.price_usd && item.segment_median_usd) ? `
    <p class="card-meta">
      Цена сравнивается с медианой ${item.segment_sample_size} похожих объявлений
      (${attrs.brand || "?"} ${attrs.model || ""} ${attrs.year || ""}).
      ${item.deal_score > 0
        ? `Эта цена ниже медианы на ${Math.round(item.deal_score * 100)}%.`
        : item.deal_score < 0
          ? `Эта цена выше медианы на ${Math.round(-item.deal_score * 100)}%.`
          : "Эта цена примерно на уровне медианы."}
    </p>
  ` : "";

  const needsReviewWarning = item.needs_review ? `
    <div class="flag-item"><span>⚠️ Цена в тексте объявления неоднозначна (несколько похожих на цену сумм) -- проверьте по оригиналу перед тем как доверять ей.</span></div>
  ` : "";

  document.getElementById("content").innerHTML = `
    <div class="detail-photo">${photo}</div>
    <h1>${item.title || "Без названия"}</h1>
    <p class="card-meta">${item.city || "Город не указан"} · ${timeAgo(item.posted_at)}</p>
    <p class="card-price" style="font-size:22px">${formatPrice(item)}</p>
    <div class="row">
      ${priceBadge}
      <span class="source-tag">${SOURCE_LABEL[item.source] || item.source}</span>
    </div>
    ${dealExplanation}

    <dl class="attrs">
      ${attrRow("Марка", attrs.brand)}
      ${attrRow("Модель", attrs.model)}
      ${attrRow("Год", attrs.year)}
      ${attrRow("Пробег", attrs.mileage_km ? `${attrs.mileage_km.toLocaleString("ru-RU")} км` : null)}
      ${attrRow("Коробка", attrs.transmission === "automatic" ? "Автомат" : attrs.transmission === "manual" ? "Механика" : null)}
      ${attrRow("Растаможен", attrs.customs_cleared === true ? "Да" : attrs.customs_cleared === false ? "Нет" : null)}
    </dl>

    ${item.segment_median_usd ? `
      <div class="segment-box">
        <strong>Медиана по ${item.segment_sample_size || "?"} похожим объявлениям:</strong>
        $${item.segment_median_usd.toLocaleString("ru-RU")}
      </div>
    ` : ""}

    ${needsReviewWarning}

    ${item.flags && item.flags.length ? `
      <div class="flags-list">${item.flags.map(flagItem).join("")}</div>
    ` : ""}

    ${item.description_raw ? `
      <p class="card-meta">Исходный текст объявления:</p>
      <div class="desc-raw">${item.description_raw.replace(/</g, "&lt;")}</div>
    ` : ""}

    <a class="cta" href="${item.source_url}" target="_blank" rel="noopener">Открыть объявление →</a>
  `;
}

async function main() {
  const id = new URLSearchParams(location.search).get("id");
  if (!id) {
    document.getElementById("content").innerHTML = '<div class="error-state">Объявление не указано</div>';
    return;
  }
  try {
    const item = await apiGet(`/v1/listings/${encodeURIComponent(id)}`);
    render(item);
  } catch (e) {
    document.getElementById("content").innerHTML = '<div class="error-state">Объявление не найдено или удалено</div>';
  }
}

main();
