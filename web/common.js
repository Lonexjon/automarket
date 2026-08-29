// API_BASE: пусто = тот же хост что и сайт (относительные /v1/... запросы).
// Меняется на конкретный адрес API только для локальной разработки, если
// фронт и API крутятся раздельно.
const API_BASE = "";

const SOURCE_LABEL = { olx: "OLX", avtoelon: "Avtoelon", telegram: "Telegram" };

function initTheme() {
  const saved = localStorage.getItem("theme");
  const theme = saved || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.setAttribute("data-theme", theme);
  return theme;
}

function toggleTheme() {
  const current = document.documentElement.getAttribute("data-theme");
  const next = current === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("theme", next);
}

function dealBadge(dealScore) {
  // dealScore -- доля (0.2 = на 20% дешевле рынка), как в openapi.yaml.
  if (dealScore === null || dealScore === undefined) {
    return { cls: "deal-market", text: "Нет данных по рынку" };
  }
  if (dealScore > 0.2) return { cls: "deal-great", text: `Отличная цена, -${Math.round(dealScore * 100)}%` };
  if (dealScore > 0.1) return { cls: "deal-good", text: `Хорошая цена, -${Math.round(dealScore * 100)}%` };
  if (dealScore > -0.05) return { cls: "deal-market", text: "По рынку" };
  return { cls: "deal-over", text: `Дороже рынка, +${Math.round(-dealScore * 100)}%` };
}

function timeAgo(isoDate) {
  if (!isoDate) return "";
  const diffMs = Date.now() - new Date(isoDate).getTime();
  const hours = Math.floor(diffMs / 3_600_000);
  if (hours < 1) return "меньше часа назад";
  if (hours < 24) return `${hours} ч назад`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} дн назад`;
  return new Date(isoDate).toLocaleDateString("ru-RU");
}

// Когда price_usd/price_uzs оба пустые, price_type объясняет ПОЧЕМУ --
// первый взнос/ежемесячный платёж НИКОГДА не подставляются вместо цены
// (см. parsers/money.py), это не "не смогли распарсить", а осознанное
// "мы знаем, что это не полная цена".
const PRICE_TYPE_LABEL = {
  negotiable: "Цена договорная",
  down_payment: "Цена не указана (в тексте — только первый взнос по рассрочке)",
  monthly_payment: "Цена не указана (в тексте — только ежемесячный платёж)",
  installment: "Цена не указана (в тексте — условия рассрочки)",
  exchange_addition: "Цена не указана (в тексте — доплата к обмену)",
  unknown: "Цена не указана",
};

function formatPrice(item) {
  const parts = [];
  if (item.price_usd) parts.push(`$${item.price_usd.toLocaleString("ru-RU")}`);
  if (item.price_uzs) parts.push(`<span class="uzs">${item.price_uzs.toLocaleString("ru-RU")} сум</span>`);
  if (parts.length) return parts.join(" ");
  return PRICE_TYPE_LABEL[item.price_type] || "Цена не указана";
}

async function apiGet(path) {
  const resp = await fetch(`${API_BASE}${path}`);
  if (!resp.ok) throw new Error(`API ${resp.status}`);
  return resp.json();
}
