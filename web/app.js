initTheme();

const state = Object.assign({ pageSize: 20 }, readFiltersFromUrl());

const els = {
  list: document.getElementById("cardList"),
  status: document.getElementById("statusLine"),
  pager: document.getElementById("pager"),
  pageLabel: document.getElementById("pageLabel"),
  prevPage: document.getElementById("prevPage"),
  nextPage: document.getElementById("nextPage"),
  statsLine: document.getElementById("statsLine"),
  sort: document.getElementById("fSort"),
  brand: document.getElementById("fBrand"),
  model: document.getElementById("fModel"),
  city: document.getElementById("fCity"),
  priceMin: document.getElementById("fPriceMin"),
  priceMax: document.getElementById("fPriceMax"),
  yearMin: document.getElementById("fYearMin"),
  yearMax: document.getElementById("fYearMax"),
  dealMin: document.getElementById("fDealMin"),
  filterToggle: document.getElementById("filterToggle"),
  filterCount: document.getElementById("filterCount"),
  filterSheet: document.getElementById("filterSheet"),
  filterOverlay: document.getElementById("filterOverlay"),
  closeFilters: document.getElementById("closeFilters"),
  resetFilters: document.getElementById("resetFilters"),
  applyFilters: document.getElementById("applyFilters"),
};

document.getElementById("themeToggle").addEventListener("click", toggleTheme);

// ---------------------------------------------------------------------
// Панель фильтров -- на мобильном это bottom sheet, на десктопе тот же
// компонент рендерится как центрированное модальное окно (см. style.css).
// Значения полей применяются только по кнопке "Показать" / Enter, чтобы
// не долбить API на каждый введённый символ.
// ---------------------------------------------------------------------

function openFilterSheet() {
  els.filterSheet.hidden = false;
  els.filterOverlay.hidden = false;
  els.filterToggle.setAttribute("aria-expanded", "true");
  document.body.style.overflow = "hidden";
  els.brand.focus();
}

function closeFilterSheet() {
  els.filterSheet.hidden = true;
  els.filterOverlay.hidden = true;
  els.filterToggle.setAttribute("aria-expanded", "false");
  document.body.style.overflow = "";
}

els.filterToggle.addEventListener("click", openFilterSheet);
els.closeFilters.addEventListener("click", closeFilterSheet);
els.filterOverlay.addEventListener("click", closeFilterSheet);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !els.filterSheet.hidden) closeFilterSheet();
});

function syncFieldsFromState() {
  els.sort.value = state.sort || "deal_score_desc";
  els.brand.value = state.brand || "";
  els.model.value = state.model || "";
  els.city.value = state.city || "";
  els.priceMin.value = state.price_min || "";
  els.priceMax.value = state.price_max || "";
  els.yearMin.value = state.year_min || "";
  els.yearMax.value = state.year_max || "";
  els.dealMin.value = state.deal_min || "";
}

function readFieldsIntoState() {
  state.brand = els.brand.value || undefined;
  state.model = els.model.value.trim().toLowerCase() || undefined;
  state.city = els.city.value || undefined;
  state.price_min = els.priceMin.value || undefined;
  state.price_max = els.priceMax.value || undefined;
  state.year_min = els.yearMin.value || undefined;
  state.year_max = els.yearMax.value || undefined;
  state.deal_min = els.dealMin.value || undefined;
}

function updateFilterCount() {
  const n = activeFilterCount(state);
  els.filterCount.hidden = n === 0;
  els.filterCount.textContent = String(n);
}

els.applyFilters.addEventListener("click", () => {
  readFieldsIntoState();
  state.page = 1;
  closeFilterSheet();
  updateFilterCount();
  writeFiltersToUrl(state);
  loadListings();
});

els.resetFilters.addEventListener("click", () => {
  for (const key of FILTER_KEYS) delete state[key];
  state.page = 1;
  syncFieldsFromState();
  updateFilterCount();
  writeFiltersToUrl(state);
  closeFilterSheet();
  loadListings();
});

els.sort.addEventListener("change", () => {
  state.sort = els.sort.value;
  state.page = 1;
  writeFiltersToUrl(state);
  loadListings();
});

// ---------------------------------------------------------------------
// Запрос списка
// ---------------------------------------------------------------------

function buildQuery() {
  const params = new URLSearchParams({ sort: state.sort, page: state.page, page_size: state.pageSize });
  for (const key of FILTER_KEYS) {
    if (state[key]) params.set(key, state[key]);
  }
  return params.toString();
}

// ---------------------------------------------------------------------
// Карточка -- вся построена через el(), без единой строки innerHTML с
// данными объявления. photo_url/source_url проходят через safeUrl().
// ---------------------------------------------------------------------

function photoNode(item) {
  const src = safeUrl(item.photo_url);
  const wrap = el("div", { class: "card-media" });
  if (!src) {
    wrap.appendChild(el("div", { class: "media-placeholder" }, "🚘"));
    return wrap;
  }
  const img = el("img", { loading: "lazy", decoding: "async", alt: "" });
  img.addEventListener("load", () => wrap.classList.add("loaded"));
  img.addEventListener("error", () => {
    clear(wrap);
    wrap.appendChild(el("div", { class: "media-placeholder" }, "🚘"));
  });
  img.src = src;
  wrap.appendChild(img);
  return wrap;
}

function warningChips(item) {
  const flags = sortedFlags(item.flags).slice(0, 2);
  if (!flags.length) return null;
  return el(
    "div",
    { class: "card-flags" },
    flags.map((f) =>
      el("span", { class: `flag-chip ${FLAG_SEVERITY_CLASS[f.severity] || "flag-info"}` }, f.label || "")
    )
  );
}

