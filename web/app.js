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
  const badge = dealBadge(item.deal_score);
  const flag = (item.flags || [])[0];
  const photo = item.photo_url
    ? `<img src="${item.photo_url}" alt="" loading="lazy" />`
    : "фото по ссылке";

  return `
    <a class="card" href="listing.html?id=${encodeURIComponent(item.id)}">
      <div class="card-photo">${photo}</div>
      <div class="card-body">
        <p class="card-title">${item.title || "Без названия"}</p>
        <p class="card-meta">${item.city || "Город не указан"} · ${timeAgo(item.posted_at)}</p>
        <p class="card-price">${formatPrice(item.price_usd, item.price_uzs)}</p>
        <div class="row">
          <span class="deal-badge ${badge.cls}">${badge.text}</span>
          <span class="source-tag">${SOURCE_LABEL[item.source] || item.source}</span>
          ${flag ? `<span class="flag-tag">${flag.label}</span>` : ""}
        </div>
      </div>
    </a>
  `;
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
  els.status.textContent = "Загрузка…";
  els.list.innerHTML = "";
  els.pager.style.display = "none";

  try {
    const data = await apiGet(`/v1/listings?${buildQuery()}`);
    state.total = data.total;

    if (data.items.length === 0) {
      els.status.textContent = "";
      els.list.innerHTML = '<div class="empty-state">Ничего не найдено по этим фильтрам</div>';
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
    els.list.innerHTML = '<div class="error-state">Не удалось загрузить объявления. Попробуйте обновить страницу.</div>';
  }
}

[els.brand, els.city, els.sort].forEach((el) => el.addEventListener("change", () => { state.page = 1; loadListings(); }));
els.priceMax.addEventListener("change", () => { state.page = 1; loadListings(); });
els.prevPage.addEventListener("click", () => { state.page -= 1; loadListings(); });
els.nextPage.addEventListener("click", () => { state.page += 1; loadListings(); });

loadFacets();
loadListings();
