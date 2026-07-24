const root = document.querySelector("#app");
const toast = document.querySelector("#toast");

const state = {
  token: sessionStorage.getItem("core.token"),
  user: null,
  sessions: [],
  session: null,
  categories: [],
  suppliers: [],
  products: [],
  variants: [],
  itemDisplay: new Map(),
  imageUrls: new Map(),
  mode: null,
  result: null,
};

const requirementLabels = {
  missing_supplier: "Выберите поставщика",
  missing_items: "Добавьте хотя бы одну позицию",
  incomplete_items: "Заполните позиции",
  missing_image: "Нужно фото",
  missing_variant: "Товар недоступен",
  missing_product: "Выберите товар",
  missing_category: "Выберите категорию",
  missing_product_title: "Введите название товара",
  missing_variant_title: "Введите вариант",
  missing_quantity: "Укажите количество",
  missing_purchase_price: "Укажите закупочную цену",
  inactive_variant: "Вариант неактивен",
  missing_primary_image: "Нет основного фото",
  missing_sku: "Нет SKU",
  missing_barcode: "Нет штрихкода",
  invalid_barcode: "Некорректный штрихкод",
  missing_retail_price: "Укажите розничную цену",
};

const activityLabels = {
  intake_session_started: "Начата приёмка",
  intake_item_added: "Добавлена позиция",
  intake_item_abandoned: "Позиция отменена",
  intake_session_completed: "Приёмка завершена",
  intake_session_abandoned: "Приёмка отменена",
};

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function showToast(message, error = false) {
  toast.textContent = message;
  toast.className = `toast show${error ? " error" : ""}`;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { toast.className = "toast"; }, 3200);
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  if (options.body && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401 && path !== "/api/auth/login") {
    logout();
    throw new Error("Сессия закончилась. Войдите снова.");
  }
  if (!response.ok) {
    let detail = `Ошибка ${response.status}`;
    try {
      const payload = await response.json();
      detail = typeof payload.detail === "string" ? payload.detail : detail;
    } catch { /* response is not JSON */ }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

function logout() {
  sessionStorage.removeItem("core.token");
  state.token = null;
  state.user = null;
  state.session = null;
  renderLogin();
}

function renderLogin() {
  root.innerHTML = `
    <section class="login">
      <form class="login-card" id="login-form">
        <div class="brand"><span class="brand-mark">C</span> Core</div>
        <h1>Всё начинается с&nbsp;товара.</h1>
        <p class="muted">Войдите, чтобы принять поставку с телефона.</p>
        <div class="field">
          <label for="email">Электронная почта</label>
          <input id="email" name="email" type="email" autocomplete="username" required>
        </div>
        <div class="field">
          <label for="password">Пароль</label>
          <input id="password" name="password" type="password" autocomplete="current-password" required>
        </div>
        <button class="button full" type="submit">Войти</button>
      </form>
    </section>`;
  document.querySelector("#login-form").addEventListener("submit", login);
}

async function login(event) {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span> Входим';
  const data = new FormData(event.currentTarget);
  const body = new URLSearchParams({ username: data.get("email"), password: data.get("password") });
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
    if (!response.ok) throw new Error("Неверная почта или пароль");
    const payload = await response.json();
    state.token = payload.access_token;
    sessionStorage.setItem("core.token", state.token);
    await bootstrap();
  } catch (error) {
    showToast(error.message, true);
    button.disabled = false;
    button.textContent = "Войти";
  }
}