function dealBlock(item) {
  const badge = dealBadge(item);
  const wrap = el("div", { class: "card-deal" });
  wrap.appendChild(el("span", { class: `deal-pill ${badge.cls}` }, badge.text));
  if (dealTrust(item).ok) {
    wrap.appendChild(
      el("span", { class: "deal-note" }, `медиана $${fmtNum(item.segment_median_usd)} · ${item.segment_sample_size} объявл.`)
    );
  }
  return wrap;
}

function cardNode(item) {
  const price = priceParts(item);
  const specs = [item.year, item.mileage_km ? `${fmtNum(item.mileage_km)} км` : null].filter(Boolean).join(" · ");
  const metaText = [item.city, specs].filter(Boolean).join(" · ") || "—";
  const metaFull = [metaText, timeAgo(item.posted_at)].filter(Boolean).join(" · ");

  const priceNode = price.length
    ? el(
        "p",
        { class: "card-price" },
        price.map((p, i) => (i === 0 ? el("span", { class: "price-usd" }, p) : el("span", { class: "price-uzs" }, p)))
      )
    : el("p", { class: "card-price price-unknown" }, PRICE_TYPE_LABEL[item.price_type] || "Полная цена не определена");

  return el(
    "a",
    { class: "card", href: `listing.html?id=${encodeURIComponent(item.id)}` },
    [
      photoNode(item),
      el("div", { class: "card-body" }, [
        el("p", { class: "card-title" }, item.title || "Без названия"),
        el("p", { class: "card-meta" }, metaFull),
        priceNode,
        dealBlock(item),
        warningChips(item),
        el("div", { class: "card-foot" }, [
          el("span", { class: "source-tag" }, SOURCE_LABEL[item.source] || item.source),
        ]),
      ]),
    ]
  );
}

function skeletonCard() {
  return el("div", { class: "card skeleton" }, [
    el("div", { class: "card-media skeleton-block" }),
    el("div", { class: "card-body" }, [
      el("div", { class: "skeleton-line", style: "width:70%" }),
      el("div", { class: "skeleton-line", style: "width:45%" }),
      el("div", { class: "skeleton-line", style: "width:35%;height:18px;margin-top:8px" }),
    ]),
  ]);
}

async function loadFacets() {
  try {
    const data = await apiGet("/v1/facets");
    for (const b of data.brands) {
      els.brand.appendChild(el("option", { value: b }, b.charAt(0).toUpperCase() + b.slice(1)));
    }
    for (const c of data.cities) {
      els.city.appendChild(el("option", { value: c }, c));
    }
    syncFieldsFromState();
  } catch (e) {
    // фильтры не критичны -- если facets не отдались, лента всё равно работает
  }
}

let requestSeq = 0;

async function loadListings() {
  const seq = ++requestSeq;
  els.status.textContent = "";
  clear(els.list);
  const n = state.pageSize > 6 ? 6 : state.pageSize;
  for (let i = 0; i < n; i++) els.list.appendChild(skeletonCard());
  els.pager.style.display = "none";

  try {
    const data = await apiGet(`/v1/listings?${buildQuery()}`);
    if (seq !== requestSeq) return; // устаревший ответ -- пользователь уже сменил фильтры

    if (data.items.length === 0) {
      els.status.textContent = "";
      clear(els.list);
      els.list.appendChild(
        el("div", { class: "empty-state" }, [
          el("span", { class: "big-emoji" }, "🔍"),
          el("p", {}, "Ничего не найдено по этим фильтрам"),
          el("button", { class: "btn-ghost", onclick: () => els.resetFilters.click() }, "Сбросить фильтры"),
        ])
      );
      return;
    }

    els.status.textContent = `Найдено объявлений: ${fmtNum(data.total)}`;
    clear(els.list);
    for (const item of data.items) els.list.appendChild(cardNode(item));

    const totalPages = Math.max(1, Math.ceil(data.total / state.pageSize));
    els.pager.style.display = totalPages > 1 ? "flex" : "none";
    els.pageLabel.textContent = `${state.page} / ${totalPages}`;
    els.prevPage.disabled = state.page <= 1;
    els.nextPage.disabled = state.page >= totalPages;
  } catch (e) {
    if (seq !== requestSeq) return;
    els.status.textContent = "";
    clear(els.list);
    els.list.appendChild(
      el("div", { class: "error-state" }, [
        el("span", { class: "big-emoji" }, "⚠️"),
        el("p", {}, "Не удалось загрузить объявления. Проверьте соединение и попробуйте ещё раз."),
        el("button", { class: "btn-primary", onclick: () => loadListings() }, "Повторить"),
      ])
    );
  }
}

els.prevPage.addEventListener("click", () => {
  state.page -= 1;
  writeFiltersToUrl(state);
  loadListings();
  window.scrollTo({ top: 0, behavior: "smooth" });
});
els.nextPage.addEventListener("click", () => {
  state.page += 1;
  writeFiltersToUrl(state);
  loadListings();
  window.scrollTo({ top: 0, behavior: "smooth" });
});

async function loadStats() {
  try {
    const data = await apiGet("/v1/stats");
    if (data.total) {
      const parts = [`${fmtNum(data.total)} объявлений`];
      if (data.last_updated_at) parts.push(`обновлено ${timeAgo(data.last_updated_at)}`);
      els.statsLine.textContent = parts.join(" · ");
    }
  } catch (e) {
    // не критично, шапка проживёт без этой строки
  }
}

syncFieldsFromState();
updateFilterCount();
loadStats();
loadFacets();
loadListings();
