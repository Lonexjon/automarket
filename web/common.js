// API_BASE: пусто = тот же хост что и сайт (относительные /v1/... запросы).
// Меняется на конкретный адрес API только для локальной разработки, если
// фронт и API крутятся раздельно.
const API_BASE = "";

const SOURCE_LABEL = { olx: "OLX", avtoelon: "Avtoelon", telegram: "Telegram" };

// ---------------------------------------------------------------------
// Тема
// ---------------------------------------------------------------------

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

// ---------------------------------------------------------------------
// Безопасное построение DOM -- ни один кусок текста от API не должен
// проходить через innerHTML. el() всегда кладёт строки как textContent
// (через createTextNode), никогда не парсит их как разметку.
// ---------------------------------------------------------------------

function el(tag, attrs, children) {
  attrs = attrs || {};
  const node = document.createElement(tag);
  for (const key in attrs) {
    const value = attrs[key];
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  const list = children === undefined || children === null ? [] : Array.isArray(children) ? children : [children];
  for (const child of list) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(typeof child === "string" || typeof child === "number" ? document.createTextNode(String(child)) : child);
  }
  return node;
}

function clear(node) {
  node.replaceChildren();
}

// Разрешаем в src/href только http(s) -- никаких javascript:, data: и
// прочего из полей, которые в теории пришли из чужого источника
// объявления (source_url, photo_url).
function safeUrl(raw) {
  if (!raw) return null;
  try {
    const u = new URL(raw, location.href);
    if (u.protocol === "http:" || u.protocol === "https:") return u.href;
  } catch (e) {
    /* невалидный URL -- игнорируем */
  }
  return null;
}

// ---------------------------------------------------------------------
// Утилиты
// ---------------------------------------------------------------------

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
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

function fmtNum(n) {
  return n.toLocaleString("ru-RU");
}

// Когда price_usd/price_uzs оба пустые, price_type объясняет ПОЧЕМУ --
// первый взнос/ежемесячный платёж НИКОГДА не подставляются вместо цены
// (см. parsers/money.py), это не "не смогли распарсить", а осознанное
// "мы знаем, что это не полная цена".
const PRICE_TYPE_LABEL = {
  negotiable: "Цена договорная",
  down_payment: "В тексте указан только первый взнос по рассрочке",
  monthly_payment: "В тексте указан только ежемесячный платёж",
  installment: "В тексте только условия рассрочки, без полной цены",
  exchange_addition: "В тексте только доплата к обмену",
  unknown: "Полная цена не определена",
};

// Возвращает массив строк ["$7 200", "91 640 000 сум"] -- без разметки,
// каждую строку кладём в textContent на вызывающей стороне.
function priceParts(item) {
  const parts = [];
  if (item.price_usd) parts.push(`$${fmtNum(item.price_usd)}`);
  if (item.price_uzs) parts.push(`${fmtNum(item.price_uzs)} сум`);
  return parts;
}

// ---------------------------------------------------------------------
// Доверие к цене / deal_score -- единственное место, где решается, можно
// ли показать бейдж "выгодная цена". Ничего здесь НЕ ПЕРЕСЧИТЫВАЕТ
// deal_score -- только читает то, что уже посчитал backend, и решает,
// достаточно ли данные надёжны, чтобы показать их как факт, а не как
// вводящую в заблуждение догадку.
// ---------------------------------------------------------------------

const MIN_SEGMENT_SIZE = 3;

function dealTrust(item) {
  if (!item.price_usd) return { ok: false, reason: "no_price" };
  if (item.price_type && item.price_type !== "full_price") return { ok: false, reason: "not_full_price" };
  if (item.needs_review) return { ok: false, reason: "needs_review" };
  // price_confidence приходит из money.py как категория (high/medium/low),
  // не число -- сравнение со числом здесь было бы всегда false и никогда
  // не срабатывало бы.
  if (item.price_confidence === "low") return { ok: false, reason: "low_confidence" };
  if ((item.flags || []).some((f) => f.severity === "negative")) return { ok: false, reason: "critical_flag" };
  if (item.deal_score === null || item.deal_score === undefined) return { ok: false, reason: "no_segment" };
  if (item.segment_sample_size != null && item.segment_sample_size < MIN_SEGMENT_SIZE) {
    return { ok: false, reason: "small_segment" };
  }
  return { ok: true };
}