async function bootstrap() {
  try {
    state.user = await api("/api/auth/me");
    await loadHome();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function loadHome() {
  state.session = null;
  state.result = null;
  const [sessions, activity] = await Promise.all([
    api("/api/intake/sessions?session_status=draft"),
    api("/api/activity/me?limit=5&offset=0"),
  ]);
  state.sessions = sessions;
  renderHome(activity.items);
}

function renderHome(activity) {
  const drafts = state.sessions.length
    ? state.sessions.map((session) => `
        <button class="session-row" data-resume="${session.id}">
          <span><strong>Приёмка</strong><br><span class="muted small">${session.items.length} поз. · ${formatDate(session.updated_at)}</span></span>
          <span aria-hidden="true">→</span>
        </button>`).join("")
    : '<div class="empty">Незавершённых приёмок нет</div>';
  const feed = activity.length
    ? activity.map((event) => `<div class="session-row"><span>${escapeHtml(activityLabels[event.event_type] || event.event_type)}</span><span class="muted small">${formatDate(event.occurred_at)}</span></div>`).join("")
    : '<p class="muted small">Действий пока нет.</p>';
  root.innerHTML = `
    <div class="shell">
      ${topbar()}
      <p class="eyebrow">Рабочий режим</p>
      <h1>Приёмка</h1>
      <p class="muted">Сначала определяем товар. Поставщика и цены добавим после.</p>
      <button class="button full" id="start-session">＋ Начать приёмку</button>
      <h2 style="margin-top:28px">Продолжить</h2>
      <div class="session-list">${drafts}</div>
      <h2 style="margin-top:28px">Мои последние действия</h2>
      <div class="session-list">${feed}</div>
    </div>`;
  bindTopbar();
  document.querySelector("#start-session").addEventListener("click", startSession);
  document.querySelectorAll("[data-resume]").forEach((button) => {
    button.addEventListener("click", () => openSession(button.dataset.resume));
  });
}

function topbar(back = false) {
  return `<header class="topbar">
    <div class="brand"><span class="brand-mark">C</span> Core</div>
    <div class="topbar-actions">
      ${back ? '<button class="button ghost" id="back-home">← Назад</button>' : ""}
      <button class="button ghost" id="logout">Выйти</button>
    </div>
  </header>`;
}

function bindTopbar() {
  document.querySelector("#logout")?.addEventListener("click", logout);
  document.querySelector("#back-home")?.addEventListener("click", async () => {
    try {
      await saveAllItemForms();
      await loadHome();
    } catch (error) { showToast(error.message, true); }
  });
}

async function startSession() {
  try {
    const session = await api("/api/intake/sessions", { method: "POST" });
    await openSession(session.id);
  } catch (error) { showToast(error.message, true); }
}

async function loadReferences() {
  if (state.categories.length) return;
  [state.categories, state.suppliers, state.products, state.variants] = await Promise.all([
    api("/api/catalog/categories"),
    api("/api/purchasing/suppliers"),
    api("/api/catalog/products"),
    api("/api/catalog/variants"),
  ]);
}

async function openSession(id) {
  try {
    await loadReferences();
    state.session = await api(`/api/intake/sessions/${id}`);
    state.mode = null;
    state.result = null;
    await loadItemDisplays();
    renderWorkspace();
  } catch (error) { showToast(error.message, true); }
}

async function refreshSession() {
  state.session = await api(`/api/intake/sessions/${state.session.id}`);
  await loadItemDisplays();
  renderWorkspace();
}

async function loadItemDisplays() {
  state.itemDisplay.clear();
  await Promise.all(state.session.items.map(async (item) => {
    if (item.kind !== "existing_variant") return;
    try {
      const variant = await api(`/api/catalog/variants/${item.variant_id}`);
      const product = await api(`/api/catalog/products/${variant.product_id}`);
      let imageId = null;
      try {
        const image = await api(`/api/media/image-links/primary/catalog_variant/${variant.id}`);
        imageId = image.id;
      } catch {
        try {
          const image = await api(`/api/media/image-links/primary/catalog_product/${product.id}`);
          imageId = image.id;
        } catch { /* no primary photo */ }
      }
      state.itemDisplay.set(item.id, { variant, product, imageId });
    } catch { /* unavailable references are already represented by requirements */ }
  }));
}

function renderWorkspace() {
  if (state.result) return renderResult();
  const activeItems = state.session.items.filter((item) => !item.abandoned_at);
  root.innerHTML = `
    <div class="shell">
      ${topbar(true)}
      <p class="eyebrow">Черновик сохраняется по шагам</p>
      <h1>Что приехало?</h1>
      <p class="muted">Отсканируйте знакомый товар или сразу сфотографируйте новый.</p>
      <div class="actions">
        <button class="action-card" id="known-action"><span class="action-icon">▦</span><strong>Сканировать или найти</strong><span class="muted small">Повторная поставка</span></button>
        <button class="action-card" id="photo-action"><span class="action-icon">◉</span><strong>Сфотографировать</strong><span class="muted small">Новый товар</span></button>
      </div>
      ${renderActionPanel()}
      <h2>Позиции · ${activeItems.length}</h2>
      <div>${activeItems.length ? activeItems.map(renderItem).join("") : '<div class="empty">Добавьте первый товар</div>'}</div>
      ${renderSessionFinish()}
    </div>`;
  bindTopbar();
  document.querySelector("#known-action").addEventListener("click", async () => {
    try {
      await saveAllItemForms();
      state.mode = "known";
      renderWorkspace();
    } catch (error) { showToast(error.message, true); }
  });
  document.querySelector("#photo-action").addEventListener("click", () => {
    document.querySelector("#photo-input").click();
  });
  document.querySelector("#photo-input")?.addEventListener("change", uploadNewPhoto);
  document.querySelector("#known-form")?.addEventListener("submit", addKnownItem);
  document.querySelectorAll("[data-item-form]").forEach((form) => form.addEventListener("submit", saveItem));
  document.querySelector("#supplier")?.addEventListener("change", saveSupplier);
  document.querySelector("#complete-session")?.addEventListener("click", completeSession);
  hydrateImages();
}

function renderActionPanel() {
  const options = state.variants.map((variant) => {
    const product = state.products.find((item) => item.id === variant.product_id);
    return `<option value="${escapeHtml(variant.barcode)}">${escapeHtml(product?.title || "Товар")} · ${escapeHtml(variant.title)} · ${escapeHtml(variant.sku)}</option>`;
  }).join("");
  return `
    <input class="hidden" id="photo-input" type="file" accept="image/*" capture="environment">
    <section class="card ${state.mode === "known" ? "" : "hidden"}">
      <h2>Найти товар</h2>
      <p class="muted small">Сканер введёт штрихкод сам. Можно также выбрать товар из подсказок.</p>
      <form id="known-form">
        <div class="field"><label for="barcode">Штрихкод, SKU или название</label><input id="barcode" name="query" list="variant-options" autocomplete="off" required autofocus><datalist id="variant-options">${options}</datalist></div>
        <div class="field-row">
          <div class="field"><label for="known-quantity">Количество</label><input id="known-quantity" name="quantity" type="number" inputmode="numeric" min="1" required></div>
          <div class="field"><label for="known-price">Закупочная цена, ₽</label><input id="known-price" name="purchase_price" type="number" inputmode="decimal" min="0" step="0.01" required></div>
        </div>
        <div class="rental-allocation">
          <div class="field"><label for="known-rental-quantity">Из них в аренду, шт.</label><input id="known-rental-quantity" name="rental_quantity" type="number" inputmode="numeric" min="0" value="0"></div>
          <p class="muted small">Оставьте 0, если вся партия предназначена для продажи.</p>
        </div>
        <button class="button full" type="submit">Добавить позицию</button>
      </form>
    </section>`;
}

function renderItem(item) {
  const display = state.itemDisplay.get(item.id);
  const isExisting = item.kind === "existing_variant";
  const title = isExisting ? display?.product?.title || "Существующий товар" : item.product_title || "Новый товар";
  const subtitle = isExisting ? display?.variant?.title || "Вариант" : item.variant_title || "Заполните карточку";
  const imageId = isExisting ? display?.imageId : item.image_id;
  const requirements = item.missing_requirements.length
    ? item.missing_requirements.map((value) => `<span class="chip warn">${escapeHtml(requirementLabels[value] || value)}</span>`).join("")
    : '<span class="chip good">Позиция заполнена</span>';
  return `
    <article class="card">
      <div class="item">
        ${imageId ? `<img class="item-photo" data-image-id="${imageId}" alt="${escapeHtml(title)}">` : '<div class="photo-placeholder">◎</div>'}
        <div class="item-main">
          <h3 class="item-title">${escapeHtml(title)}</h3>
          <div class="muted small">${escapeHtml(subtitle)}${display?.variant ? ` · ${escapeHtml(display.variant.sku)}` : ""}</div>
          ${item.rental_quantity ? `<div class="rental-summary">В аренду: ${item.rental_quantity} шт.</div>` : ""}
          <div class="chips">${requirements}</div>
        </div>
      </div>
      <form class="drawer" data-item-form="${item.id}">
        ${isExisting ? "" : renderNewItemFields(item)}
        <div class="field-row">
          <div class="field"><label>Количество</label><input name="quantity" type="number" inputmode="numeric" min="1" value="${item.quantity ?? ""}" required></div>
          <div class="field"><label>Закупочная цена, ₽</label><input name="purchase_price" type="number" inputmode="decimal" min="0" step="0.01" value="${item.purchase_price ?? ""}" required></div>
        </div>
        <div class="rental-allocation">
          <div class="field"><label>Из них в аренду, шт.</label><input name="rental_quantity" type="number" inputmode="numeric" min="0" ${item.quantity === null ? "" : `max="${item.quantity}"`} value="${item.rental_quantity ?? 0}"></div>
          <p class="muted small">Каждая единица получит собственный номер RentalAsset.</p>
        </div>
        <button class="button secondary full" type="submit">Сохранить позицию</button>
      </form>
    </article>`;
}

function renderNewItemFields(item) {
  const categories = state.categories.map((category) => `<option value="${category.id}" ${item.category_id === category.id ? "selected" : ""}>${escapeHtml(category.title)}</option>`).join("");
  return `
    <div class="field"><label>Категория</label><select name="category_id" required><option value="">Выберите категорию</option>${categories}</select></div>
    <div class="field"><label>Название товара</label><input name="product_title" value="${escapeHtml(item.product_title || "")}" required></div>
    <div class="field"><label>Вариант — цвет, размер или исполнение</label><input name="variant_title" value="${escapeHtml(item.variant_title || "")}" required></div>
    <div class="field"><label>Описание <span class="muted">(необязательно)</span></label><textarea name="product_description">${escapeHtml(item.product_description || "")}</textarea></div>`;
}

function renderSessionFinish() {
  const supplierOptions = state.suppliers.map((supplier) => `<option value="${supplier.id}" ${state.session.supplier_id === supplier.id ? "selected" : ""}>${escapeHtml(supplier.display_name || supplier.name)}</option>`).join("");
  const missing = state.session.missing_requirements.map((value) => `<span class="chip warn">${escapeHtml(requirementLabels[value] || value)}</span>`).join("");
  return `<section class="card">
    <h2>Завершение</h2>
    <div class="field"><label for="supplier">Поставщик</label><select id="supplier"><option value="">Выберите после товаров</option>${supplierOptions}</select></div>
    <div class="chips">${missing || '<span class="chip good">Всё готово</span>'}</div>
    <button class="button full" id="complete-session" style="margin-top:16px" ${state.session.missing_requirements.length ? "disabled" : ""}>Провести приёмку</button>
  </section>`;
}

async function uploadNewPhoto(event) {
  const file = event.target.files[0];
  if (!file) return;
  const data = new FormData();
  data.append("file", file);
  showToast("Сохраняем фото…");
  try {
    await saveAllItemForms();
    await api(`/api/intake/sessions/${state.session.id}/items/new`, { method: "POST", body: data });
    state.mode = null;
    await refreshSession();
    showToast("Фото сохранено. Теперь заполните товар.");
  } catch (error) { showToast(error.message, true); }
}

async function addKnownItem(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  const query = String(data.get("query")).trim();
  const normalized = query.toLocaleLowerCase("ru");
  const variant = state.variants.find((item) => {
    const product = state.products.find((value) => value.id === item.product_id);
    return item.barcode === query || item.sku.toLocaleLowerCase("ru") === normalized || `${product?.title || ""} ${item.title}`.toLocaleLowerCase("ru") === normalized;
  });
  const payload = {
    ...(variant ? { variant_id: variant.id } : { barcode: query }),
    quantity: Number(data.get("quantity")),
    rental_quantity: Number(data.get("rental_quantity") || 0),
    purchase_price: String(data.get("purchase_price")),
  };
  try {
    await api(`/api/intake/sessions/${state.session.id}/items/existing`, { method: "POST", body: JSON.stringify(payload) });
    state.mode = null;
    await refreshSession();
    showToast("Товар найден и добавлен");
  } catch (error) { showToast(error.message, true); }
}

async function saveItem(event) {
  event.preventDefault();
  try {
    await persistItemForm(event.currentTarget);
    await refreshSession();
    showToast("Позиция сохранена");
  } catch (error) { showToast(error.message, true); }
}

function nullableText(value) {
  const normalized = String(value ?? "").trim();
  return normalized || null;
}

function buildItemPayload(form, item) {
  const data = new FormData(form);
  const quantity = nullableText(data.get("quantity"));
  const rentalQuantity = nullableText(data.get("rental_quantity"));
  const purchasePrice = nullableText(data.get("purchase_price"));
  const payload = {
    quantity: quantity === null ? null : Number(quantity),
    rental_quantity: rentalQuantity === null ? 0 : Number(rentalQuantity),
    purchase_price: purchasePrice,
  };
  if (item.kind !== "existing_variant") {
    Object.assign(payload, {
      category_id: nullableText(data.get("category_id")),
      product_title: nullableText(data.get("product_title")),
      variant_title: nullableText(data.get("variant_title")),
      product_description: nullableText(data.get("product_description")),
    });
  }
  return payload;
}

async function persistItemForm(form) {
  const item = state.session.items.find((value) => value.id === form.dataset.itemForm);
  if (!item) return;
  const updated = await api(`/api/intake/sessions/${state.session.id}/items/${item.id}`, {
    method: "PATCH",
    body: JSON.stringify(buildItemPayload(form, item)),
  });
  state.session.items = state.session.items.map((value) => value.id === updated.id ? updated : value);
}

async function saveAllItemForms() {
  if (!state.session || state.session.status !== "draft") return;
  const forms = [...document.querySelectorAll("[data-item-form]")];
  await Promise.all(forms.map(persistItemForm));
}

async function saveSupplier(event) {
  const supplierId = event.target.value || null;
  try {
    await saveAllItemForms();
    state.session = await api(`/api/intake/sessions/${state.session.id}`, {
      method: "PATCH",
      body: JSON.stringify({ supplier_id: supplierId }),
    });
    renderWorkspace();
  } catch (error) { showToast(error.message, true); }
}

async function completeSession() {
  const button = document.querySelector("#complete-session");
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span> Проводим';
  try {
    await saveAllItemForms();
    state.result = await api(`/api/intake/sessions/${state.session.id}/complete`, { method: "POST" });
    renderResult();
  } catch (error) {
    showToast(error.message, true);
    button.disabled = false;
    button.textContent = "Провести приёмку";
  }
}

function renderResult() {
  const readyCount = state.result.readiness.filter((item) => item.is_ready).length;
  const pendingItems = state.result.readiness.filter((item) => !item.is_ready);
  const pending = pendingItems.length;
  const attention = pendingItems.map((readiness) => {
    const mapping = state.result.items.find((item) => item.variant_id === readiness.variant_id);
    const source = state.session.items.find((item) => item.id === mapping?.item_id);
    const display = source ? state.itemDisplay.get(source.id) : null;
    const title = source?.product_title || display?.product?.title || "Товар";
    const variant = source?.variant_title || display?.variant?.title || "";
    const reasons = readiness.missing_requirements.map((value) => `<span class="chip warn">${escapeHtml(requirementLabels[value] || value)}</span>`).join("");
    return `<div class="attention-row"><strong>${escapeHtml(title)}${variant ? ` · ${escapeHtml(variant)}` : ""}</strong><div class="chips">${reasons}</div></div>`;
  }).join("");
  root.innerHTML = `<div class="shell">
    ${topbar()}
    <div class="result" style="margin-top:36px">
      <div class="result-mark">✓</div>
      <h1>Товар принят</h1>
      <p>Приход <strong>${escapeHtml(state.result.receipt.number)}</strong> проведён. Остатки обновлены.</p>
      <div class="chips" style="justify-content:center">
        <span class="chip good">Готово к продаже: ${readyCount}</span>
        ${pending ? `<span class="chip warn">Требует внимания: ${pending}</span>` : ""}
      </div>
      ${attention ? `<div class="attention-list"><h3>Что нужно сделать дальше</h3>${attention}</div>` : ""}
      <button class="button full" id="finish-home" style="margin-top:20px">Готово</button>
    </div>
  </div>`;
  bindTopbar();
  document.querySelector("#finish-home").addEventListener("click", loadHome);
}

async function hydrateImages() {
  await Promise.all([...document.querySelectorAll("[data-image-id]")].map(async (element) => {
    const id = element.dataset.imageId;
    try {
      let url = state.imageUrls.get(id);
      if (!url) {
        const response = await fetch(`/api/media/images/${id}/source`, { headers: { Authorization: `Bearer ${state.token}` } });
        if (!response.ok) return;
        url = URL.createObjectURL(await response.blob());
        state.imageUrls.set(id, url);
      }
      element.src = url;
    } catch { /* a missing preview must not block intake */ }
  }));
}

function formatDate(value) {
  return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

if (state.token) bootstrap(); else renderLogin();
