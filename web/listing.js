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
  const badge = dealBadge(item.deal_score);
  const attrs = item.attrs || {};
  const photo = item.photos && item.photos.length
    ? `<img src="${item.photos[0]}" alt="" />`
    : "Фото по ссылке на оригинал";

  document.getElementById("content").innerHTML = `
    <div class="detail-photo">${photo}</div>
    <h1>${item.title || "Без названия"}</h1>
    <p class="card-meta">${item.city || "Город не указан"} · ${timeAgo(item.posted_at)}</p>
    <p class="card-price" style="font-size:22px">${formatPrice(item.price_usd, item.price_uzs)}</p>
    <div class="row">
      <span class="deal-badge ${badge.cls}">${badge.text}</span>
      <span class="source-tag">${SOURCE_LABEL[item.source] || item.source}</span>
    </div>

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