const DEAL_NEUTRAL_LABEL = {
  no_price: "Полная цена не определена",
  needs_review: "Цена требует проверки",
  low_confidence: "Цена под вопросом",
  no_segment: "Недостаточно данных для сравнения",
  small_segment: "Недостаточно похожих объявлений",
  critical_flag: "Требует внимания",
};

// Возвращает { cls, text } для компактного бейджа на карточке и на
// детальной странице. cls управляет цветом (см. style.css .deal-*).
function dealBadge(item) {
  const trust = dealTrust(item);
  if (!trust.ok) {
    if (trust.reason === "not_full_price") {
      return { cls: "deal-warn", text: PRICE_TYPE_LABEL[item.price_type] || "Цена не определена" };
    }
    if (trust.reason === "needs_review" || trust.reason === "low_confidence") {
      return { cls: "deal-warn", text: DEAL_NEUTRAL_LABEL[trust.reason] };
    }
    if (trust.reason === "critical_flag") {
      return { cls: "deal-critical", text: DEAL_NEUTRAL_LABEL[trust.reason] };
    }
    return { cls: "deal-neutral", text: DEAL_NEUTRAL_LABEL[trust.reason] || "Нет данных" };
  }
  const score = item.deal_score;
  const pct = Math.round(Math.abs(score) * 100);
  if (score > 0.2) return { cls: "deal-great", text: `Отличная цена, −${pct}%` };
  if (score > 0.1) return { cls: "deal-good", text: `Хорошая цена, −${pct}%` };
  if (score > -0.05) return { cls: "deal-neutral", text: "По рынку" };
  return { cls: "deal-over", text: `Дороже рынка, +${pct}%` };
}

// ---------------------------------------------------------------------
// Флаги -- визуально разделяем severity, не красим всё одним цветом.
// ---------------------------------------------------------------------

const FLAG_SEVERITY_CLASS = { info: "flag-info", warning: "flag-warning", negative: "flag-critical" };
const FLAG_SEVERITY_ORDER = { negative: 0, warning: 1, info: 2 };
const FLAG_SOURCE_LABEL = {
  photo_heuristic: "по фото, предположение",
  price_history: "по истории цены",
};

function sortedFlags(flags) {
  return [...(flags || [])].sort((a, b) => (FLAG_SEVERITY_ORDER[a.severity] ?? 3) - (FLAG_SEVERITY_ORDER[b.severity] ?? 3));
}

// ---------------------------------------------------------------------
// Состояние фильтров <-> URL. Всё, что не page/sort -- ключ фильтра.
// ---------------------------------------------------------------------

const FILTER_KEYS = ["brand", "model", "city", "price_min", "price_max", "year_min", "year_max", "deal_min"];

function readFiltersFromUrl() {
  const params = new URLSearchParams(location.search);
  const state = { page: parseInt(params.get("page") || "1", 10) || 1, sort: params.get("sort") || "deal_score_desc" };
  for (const key of FILTER_KEYS) {
    const v = params.get(key);
    if (v) state[key] = v;
  }
  return state;
}

function writeFiltersToUrl(state) {
  const params = new URLSearchParams();
  for (const key of FILTER_KEYS) {
    if (state[key]) params.set(key, state[key]);
  }
  if (state.sort && state.sort !== "deal_score_desc") params.set("sort", state.sort);
  if (state.page && state.page > 1) params.set("page", state.page);
  const qs = params.toString();
  const url = qs ? `${location.pathname}?${qs}` : location.pathname;
  history.replaceState(null, "", url);
}

function activeFilterCount(state) {
  return FILTER_KEYS.reduce((n, key) => n + (state[key] ? 1 : 0), 0);
}

async function apiGet(path) {
  const resp = await fetch(`${API_BASE}${path}`);
  if (!resp.ok) throw new Error(`API ${resp.status}`);
  return resp.json();
}
