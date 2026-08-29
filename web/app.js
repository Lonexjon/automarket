const state = { page: 1, pageSize: 20, total: 0 };

const els = {
  list: document.getElementById("cardList"),
  status: document.getElementById("statusLine"),
  pager: document.getElementById("pager"),
  pageLabel: document.getElementById("pageLabel"),
  prevPage: document.getElementById("prevPage"),
  nextPage: document.getElementById("nextPage"),
  brand: document.getElementById("fBrand"),
  city: document.getElementById("fCity"),
  priceMax: document.getElementById("fPriceMax"),
  sort: document.getElementById("fSort"),
};

document.getElementById("themeToggle").addEventListener("click", toggleTheme);

function buildQuery() {
  const params = new URLSearchParams({
    sort: els.sort.value,
    page: state.page,
    page_size: state.pageSize,
  });
  if (els.brand.value) params.set("brand", els.brand.value);
  if (els.city.value) params.set("city", els.city.value);
  if (els.priceMax.value) params.set("price_max", els.priceMax.value);
  return params.toString();
}

function cardHtml(item) {
  const flag = (item.flags || [])[0];
  const photo = item.photo_url
    ? `<img src="${item.photo_url}" alt="" loading="lazy" />`
    : "фото по ссылке";

  const specs = [item.year, item.mileage_km ? `${item.mileage_km.toLocaleString("ru-RU")} км` : null]
    .filter(Boolean).join(" · ");

  // Бейдж "выгодная цена" показываем ТОЛЬКО когда есть настоящая цена --
  // если price_usd пустой (первый взнос/договорная/рассрочка и т.п.),
  // сравнивать не с чем, и бейдж "Нет данных по рынку" выглядел бы как
  // "мы посмотрели и не нашли скидку", хотя на деле цену вообще не знаем.
  const priceBadge = item.price_usd
    ? (() => { const b = dealBadge(item.deal_score); return `<span class="deal-badge ${b.cls}">${b.text}</span>`; })()
    : `<span class="deal-badge deal-market">Цена не определена</span>`;

  // needs_review приходит из money.py: цена технически есть, но текст
  // объявления неоднозначен (несколько похожих на цену сумм) -- без этой
  // пометки цена в ленте выглядела бы так же надёжно, как честная,
  // а разбор неоднозначности читатель увидит только на детальной странице.
  const reviewBadge = item.needs_review
    ? `<span class="deal-badge deal-market">⚠️ Требует проверки</span>`
    : "";

  return `
    <a class="card" href="listing.html?id=${encodeURIComponent(item.id)}">
      <div class="card-photo">${photo}</div>
      <div class="card-body">
        <p class="card-title">${item.title || "Без названия"}</p>
        <p class="card-meta">${[item.city, specs].filter(Boolean).join(" · ") || "—"} · ${timeAgo(item.posted_at)}</p>
        <p class="card-price">${formatPrice(item)}</p>
        <div class="row">
          ${priceBadge}
          ${reviewBadge}
          <span class="source-tag">${SOURCE_LABEL[item.source] || item.source}</span>
          ${flag ? `<span class="flag-tag">${flag.label}</span>` : ""}
        </div>
      </div>
    </a>
  `;
}

function skeletonHtml(n) {
  return Array.from({ length: n }, () => `
    <div class="card skeleton">
      <div class="card-photo skeleton-block"></div>
      <div class="card-body">
        <div class="skeleton-line" style="width:70%"></div>
        <div class="skeleton-line" style="width:45%"></div>
        <div class="skeleton-line" style="width:35%;height:18px;margin-top:8px"></div>
      </div>
    </div>
  `).join("");
}

async function loadFacets() {
  try {
    const data = await apiGet("/v1/facets");
    for (const b of data.brands) {
      const opt = document.createElement("option");
      opt.value = b;
      opt.textContent = b[0].toUpperCase() + b.slice(1);
      els.brand.appendChild(opt);
    }
    for (const c of data.cities) {
      const opt = document.createElement("option");
      opt.value = c;
      opt.textContent = c;
      els.city.appendChild(opt);
    }
  } catch (e) {
    // фильтры не критичны -- если facets не отдались, лента всё равно работает
  }
}

async function loadListings() {
  els.status.textContent = "";
  els.list.innerHTML = skeletonHtml(state.pageSize > 6 ? 6 : state.pageSize);
  els.pager.style.display = "none";

  try {
    const data = await apiGet(`/v1/listings?${buildQuery()}`);
    state.total = data.total;

    if (data.items.length === 0) {
      els.status.textContent = "";
      els.list.innerHTML = '<div class="empty-state"><span class="big-emoji">🔍</span>Ничего не найдено по этим фильтрам</div>';
      return;
    }

    els.status.textContent = `Найдено объявлений: ${data.total}`;
    els.list.innerHTML = data.items.map(cardHtml).join("");

    const totalPages = Math.max(1, Math.ceil(data.total / state.pageSize));
    els.pager.style.display = totalPages > 1 ? "flex" : "none";
    els.pageLabel.textContent = `${state.page} / ${totalPages}`;
    els.prevPage.disabled = state.page <= 1;
    els.nextPage.disabled = state.page >= totalPages;
  } catch (e) {
    els.status.textContent = "";
    els.list.innerHTML = '<div class="error-state"><span class="big-emoji">⚠️</span>Не удалось загрузить объявления. Попробуйте обновить страницу.</div>';
  }
}

[els.brand, els.city, els.sort].forEach((el) => el.addEventListener("change", () => { state.page = 1; loadListings(); }));
els.priceMax.addEventListener("change", () => { state.page = 1; loadListings(); });
els.prevPage.addEventListener("click", () => { state.page -= 1; loadListings(); });
els.nextPage.addEventListener("click", () => { state.page += 1; loadListings(); });

async function loadStats() {
  try {
    const data = await apiGet("/v1/stats");
    const heroTagline = document.getElementById("heroTagline");
    if (heroTagline && data.total) {
      heroTagline.textContent = `${data.total.toLocaleString("ru-RU")} объявлений · обновляется каждые 6 часов`;
    }
  } catch (e) {
    // не критично, дефолтный текст в шапке уже есть
  }
}

loadStats();
loadFacets();
loadListings();
