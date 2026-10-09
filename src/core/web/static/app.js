const root = document.querySelector("#app");
const toast = document.querySelector("#toast");
let logicalParent = () => loadHome();
let restoringHistory = false;
let zxingLoader = null;
let intakeSearchTimer = null;
let scannerService = null;
let salesProgressTimer = null;

function recordRoute(name, data = {}) {
  if (name !== "intake") scannerService?.clearLocalHandler();
  const route = { name, ...data };
  if (restoringHistory) return;
  if (window.history.state?.coreRoute?.name === name
      && JSON.stringify(window.history.state.coreRoute) === JSON.stringify(route)) return;
  const method = window.history.state?.coreRoute ? "pushState" : "replaceState";
  window.history[method]({ coreRoute: route }, "", routeUrl(route));
}

function routeUrl(route) {
  if (route.name === "product") return `${window.location.pathname}#product/${route.productId}`;
  if (route.name === "customer") return `${window.location.pathname}#customer/${route.customerId}`;
  if (route.name === "order") return `${window.location.pathname}#order/${route.orderId}`;
  if (route.name === "intake") return `${window.location.pathname}#intake/${route.sessionId}`;
  if (route.name === "sale") return `${window.location.pathname}#sale${route.saleId ? `/${route.saleId}` : ""}`;
  if (route.name === "catalog") {
    const params = catalogUrlParams(normalizeCatalogState(route), false);
    const query = params.toString();
    return `${window.location.pathname}#catalog${query ? `?${query}` : ""}`;
  }
  return `${window.location.pathname}#${route.name}`;
}

function routeFromLocation() {
  const [routePath, queryString = ""] = window.location.hash.slice(1).split("?");
  const [name, id] = routePath.split("/");
  if (name === "product" && id) return { name, productId: id };
  if (name === "customer" && id) return { name, customerId: id };
  if (name === "order" && id) return { name, orderId: id };
  if (name === "intake" && id) return { name, sessionId: id };
  if (name === "sale") return { name, saleId: id || undefined };
  if (name === "catalog") {
    const params = new URLSearchParams(queryString);
    return {
      name,
      mode: params.get("mode") || undefined,
      status: params.get("status") || undefined,
      query: params.get("query") || undefined,
      categoryId: params.get("category_id") || undefined,
      supplierId: params.get("supplier_id") || undefined,
      attention: params.getAll("attention"),
      productFilter: params.get("product_filter") || undefined,
      sort: params.get("sort") || undefined,
    };
  }
  if (["workspace", "rental", "settings"].includes(name)) return { name };
  return null;
}

async function restoreRoute(route) {
  restoringHistory = true;
  try {
    await flushProductAutosaves();
    if (!route || route.name === "workspace") await loadHome();
    else if (route.name === "catalog") await openOperationsCatalog(route);
    else if (route.name === "product") await openOperationsProduct(route.productId);
    else if (route.name === "rental") await openRentalHub();
    else if (route.name === "settings") await openSettings();
    else if (route.name === "sale") await openSalesWorkspace(route.saleId);
    else if (route.name === "customer") await selectRentalCustomer(route.customerId);
    else if (route.name === "order") await openRentalReturnOrder(route.orderId);
    else if (route.name === "intake") await openSession(route.sessionId);
    else await loadHome();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    restoringHistory = false;
  }
}

window.addEventListener("popstate", (event) => restoreRoute(event.state?.coreRoute));

const state = {
  token: sessionStorage.getItem("core.token"),
  user: null,
  sessions: [],
  session: null,
  categories: [],
  suppliers: [],
  referencesLoaded: false,
  products: [],
  variants: [],
  itemDisplay: new Map(),
  imageUrls: new Map(),
  printCapability: { available: false, printer_name: null, fallback: "pdf" },
  mode: null,
  result: null,
  intakeBarcode: {
    value: "",
    result: null,
    unknown: false,
  },
  intakeVariantSearch: { query: "", items: [], hasMore: false, loading: false, selected: null },
  intakeProductSearch: { query: "", items: [], hasMore: false, loading: false, selected: null },
  intakeAqsi: new Map(),
  sales: {
    drafts: [],
    activeId: null,
    active: null,
  },
  rental: {
    customers: [],
    customer: null,
    customerHistory: null,
    drafts: [],
    order: null,
    assets: [],
    returnAssets: new Map(),
  },
  operations: {
    catalog: null,
    categoryManagement: null,
    products: [],
    product: null,
    imageLinks: [],
    assets: [],
    asset: null,
    aqsi: new Map(),
  },
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

function activityDetail(event) {
  if (event.event_type === "intake_session_completed") {
    return `${event.data.item_count ?? 0} поз. · ${event.data.total_quantity ?? 0} шт.`;
  }
  if (event.event_type === "intake_item_added") return "Товар добавлен в приёмку";
  return "";
}

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function visibleVariantTitle(value, fallback = "") {
  return ["default", "default variant", "основной"].includes(String(value || "").trim().toLocaleLowerCase("ru")) ? fallback : (value || fallback);
}

function showToast(message, error = false) {
  const text = message instanceof Event ? "Действие не удалось. Повторите ещё раз." : String(message ?? "");
  toast.textContent = text;
  toast.className = `toast show${error ? " error" : ""}`;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { toast.className = "toast"; }, 3200);
}

class ScannerService {
  /** Route likely HID scanner sequences without treating ordinary typing as scans. */
  constructor(target = document) {
    this.target = target;
    this.buffer = "";
    this.startedAt = 0;
    this.lastAt = 0;
    this.fastMaxGap = 35;
    this.bluetoothMaxGap = 180;
    this.bluetoothAverageGap = 100;
    this.minLength = 4;
    this.bluetoothMinLength = 8;
    this.localHandler = null;
    this.globalHandler = null;
    this.onKeyDown = this.onKeyDown.bind(this);
    target.addEventListener("keydown", this.onKeyDown, true);
  }

  setLocalHandler(owner, handler) {
    this.localHandler = { owner, handler };
  }

  clearLocalHandler(owner = null) {
    if (!owner || this.localHandler?.owner === owner) this.localHandler = null;
  }

  setGlobalHandler(handler) {
    this.globalHandler = handler;
  }

  reset() {
    this.buffer = "";
    this.startedAt = 0;
    this.lastAt = 0;
  }

  isEditableTarget(target) {
    if (!target?.closest) return false;
    return Boolean(target.closest("input, textarea, select, [contenteditable='true']"));
  }

  onKeyDown(event) {
    if (event.isComposing || event.ctrlKey || event.altKey || event.metaKey) {
      this.reset();
      return;
    }
    if (this.isEditableTarget(event.target) || document.querySelector("dialog[open]")) {
      this.reset();
      return;
    }
    const now = Number(event.timeStamp || performance.now());
    if (event.key === "Enter" || event.key === "Tab") {
      const elapsed = this.lastAt && this.startedAt ? this.lastAt - this.startedAt : Infinity;
      const terminatorGap = this.lastAt ? now - this.lastAt : Infinity;
      const fastScanner = this.buffer.length >= this.minLength
        && elapsed <= Math.max(90, (this.buffer.length - 1) * this.fastMaxGap)
        && terminatorGap <= this.fastMaxGap * 2;
      const bluetoothScanner = this.buffer.length >= this.bluetoothMinLength
        && elapsed <= (this.buffer.length - 1) * this.bluetoothAverageGap
        && terminatorGap <= this.bluetoothMaxGap * 2;
      const scannerLike = fastScanner || bluetoothScanner;
      const value = this.buffer;
      this.reset();
      if (!scannerLike) return;
      event.preventDefault();
      event.stopPropagation();
      this.emit(value);
      return;
    }
    if (event.key.length !== 1 || event.key < " " || event.key > "~") {
      this.reset();
      return;
    }
    if (this.lastAt && now - this.lastAt > this.bluetoothMaxGap) this.reset();
    if (!this.buffer) this.startedAt = now;
    this.buffer += event.key;
    this.lastAt = now;
  }

  emit(value) {
    if (this.localHandler?.handler(value) === true) return "local";
    if (this.globalHandler?.(value) === true) return "global";
    return null;
  }
}

function ensureScannerService() {
  if (scannerService) return scannerService;
  scannerService = new ScannerService(document);
  scannerService.setGlobalHandler((value) => {
    void addBarcodeToActiveSale(value);
    return true;
  });
  return scannerService;
}

function activeSaleStorageKey() {
  return state.user ? `core.activeSale.${state.user.id}` : null;
}

function storedActiveSaleId() {
  const key = activeSaleStorageKey();
  return key ? window.localStorage?.getItem(key) || null : null;
}

function storeActiveSaleId(saleId) {
  const key = activeSaleStorageKey();
  if (!key || !window.localStorage) return;
  if (saleId) window.localStorage.setItem(key, saleId);
  else window.localStorage.removeItem(key);
}

async function refreshSalesContext(preferredId = undefined) {
  state.sales.drafts = await api("/api/sales");
  const candidate = preferredId === undefined ? storedActiveSaleId() : preferredId;
  const active = state.sales.drafts.find((sale) => sale.id === candidate) || null;
  state.sales.activeId = active?.id || null;
  state.sales.active = active;
  storeActiveSaleId(state.sales.activeId);
  return active;
}

function rememberSale(sale, makeActive = true) {
  state.sales.drafts = [sale, ...state.sales.drafts.filter((item) => item.id !== sale.id)]
    .filter((item) => item.status === "draft");
  if (makeActive) {
    state.sales.activeId = sale.status === "draft" ? sale.id : null;
    state.sales.active = sale.status === "draft" ? sale : null;
    storeActiveSaleId(state.sales.activeId);
  }
  updateSalesIndicator();
}

function selectActiveSale(saleId) {
  const sale = state.sales.drafts.find((item) => item.id === saleId) || null;
  state.sales.activeId = sale?.id || null;
  state.sales.active = sale;
  storeActiveSaleId(state.sales.activeId);
  updateSalesIndicator();
  return sale;
}

function renderSalesIndicator() {
  const sale = state.sales.active;
  const summary = sale
    ? `<strong>🛒 Продажа #${sale.sale_number}</strong><span>${sale.item_quantity} поз. · ${formatMoney(sale.total_amount)}</span>`
    : "<strong>🛒 Продажа</strong><span>Корзина пуста</span>";
  return `<button class="sales-indicator" id="sales-indicator" type="button" aria-label="Открыть продажи">${summary}</button>`;
}

function updateSalesIndicator() {
  const current = document.querySelector("#sales-indicator");
  if (current) {
    const wrapper = document.createElement("div");
    wrapper.innerHTML = renderSalesIndicator();
    current.replaceWith(wrapper.firstElementChild);
    bindSalesIndicator();
  }
  const catalogContext = document.querySelector("#catalog-sales-context");
  if (catalogContext) {
    const wrapper = document.createElement("div");
    wrapper.innerHTML = renderCatalogSalesContext(state.operations.catalog);
    catalogContext.replaceWith(wrapper.firstElementChild);
    bindCatalogSalesContext();
  }
}

function bindSalesIndicator() {
  document.querySelector("#sales-indicator")?.addEventListener("click", () => {
    openSalesWorkspace(state.sales.activeId);
  });
}

function renderCatalogSalesContext(catalog) {
  const sale = state.sales.active;
  const visible = sale && ["sale", "all"].includes(catalog?.mode);
  return `<section id="catalog-sales-context" class="catalog-sales-context ${visible ? "" : "hidden"}">
    ${visible ? `<span><strong>Продажа #${sale.sale_number}</strong><small>${sale.item_quantity} поз. · ${formatMoney(sale.total_amount)}</small></span><button class="button ghost compact" id="catalog-open-sale" type="button">Перейти к продаже</button>` : ""}
  </section>`;
}

function bindCatalogSalesContext() {
  document.querySelector("#catalog-open-sale")?.addEventListener("click", () => openSalesWorkspace(state.sales.activeId));
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
    let responseDetail = null;
    try {
      const payload = await response.json();
      responseDetail = payload.detail;
      detail = typeof payload.detail === "string" ? payload.detail : detail;
    } catch { /* response is not JSON */ }
    const error = new Error(detail);
    error.status = response.status;
    error.detail = responseDetail;
    throw error;
  }
  if (response.status === 204) return null;
  return response.json();
}

async function openAuthenticatedFile(path, print = false) {
  const popup = window.open("", "_blank");
  try {
    const response = await fetch(path, {
      headers: state.token ? { Authorization: `Bearer ${state.token}` } : {},
    });
    if (!response.ok) throw new Error(`Не удалось открыть документ (${response.status})`);
    const url = URL.createObjectURL(await response.blob());
    if (popup) {
      popup.location = url;
      if (print) popup.addEventListener("load", () => popup.print(), { once: true });
    }
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (error) {
    popup?.close();
    showToast(error.message, true);
  }
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
    await refreshSalesContext();
    ensureScannerService();
    const directRoute = routeFromLocation();
    if (directRoute) {
      window.history.replaceState({ coreRoute: directRoute }, "", routeUrl(directRoute));
      await restoreRoute(directRoute);
    } else {
      await loadHome();
    }
  } catch (error) {
    showToast(error.message, true);
  }
}

async function loadHome() {
  recordRoute("workspace");
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
    ? activity.map((event) => `<div class="session-row"><span><strong>${escapeHtml(activityLabels[event.event_type] || "Действие")}</strong>${activityDetail(event) ? `<br><span class="muted small">${escapeHtml(activityDetail(event))}</span>` : ""}</span><span class="muted small">${formatDate(event.occurred_at)}</span></div>`).join("")
    : '<p class="muted small">Действий пока нет.</p>';
  root.innerHTML = `
    <div class="shell">
      ${topbar()}
      <p class="eyebrow">Рабочий режим</p>
      <h1>Рабочее место</h1>
      <p class="muted">Выберите операцию.</p>
      <div class="actions">
        <button class="action-card" id="open-intake"><span class="action-icon">＋</span><strong>Приёмка</strong><span class="muted small">Принять товар</span></button>
        <button class="action-card" id="open-catalog"><span class="action-icon">▦</span><strong>Каталог</strong><span class="muted small">Товары и варианты</span></button>
        <button class="action-card" id="open-rental"><span class="action-icon">↔</span><strong>Аренда</strong><span class="muted small">Активные, выдача и возврат</span></button>
        ${state.user?.is_admin || state.user?.is_superuser ? '<button class="action-card" id="open-settings"><span class="action-icon">⚙</span><strong>Настройки</strong><span class="muted small">Интеграции и доступы</span></button>' : ""}
      </div>
      <section id="intake-home" class="hidden">
      <p class="muted">Сначала определяем товар. Поставщика и цены добавим после.</p>
      <button class="button full" id="start-session">＋ Начать приёмку</button>
      <h2 style="margin-top:28px">Продолжить</h2>
      <div class="session-list">${drafts}</div>
      <h2 style="margin-top:28px">Мои последние действия</h2>
      <div class="session-list">${feed}</div>
      </section>
    </div>`;
  bindTopbar();
  document.querySelector("#open-intake").addEventListener("click", () => {
    document.querySelector("#intake-home").classList.remove("hidden");
    document.querySelector("#open-intake").scrollIntoView({ behavior: "smooth" });
  });
  document.querySelector("#open-catalog").addEventListener("click", () => openOperationsCatalog());
  document.querySelector("#open-rental").addEventListener("click", () => openRentalHub());
  document.querySelector("#open-settings")?.addEventListener("click", () => openSettings());
  document.querySelector("#start-session").addEventListener("click", () => startSession());
  document.querySelectorAll("[data-resume]").forEach((button) => {
    button.addEventListener("click", () => openSession(button.dataset.resume));
  });
}

async function openSettings() {
  recordRoute("settings");
  logicalParent = () => loadHome();
  try {
    const aqsi = await api("/api/settings/integrations/aqsi");
    renderSettings(aqsi);
  } catch (error) {
    showToast(error.message, true);
  }
}

function integrationStatusLabel(aqsi) {
  if (aqsi.using_legacy_environment) return "Устаревшая конфигурация";
  if (aqsi.status === "connected") return "Подключено";
  if (aqsi.status === "disabled") return "Выключено";
  return "Не подключено";
}

function aqsiTaxSystemOptions(selected) {
  const values = [[1, "ОСН"], [2, "УСН доход"], [4, "УСН доходы минус расходы"], [16, "Патент"], [32, "НПД"]];
  return '<option value="">Выберите систему</option>' + values.map(([value, label]) => `<option value="${value}" ${Number(selected) === value ? "selected" : ""}>${label}</option>`).join("");
}

function aqsiAcquiringModeOptions(selected = "sbp_with_card") {
  const values = [
    ["sbp_with_card", "Карта / QR"],
    ["card_only", "Только карта"],
    ["sbp_only", "Только QR"],
  ];
  return values.map(([value, label]) => `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`).join("");
}

function renderSettings(aqsi) {
  const integration = aqsi.integration;
  const configuration = integration?.configuration || {};
  const savedAt = integration?.credential_rotated_at || integration?.credential_saved_at;
  root.innerHTML = `<div class="shell settings-shell">
    ${topbar(true)}
    <p class="eyebrow">Настройки</p>
    <h1>Интеграции</h1>
    <p class="muted">Подключения внешних сервисов. Каталог и цены остаются под управлением Core.</p>
    ${aqsi.using_legacy_environment ? `<div class="settings-warning"><strong>Используются устаревшие настройки из окружения</strong><p class="muted small">Перенесите ключ в защищённые настройки после подготовки master key на сервере.</p><button class="button secondary" id="migrate-aqsi" type="button">Перенести в настройки</button></div>` : ""}
    <section class="card integration-card">
      <div class="section-heading"><div><p class="eyebrow">Интеграция</p><h2>AQSI</h2></div><span class="chip ${aqsi.status === "connected" ? "good" : aqsi.status === "not_connected" ? "warn" : ""}">${escapeHtml(integrationStatusLabel(aqsi))}</span></div>
      <p class="muted small">${configuration.catalog_sync_enabled ? "Core автоматически поддерживает каталог AQSI как внешнюю проекцию." : "Ручная проекция каталога Core в AQSI. Автоматическая синхронизация выключена."}</p>
      ${integration ? `<div class="settings-facts">
        <div><span class="muted small">API key</span><strong>${savedAt ? "••••••••••••••••••••" : "Не сохранён"}</strong>${savedAt ? `<span class="muted small">Сохранён: ${formatDate(savedAt)}</span>` : ""}</div>
        <label class="settings-toggle"><input id="aqsi-enabled" type="checkbox" ${integration.enabled ? "checked" : ""}> <span>Интеграция включена</span></label>
      </div>
      <div class="inline-actions">
        <button class="button secondary" id="replace-aqsi-key" type="button">${savedAt ? "Заменить ключ" : "Сохранить ключ"}</button>
        <button class="button ghost" id="test-aqsi" type="button" ${savedAt ? "" : "disabled"}>Проверить подключение</button>
      </div>
      <form id="aqsi-key-form" class="settings-secret-form hidden">
        <div class="field"><label for="aqsi-api-key">Новый API key</label><input id="aqsi-api-key" name="api_key" type="password" autocomplete="new-password" required></div>
        <div class="inline-actions"><button class="button" type="submit">Сохранить</button><button class="button ghost" id="cancel-aqsi-key" type="button">Отмена</button></div>
      </form>
      <div class="divider"></div>
      <div class="field"><label for="aqsi-shop">Магазин AQSI</label><select id="aqsi-shop"><option value="${escapeHtml(configuration.shop_id || "")}">${configuration.shop_id ? `AQSI ${escapeHtml(configuration.shop_id)}` : "Определять автоматически, если магазин один"}</option></select></div>
      <button class="button ghost" id="discover-aqsi-shops" type="button" ${savedAt ? "" : "disabled"}>Обновить список магазинов</button>
      <div class="divider"></div>
      <form id="aqsi-checkout-form" class="settings-secret-form">
        <strong>Оплата на кассе</strong>
        <p class="muted small">Необходима для запуска банковского терминала и печати фискального чека из Core.</p>
        <div class="field"><label for="aqsi-device-id">ID устройства AQSI</label><input id="aqsi-device-id" name="device_id" inputmode="numeric" value="${escapeHtml(configuration.device_id || "")}" required></div>
        <div class="field"><label for="aqsi-acquiring-mode">Режим безналичной оплаты</label><select id="aqsi-acquiring-mode" name="acquiring_mode">${aqsiAcquiringModeOptions(configuration.acquiring_mode || "sbp_with_card")}</select></div>
        <div class="field"><label for="aqsi-tax-system">Система налогообложения</label><select id="aqsi-tax-system" name="tax_system_code" required>${aqsiTaxSystemOptions(configuration.tax_system_code)}</select></div>
        <div class="field"><label for="aqsi-tax-code">Код ставки НДС AQSI</label><input id="aqsi-tax-code" name="tax_code" type="number" min="1" max="10" value="${escapeHtml(configuration.tax_code || "")}" required></div>
        <button class="button secondary" type="submit">Сохранить настройки оплаты</button>
      </form>
      <div class="divider"></div>
      <div class="settings-sync-control">
        <label class="settings-toggle"><input id="aqsi-auto-sync" type="checkbox" ${configuration.catalog_sync_enabled ? "checked" : ""} ${integration.enabled && savedAt ? "" : "disabled"}> <span>Автоматическая синхронизация каталога</span></label>
        <p class="muted small">Каждые 5 минут Core отправляет новые и изменившиеся готовые варианты. Архивирование в AQSI пока выполняется отдельно.</p>
        <button class="button secondary" id="sync-aqsi-now" type="button" ${integration.enabled && savedAt ? "" : "disabled"}>Синхронизировать сейчас</button>
      </div>` : `<form id="new-aqsi-form">
        <div class="field"><label for="new-aqsi-key">API key</label><input id="new-aqsi-key" name="api_key" type="password" autocomplete="new-password" required></div>
        <label class="settings-toggle"><input name="enabled" type="checkbox" checked> <span>Интеграция включена</span></label>
        <button class="button" type="submit">Подключить AQSI</button>
      </form>`}
    </section>
  </div>`;
  bindTopbar();
  document.querySelector("#migrate-aqsi")?.addEventListener("click", migrateLegacyAqsi);
  document.querySelector("#new-aqsi-form")?.addEventListener("submit", createAqsiIntegration);
  document.querySelector("#aqsi-enabled")?.addEventListener("change", updateAqsiEnabled);
  document.querySelector("#replace-aqsi-key")?.addEventListener("click", () => document.querySelector("#aqsi-key-form").classList.remove("hidden"));
  document.querySelector("#cancel-aqsi-key")?.addEventListener("click", () => document.querySelector("#aqsi-key-form").classList.add("hidden"));
  document.querySelector("#aqsi-key-form")?.addEventListener("submit", (event) => replaceAqsiKey(event, integration.id));
  document.querySelector("#test-aqsi")?.addEventListener("click", () => testAqsiConnection(integration.id));
  document.querySelector("#discover-aqsi-shops")?.addEventListener("click", () => discoverAqsiShops(integration));
  document.querySelector("#aqsi-shop")?.addEventListener("change", () => saveAqsiShop(integration));
  document.querySelector("#aqsi-checkout-form")?.addEventListener("submit", (event) => saveAqsiCheckout(event, integration));
  document.querySelector("#aqsi-auto-sync")?.addEventListener("change", (event) => updateAqsiAutoSync(event, integration));
  document.querySelector("#sync-aqsi-now")?.addEventListener("click", () => synchronizeAqsiNow(integration));
}

async function migrateLegacyAqsi() {
  try {
    await api("/api/settings/integrations/aqsi/migrate", { method: "POST" });
    showToast("Настройки AQSI перенесены");
    await openSettings();
  } catch (error) {
    showToast(error.message, true);
    await openSettings();
  }
}

async function createAqsiIntegration(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  try {
    const integration = await api("/api/settings/integrations", {
      method: "POST",
      body: JSON.stringify({ provider: "aqsi", name: "AQSI", enabled: data.get("enabled") === "on", configuration: {} }),
    });
    await api(`/api/settings/integrations/${integration.id}/credential`, {
      method: "PUT",
      body: JSON.stringify({ api_key: data.get("api_key") }),
    });
    showToast("AQSI подключена");
    await openSettings();
  } catch (error) {
    showToast(error.message, true);
    await openSettings();
  }
}

async function updateAqsiEnabled(event) {
  const aqsi = await api("/api/settings/integrations/aqsi");
  if (!aqsi.integration) return;
  try {
    await api(`/api/settings/integrations/${aqsi.integration.id}`, { method: "PATCH", body: JSON.stringify({ enabled: event.currentTarget.checked }) });
    showToast(event.currentTarget.checked ? "Интеграция включена" : "Интеграция выключена");
    await openSettings();
  } catch (error) {
    event.currentTarget.checked = !event.currentTarget.checked;
    showToast(error.message, true);
  }
}

async function replaceAqsiKey(event, integrationId) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  try {
    await api(`/api/settings/integrations/${integrationId}/credential`, { method: "PUT", body: JSON.stringify({ api_key: data.get("api_key") }) });
    event.currentTarget.reset();
    showToast("API key сохранён");
    await openSettings();
  } catch (error) { showToast(error.message, true); }
}

async function testAqsiConnection(integrationId) {
  try {
    const result = await api(`/api/settings/integrations/${integrationId}/test`, { method: "POST" });
    showToast(result.ok ? `✓ ${result.message}` : result.message, !result.ok);
  } catch (error) { showToast(error.message, true); }
}

async function discoverAqsiShops(integration) {
  try {
    const shops = await api(`/api/settings/integrations/${integration.id}/shops`);
    const select = document.querySelector("#aqsi-shop");
    select.innerHTML = '<option value="">Выберите магазин</option>' + shops.map((shop) => `<option value="${escapeHtml(shop.id)}" ${shop.id === integration.configuration.shop_id ? "selected" : ""}>${escapeHtml(shop.name)}</option>`).join("");
    showToast(shops.length ? "Список магазинов обновлён" : "Активные магазины не найдены");
  } catch (error) { showToast(error.message, true); }
}

async function saveAqsiShop(integration) {
  const shopId = document.querySelector("#aqsi-shop").value || null;
  try {
    await api(`/api/settings/integrations/${integration.id}`, {
      method: "PATCH",
      body: JSON.stringify({ configuration: { ...integration.configuration, shop_id: shopId } }),
    });
    showToast("Магазин AQSI сохранён");
  } catch (error) { showToast(error.message, true); }
}

async function saveAqsiCheckout(event, integration) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  const deviceId = String(data.get("device_id") || "").trim();
  const taxSystemCode = Number(data.get("tax_system_code"));
  const taxCode = Number(data.get("tax_code"));
  const acquiringMode = String(data.get("acquiring_mode") || "");
  if (!/^\d+$/.test(deviceId) || ![1, 2, 4, 16, 32].includes(taxSystemCode) || !Number.isInteger(taxCode) || taxCode < 1 || taxCode > 10 || !["card_only", "sbp_with_card", "sbp_only"].includes(acquiringMode)) {
    showToast("Проверьте ID устройства, систему налогообложения и ставку НДС", true);
    return;
  }
  try {
    await api(`/api/settings/integrations/${integration.id}`, {
      method: "PATCH",
      body: JSON.stringify({ configuration: { ...integration.configuration, device_id: deviceId, tax_system_code: taxSystemCode, tax_code: taxCode, acquiring_mode: acquiringMode } }),
    });
    showToast("Настройки оплаты сохранены");
    await openSettings();
  } catch (error) { showToast(error.message, true); }
}

async function updateAqsiAutoSync(event, integration) {
  const enabled = event.currentTarget.checked;
  try {
    await api(`/api/settings/integrations/${integration.id}`, {
      method: "PATCH",
      body: JSON.stringify({ configuration: { ...integration.configuration, catalog_sync_enabled: enabled } }),
    });
    if (enabled) {
      await api(`/api/settings/integrations/${integration.id}/sync`, { method: "POST" });
    }
    showToast(enabled ? "Автоматическая синхронизация включена" : "Автоматическая синхронизация выключена");
    await openSettings();
  } catch (error) {
    event.currentTarget.checked = !enabled;
    showToast(error.message, true);
  }
}

async function synchronizeAqsiNow(integration) {
  try {
    const result = await api(`/api/settings/integrations/${integration.id}/sync`, { method: "POST" });
    showToast(result.message);
  } catch (error) { showToast(error.message, true); }
}

function topbar(back = false) {
  return `<header class="topbar">
    <div class="brand"><span class="brand-mark">C</span> Core</div>
    ${renderSalesIndicator()}
    <div class="topbar-actions">
      ${back ? '<button class="button ghost" id="back-home">← Назад</button>' : ""}
      <button class="button ghost" id="logout">Выйти</button>
    </div>
  </header>`;
}

function bindTopbar() {
  bindSalesIndicator();
  document.querySelector("#logout")?.addEventListener("click", logout);
  document.querySelector("#back-home")?.addEventListener("click", async () => {
    try {
      await saveAllItemForms();
      await logicalParent();
    } catch (error) { showToast(error.message, true); }
  });
}

async function openSalesWorkspace(requestedSaleId = undefined) {
  try {
    const candidate = requestedSaleId === undefined ? state.sales.activeId : requestedSaleId;
    await refreshSalesContext(candidate || null);
    let sale = state.sales.active;
    if (!sale && requestedSaleId) sale = await api(`/api/sales/${requestedSaleId}`);
    recordRoute("sale", { saleId: sale?.id });
    logicalParent = () => loadHome();
    renderSalesWorkspace(sale);
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderSalesWorkspace(sale = state.sales.active) {
  const editable = sale?.status === "draft";
  const items = sale?.items.length
    ? sale.items.map((item) => `<article class="sale-item">
        <div class="sale-item-copy">
          <strong>${escapeHtml(item.display_label_snapshot)}${item.source === "manual" ? ' <span class="sale-item-source">Свободная</span>' : ""}</strong>
          <span class="muted small">${item.sku_snapshot ? `${escapeHtml(item.sku_snapshot)} · ` : ""}${formatMoney(item.unit_price)} × ${item.quantity}</span>
        </div>
        <strong class="sale-line-total">${formatMoney(item.line_total)}</strong>
        ${editable ? `<div class="sale-quantity" aria-label="Количество ${escapeHtml(item.display_label_snapshot)}">
          <button class="quantity-button" data-sale-quantity="-1" data-sale-item="${item.id}" type="button" aria-label="Уменьшить количество">−</button>
          <strong>${item.quantity}</strong>
          <button class="quantity-button" data-sale-quantity="1" data-sale-item="${item.id}" type="button" aria-label="Увеличить количество">＋</button>
          <button class="button ghost compact danger-text" data-remove-sale-item="${item.id}" type="button">Удалить</button>
        </div>` : ""}
      </article>`).join("")
    : '<div class="empty">В продаже пока нет товаров</div>';
  root.innerHTML = `<div class="shell sale-shell">
    ${topbar(true)}
    <div class="sale-heading">
      <div><p class="eyebrow">Продажа</p><h1>${sale ? `Продажа #${sale.sale_number}` : "Продажи"}</h1></div>
      <button class="button secondary" id="new-sale" type="button">＋ Новая продажа</button>
    </div>
    ${renderSaleSwitcher(sale)}
    <form class="sale-barcode-form" id="sale-barcode-form">
      <label for="sale-barcode-input"><strong>Сканер</strong><span class="muted small">Нажмите на поле и отсканируйте товар</span></label>
      <div class="sale-barcode-controls">
        <input id="sale-barcode-input" name="barcode" type="text" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="Штрихкод" aria-label="Штрихкод товара">
        <button class="button secondary" type="submit">Добавить</button>
      </div>
    </form>
    ${sale ? `<section class="sale-cart ${sale.status === "cancelled" ? "cancelled" : ""}">
      ${sale.status === "cancelled" ? '<p class="sale-cancelled-note">Продажа отменена. История сохранена только для просмотра.</p>' : ""}
      ${renderCheckoutState(sale)}
      <div class="sale-items">${items}</div>
      ${editable ? '<div class="sale-add-actions"><button class="button secondary" id="sale-add-catalog" type="button">＋ Добавить из каталога</button><button class="button secondary" id="sale-add-manual" type="button">＋ Свободная позиция</button></div>' : ""}
      ${editable && sale.items.length ? renderSaleDiscount(sale) : ""}
      <div class="sale-totals"><div><span>Подытог</span><strong>${formatMoney(sale.subtotal_amount)}</strong></div>${Number(sale.discount_amount) > 0 ? `<div class="sale-discount-total"><span>Скидка ${Number(sale.discount_value)}%</span><strong>−${formatMoney(sale.discount_amount)}</strong></div>` : ""}<div class="sale-total"><span>К оплате</span><strong>${formatMoney(sale.total_amount)}</strong></div></div>
      ${editable && sale.items.length ? '<button class="button full sale-checkout-button" id="sale-checkout" type="button" disabled>Проверяем настройку оплаты…</button>' : ""}
      ${editable ? `<div class="sale-actions"><button class="button secondary" id="defer-sale" type="button">Отложить</button><button class="button danger" id="cancel-sale" type="button">Отменить продажу</button></div>` : ""}
    </section>` : '<section class="empty sale-empty"><strong>Активной продажи нет</strong><p>Выберите отложенную или начните новую продажу.</p></section>'}
  </div>`;
  bindTopbar();
  document.querySelector("#new-sale").addEventListener("click", createAndOpenSale);
  document.querySelector("#sale-barcode-form").addEventListener("submit", submitSaleBarcode);
  document.querySelectorAll("[data-switch-sale]").forEach((button) => {
    button.addEventListener("click", () => switchSale(button.dataset.switchSale));
  });
  document.querySelectorAll("[data-sale-quantity]").forEach((button) => {
    button.addEventListener("click", () => changeSaleItemQuantity(
      sale.id,
      button.dataset.saleItem,
      Number(button.dataset.saleQuantity),
    ));
  });
  document.querySelectorAll("[data-remove-sale-item]").forEach((button) => {
    button.addEventListener("click", () => removeSaleItem(sale.id, button.dataset.removeSaleItem));
  });
  document.querySelector("#sale-add-catalog")?.addEventListener("click", () => openOperationsCatalog({ mode: "sale" }));
  document.querySelector("#sale-add-manual")?.addEventListener("click", () => openManualSaleItemDialog(sale.id));
  document.querySelectorAll("[data-sale-discount]").forEach((button) => {
    button.addEventListener("click", () => updateSaleDiscount(sale.id, button.dataset.saleDiscount));
  });
  document.querySelector("#custom-sale-discount")?.addEventListener("click", () => openCustomDiscountDialog(sale));
  document.querySelector("#sale-checkout")?.addEventListener("click", () => openCheckoutConfirmation(sale));
  document.querySelector("#retry-payment")?.addEventListener("click", () => openCheckoutConfirmation(sale));
  document.querySelector("#retry-draft-payment")?.addEventListener("click", () => openCheckoutConfirmation(sale));
  document.querySelector("#return-to-sale")?.addEventListener("click", () => document.querySelector(".sale-items")?.scrollIntoView({ behavior: "smooth" }));
  document.querySelector("#retry-fiscalization")?.addEventListener("click", () => executeCheckoutCommand(sale.id, "retry-fiscalization"));
  document.querySelector("#defer-sale")?.addEventListener("click", deferActiveSale);
  document.querySelector("#cancel-sale")?.addEventListener("click", () => cancelSale(sale.id));
  if (editable && sale.items.length) void hydrateCheckoutButton(sale.id);
  scheduleCheckoutProgress(sale);
}

function renderSaleDiscount(sale) {
  const selected = String(Number(sale.discount_value));
  const preset = (value, label) => `<button class="discount-choice ${selected === value ? "active" : ""}" data-sale-discount="${value}" type="button">${label}</button>`;
  const custom = !["0", "5", "10"].includes(selected);
  return `<section class="sale-discount"><strong>Скидка</strong><div class="discount-choices">${preset("0", "Нет")}${preset("5", "5%")}${preset("10", "10%")}<button class="discount-choice ${custom ? "active" : ""}" id="custom-sale-discount" type="button">${custom ? `${selected}%` : "Своя"}</button></div></section>`;
}

function renderCheckoutState(sale) {
  const payment = sale.payments?.[sale.payments.length - 1];
  const fiscal = sale.fiscalization;
  if (sale.status === "cancelled") return "";
  if (sale.status === "draft" && !["canceled", "failed"].includes(payment?.status)) return "";
  const messages = {
    payment_pending: payment?.status === "unknown"
      ? ["⚠ Результат оплаты пока неизвестен", "Проверяем состояние AQSI. Не запускайте новую оплату."]
      : ["Ожидаем оплату на кассе…", "Следуйте подсказкам на устройстве AQSI."],
    paid: ["✓ Оплата прошла", "Фиксируем продажу в учёте…"],
    fiscalization_pending: fiscal?.status === "unknown"
      ? ["⚠ Оплата прошла, статус чека пока неизвестен", "Проверяем AQSI. Повторная оплата запрещена."]
      : ["✓ Оплата прошла", "Формируем фискальный чек…"],
    completed: fiscal?.status === "skipped"
      ? ["✓ Продажа завершена", "Оплата подтверждена, товар списан, фискальный чек не формировался."]
      : ["✓ Продажа завершена", "Оплата подтверждена, товар списан, чек фискализирован."],
    payment_failed: ["⚠ Оплата не выполнена", "Можно явно создать новую попытку оплаты."],
    fiscalization_failed: ["⚠ Оплата прошла, но чек не сформирован", "Повторяйте только формирование чека, не оплату."],
  };
  const draftOutcome = payment?.status === "canceled"
    ? ["Оплата отменена на терминале", "Деньги не списаны. Продажу можно изменить и оплатить снова."]
    : ["Оплата не выполнена", "Деньги не списаны. Продажу можно изменить и оплатить снова."];
  const [title, detail] = sale.status === "draft" ? draftOutcome : messages[sale.status] || ["Состояние продажи", sale.status];
  const paymentLabels = { succeeded: "✓ Оплачено", canceled: "Отменено на терминале", failed: "Не выполнено", unknown: "Результат неизвестен", pending: "Ожидается" };
  const paymentMethodLabel = payment?.payment_method === "cash" ? "Наличными" : "Карта / QR";
  const paidLabel = payment?.status === "succeeded" ? `✓ ${paymentMethodLabel}` : paymentMethodLabel;
  const paymentSummary = payment ? `<div><span>Оплата</span><strong>${paidLabel} · ${formatMoney(payment.requested_amount)}</strong><small>${escapeHtml(paymentLabels[payment.status] || payment.status)}${payment.external_id ? ` · ${escapeHtml(payment.external_id)}` : ""}</small></div>` : "";
  const fiscalStatusLabel = fiscal?.status === "succeeded"
    ? "✓ Фискализирован"
    : fiscal?.status === "skipped"
      ? "— Не формировался"
      : fiscal?.status;
  const fiscalSummary = fiscal ? `<div><span>Фискальный чек</span><strong>${escapeHtml(fiscalStatusLabel)}</strong>${fiscal.external_receipt_id ? `<small>№ ${escapeHtml(fiscal.external_receipt_id)}</small>` : ""}</div>` : "";
  const action = sale.status === "draft" && ["canceled", "failed"].includes(payment?.status)
    ? '<div class="checkout-recovery-actions"><button class="button" id="retry-draft-payment" type="button">Повторить оплату</button><button class="button secondary" id="return-to-sale" type="button">Вернуться к продаже</button></div>'
    : sale.status === "payment_failed"
    ? '<button class="button" id="retry-payment" type="button">Повторить оплату</button>'
    : sale.status === "fiscalization_failed"
      ? '<button class="button" id="retry-fiscalization" type="button">Повторить формирование чека</button>'
      : "";
  return `<section class="checkout-state checkout-${sale.status}"><strong>${title}</strong><p>${detail}</p>${paymentSummary || fiscalSummary ? `<div class="transaction-summary">${paymentSummary}${fiscalSummary}</div>` : ""}${action}</section>`;
}

async function hydrateCheckoutButton(saleId) {
  const button = document.querySelector("#sale-checkout");
  if (!button) return;
  try {
    const context = await api(`/api/sales/${saleId}/checkout/context`);
    const current = document.querySelector("#sale-checkout");
    if (!current) return;
    current.disabled = !context.available;
    const sale = state.sales.drafts.find((item) => item.id === saleId) || state.sales.active;
    current.textContent = context.available ? `Оплатить ${formatMoney(sale?.total_amount || 0)}` : "Оплата не настроена";
    current.title = context.message;
  } catch (error) {
    button.textContent = "Не удалось проверить оплату";
    button.title = error.message;
  }
}

async function openCheckoutConfirmation(sale) {
  try {
    const context = await api(`/api/sales/${sale.id}/checkout/context`);
    if (!context.available) throw new Error(context.message);
    const warnings = context.stock_warnings.length
      ? `<div class="checkout-warnings"><strong>Остаток не блокирует продажу</strong>${context.stock_warnings.map((warning) => `<p>${escapeHtml(warning.label)}: на учёте ${warning.on_hand}, будет ${warning.after_sale}</p>`).join("")}</div>`
      : "";
    const dialog = document.createElement("dialog");
    dialog.className = "label-dialog checkout-dialog";
    const acquiringLabel = context.acquiring_label || "Карта / QR";
    const providerDisabled = context.fiscalization_available ? "" : "disabled";
    const defaultCard = context.fiscalization_available ? "checked" : "";
    const defaultCashWithoutReceipt = context.fiscalization_available ? "" : "checked";
    dialog.innerHTML = `<form method="dialog"><p class="eyebrow">Подтверждение оплаты</p><h2>Продажа #${sale.sale_number}</h2><div class="checkout-facts"><span>${sale.item_quantity} поз.</span><span>Подытог: ${formatMoney(sale.subtotal_amount)}</span>${Number(sale.discount_amount) > 0 ? `<span>Скидка: −${formatMoney(sale.discount_amount)}</span>` : ""}<strong>К оплате: ${formatMoney(sale.total_amount)}</strong></div><fieldset class="checkout-method-options"><legend>Способ оплаты</legend><label><input type="radio" name="payment_option" value="card" ${defaultCard} ${providerDisabled}> <span><strong>${escapeHtml(acquiringLabel)}</strong><small>Оплата и чек через AQSI</small></span></label><label><input type="radio" name="payment_option" value="cash_with_receipt" ${providerDisabled}> <span><strong>Наличными + чек</strong><small>Наличные, фискальный чек через AQSI</small></span></label><label><input type="radio" name="payment_option" value="cash_without_receipt" ${defaultCashWithoutReceipt}> <span><strong>Наличными без чека</strong><small>Продажа будет учтена в Core без отправки на кассу</small></span></label></fieldset><div class="cash-amount hidden" id="cash-amount"><span>К получению</span><strong>${formatMoney(sale.total_amount)}</strong></div><div class="checkout-method"><span class="muted small">Касса для фискального чека</span><strong id="checkout-fiscal-provider">${context.fiscalization_available ? escapeHtml(context.integration_name) : "Не настроена"}</strong><span class="muted small checkout-method-note" id="checkout-method-note"></span></div>${warnings}<div class="sale-actions"><button class="button secondary" value="cancel">Отмена</button><button class="button" id="confirm-checkout" value="default"></button></div></form>`;
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove());
    const confirm = dialog.querySelector("#confirm-checkout");
    const updateMethod = () => {
      const option = dialog.querySelector('input[name="payment_option"]:checked').value;
      const isCash = option.startsWith("cash_");
      confirm.textContent = isCash ? "Получено наличными" : `Оплатить ${formatMoney(sale.total_amount)}`;
      dialog.querySelector("#cash-amount").classList.toggle("hidden", !isCash);
      dialog.querySelector("#checkout-method-note").textContent = option === "cash_without_receipt"
        ? "На кассу ничего не отправляется"
        : option === "cash_with_receipt"
          ? "Acquiring не запускается; на AQSI отправляется только наличный чек"
          : `На устройстве откроется экран ${acquiringLabel.toLowerCase()}, затем сформируется чек`;
      dialog.querySelector("#checkout-fiscal-provider").textContent = option === "cash_without_receipt"
        ? "Не используется"
        : context.integration_name || "Не настроена";
    };
    dialog.querySelectorAll('input[name="payment_option"]').forEach((input) => input.addEventListener("change", updateMethod));
    updateMethod();
    confirm.addEventListener("click", (event) => {
      event.preventDefault();
      const paymentOption = dialog.querySelector('input[name="payment_option"]:checked').value;
      dialog.close();
      const latest = sale.payments?.[sale.payments.length - 1];
      const retry = ["canceled", "failed"].includes(latest?.status);
      void executeCheckoutCommand(sale.id, retry ? "retry-payment" : "", paymentOption);
    });
    dialog.showModal();
  } catch (error) { showToast(error.message, true); }
}

async function executeCheckoutCommand(saleId, suffix, paymentOption = null) {
  try {
    const path = `/api/sales/${saleId}/checkout${suffix ? `/${suffix}` : ""}`;
    const options = { method: "POST" };
    if (paymentOption) options.body = JSON.stringify({ payment_option: paymentOption });
    const sale = await api(path, options);
    rememberSale(sale);
    renderSalesWorkspace(sale);
  } catch (error) { showToast(error.message, true); }
}

function scheduleCheckoutProgress(sale) {
  clearTimeout(salesProgressTimer);
  if (!sale || !["payment_pending", "paid", "fiscalization_pending"].includes(sale.status)) return;
  salesProgressTimer = setTimeout(async () => {
    if (!window.location.hash.startsWith(`#sale/${sale.id}`)) return;
    try {
      const current = await api(`/api/sales/${sale.id}/checkout/progress`, { method: "POST" });
      rememberSale(current);
      renderSalesWorkspace(current);
    } catch (error) {
      showToast(error.message, true);
      salesProgressTimer = setTimeout(() => scheduleCheckoutProgress(sale), 3000);
    }
  }, 1200);
}

function renderSaleSwitcher(current) {
  const drafts = state.sales.drafts;
  const active = current?.status === "draft" && current.id === state.sales.activeId ? current : null;
  const deferred = drafts.filter((sale) => sale.id !== active?.id);
  const row = (sale, selected) => `<button class="sale-switch-row ${selected ? "active" : ""}" data-switch-sale="${sale.id}" type="button">
    <span>${selected ? "✓ " : ""}#${sale.sale_number}</span>
    <span>${sale.item_quantity} поз.</span>
    <strong>${formatMoney(sale.total_amount)}</strong>
  </button>`;
  return `<details class="sale-switcher" ${current ? "" : "open"}>
    <summary>Корзины · ${drafts.length}</summary>
    <div class="sale-switcher-content">
      <span class="muted small">Текущая</span>
      ${active ? row(active, true) : '<span class="muted small sale-switcher-empty">Не выбрана</span>'}
      <span class="muted small">Отложенные</span>
      ${deferred.length ? deferred.map((sale) => row(sale, false)).join("") : '<span class="muted small sale-switcher-empty">Нет отложенных продаж</span>'}
    </div>
  </details>`;
}

async function createAndOpenSale() {
  try {
    const sale = await api("/api/sales", { method: "POST" });
    rememberSale(sale);
    await openSalesWorkspace(sale.id);
  } catch (error) { showToast(error.message, true); }
}

async function switchSale(saleId) {
  selectActiveSale(saleId);
  await openSalesWorkspace(saleId);
}

function deferActiveSale() {
  selectActiveSale(null);
  recordRoute("sale");
  renderSalesWorkspace(null);
  showToast("Продажа отложена");
}

async function changeSaleItemQuantity(saleId, itemId, delta) {
  try {
    const sale = await api(`/api/sales/${saleId}/items/${itemId}/quantity`, {
      method: "POST",
      body: JSON.stringify({ delta }),
    });
    rememberSale(sale);
    renderSalesWorkspace(sale);
  } catch (error) { showToast(error.message, true); }
}

async function removeSaleItem(saleId, itemId) {
  try {
    const sale = await api(`/api/sales/${saleId}/items/${itemId}`, { method: "DELETE" });
    rememberSale(sale);
    renderSalesWorkspace(sale);
    showToast("Позиция удалена");
  } catch (error) { showToast(error.message, true); }
}

function openManualSaleItemDialog(saleId) {
  const dialog = document.createElement("dialog");
  dialog.className = "label-dialog manual-sale-dialog";
  dialog.innerHTML = `<form id="manual-sale-item-form"><p class="eyebrow">Свободная позиция</p><h2>Добавить без каталога</h2><label>Название<input name="name" maxlength="511" required autofocus placeholder="Игрушка антистресс"></label><label>Цена<input name="unit_price" type="number" inputmode="decimal" min="0.01" step="0.01" required placeholder="350.00"></label><label>Количество<input name="quantity" type="number" inputmode="numeric" min="1" max="9999" step="1" value="1" required></label><p class="muted small">Свободная позиция попадёт в чек, но не изменит складской остаток.</p><div class="sale-actions"><button class="button secondary" id="cancel-manual-sale-item" type="button">Отмена</button><button class="button" type="submit">Добавить</button></div></form>`;
  document.body.append(dialog);
  dialog.addEventListener("close", () => dialog.remove());
  dialog.querySelector("#cancel-manual-sale-item").addEventListener("click", () => dialog.close());
  dialog.querySelector("#manual-sale-item-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      const sale = await api(`/api/sales/${saleId}/items/manual`, {
        method: "POST",
        body: JSON.stringify({
          name: String(data.get("name") || "").trim(),
          unit_price: String(data.get("unit_price") || ""),
          quantity: Number(data.get("quantity") || 1),
        }),
      });
      dialog.close();
      rememberSale(sale);
      renderSalesWorkspace(sale);
      showToast("Свободная позиция добавлена");
    } catch (error) { showToast(error.message, true); }
  });
  dialog.showModal();
}

async function updateSaleDiscount(saleId, discountValue) {
  try {
    const sale = await api(`/api/sales/${saleId}/discount`, {
      method: "PATCH",
      body: JSON.stringify({ discount_value: String(discountValue) }),
    });
    rememberSale(sale);
    renderSalesWorkspace(sale);
  } catch (error) { showToast(error.message, true); }
}

function openCustomDiscountDialog(sale) {
  const dialog = document.createElement("dialog");
  dialog.className = "label-dialog custom-discount-dialog";
  dialog.innerHTML = `<form id="custom-discount-form"><p class="eyebrow">Скидка</p><h2>Своя скидка</h2><label>Процент<input name="discount_value" type="number" inputmode="decimal" min="0" max="99.99" step="0.01" value="${escapeHtml(sale.discount_value)}" required></label><p class="muted small">Максимум 99,99%. Итог и строки чека рассчитывает сервер.</p><div class="sale-actions"><button class="button secondary" id="cancel-custom-discount" type="button">Отмена</button><button class="button" type="submit">Применить</button></div></form>`;
  document.body.append(dialog);
  dialog.addEventListener("close", () => dialog.remove());
  dialog.querySelector("#cancel-custom-discount").addEventListener("click", () => dialog.close());
  dialog.querySelector("#custom-discount-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    dialog.close();
    await updateSaleDiscount(sale.id, data.get("discount_value"));
  });
  dialog.showModal();
}

async function cancelSale(saleId) {
  if (!window.confirm("Отменить продажу? Товары и сумма сохранятся в истории.")) return;
  try {
    const sale = await api(`/api/sales/${saleId}/cancel`, { method: "POST" });
    state.sales.drafts = state.sales.drafts.filter((item) => item.id !== sale.id);
    if (state.sales.activeId === sale.id) selectActiveSale(null);
    renderSalesWorkspace(sale);
    showToast(`Продажа #${sale.sale_number} отменена`);
  } catch (error) { showToast(error.message, true); }
}

async function addVariantToActiveSale(variantId) {
  const activeId = state.sales.activeId;
  const path = activeId
    ? `/api/sales/${activeId}/items/by-variant`
    : "/api/sales/auto/items/by-variant";
  try {
    const sale = await api(path, {
      method: "POST",
      body: JSON.stringify({ variant_id: variantId }),
    });
    const added = sale.items.find((item) => item.variant_id === variantId);
    rememberSale(sale);
    showToast(`✓ ${added?.display_label_snapshot || "Товар"} добавлен · Продажа #${sale.sale_number}`);
  } catch (error) { showToast(error.message, true); }
}

async function addBarcodeToActiveSale(barcode) {
  const activeId = state.sales.activeId;
  const path = activeId
    ? `/api/sales/${activeId}/items/by-barcode`
    : "/api/sales/auto/items/by-barcode";
  try {
    const sale = await api(path, {
      method: "POST",
      body: JSON.stringify({ barcode }),
    });
    const added = sale.items.find((item) => item.barcode_snapshot === barcode.trim());
    rememberSale(sale);
    showToast(`✓ ${added?.display_label_snapshot || "Товар"} добавлен · Продажа #${sale.sale_number}`);
    if (window.location.hash.startsWith("#sale")) {
      recordRoute("sale", { saleId: sale.id });
      renderSalesWorkspace(sale);
    }
    return true;
  } catch (error) {
    showToast(error.message, true);
    return false;
  }
}

async function submitSaleBarcode(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const input = form.querySelector("#sale-barcode-input");
  const barcode = input.value.trim();
  if (!barcode) return;
  const button = form.querySelector("button");
  input.disabled = true;
  button.disabled = true;
  const added = await addBarcodeToActiveSale(barcode);
  const currentInput = document.querySelector("#sale-barcode-input");
  const currentButton = document.querySelector("#sale-barcode-form button");
  if (!currentInput || !currentButton) return;
  currentInput.disabled = false;
  currentButton.disabled = false;
  if (added) currentInput.value = "";
  else currentInput.value = barcode;
  currentInput.focus();
}

async function handleIntakeScanner(value) {
  if (state.mode !== "known") {
    state.mode = "known";
    renderWorkspace();
  }
  await acceptScannedBarcode("intake-variant-query", value);
}

async function startSession() {
  try {
    const session = await api("/api/intake/sessions", { method: "POST" });
    await openSession(session.id);
  } catch (error) { showToast(error.message, true); }
}

async function loadReferences() {
  if (state.referencesLoaded) return;
  [state.categories, state.suppliers, state.products, state.variants, state.printCapability] = await Promise.all([
    api("/api/catalog/categories"),
    api("/api/purchasing/suppliers"),
    api("/api/catalog/products"),
    api("/api/catalog/variants"),
    api("/api/labels/variants/print-capability"),
  ]);
  state.referencesLoaded = true;
}

async function openSession(id) {
  try {
    recordRoute("intake", { sessionId: id });
    logicalParent = () => loadHome();
    await loadReferences();
    state.session = await api(`/api/intake/sessions/${id}`);
    state.mode = null;
    state.result = null;
    await loadItemDisplays();
    renderWorkspace();
  } catch (error) { showToast(error.message, true); }
}

async function refreshSession() {
  await flushProductAutosaves();
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
  const productGroups = groupIntakeItems(activeItems);
  const productCount = productGroups.length;
  const variantCount = activeItems.length;
  root.innerHTML = `
    <div class="shell">
      ${topbar(true)}
      <p class="eyebrow">Черновик сохраняется по шагам</p>
      <h1>Что приехало?</h1>
      <p class="muted">Отсканируйте знакомый товар или сразу сфотографируйте новый.</p>
      <div class="actions">
        <button class="action-card" id="known-action"><span class="action-icon">▦</span><strong>Сканировать или найти</strong><span class="muted small">Повторная поставка</span></button>
        <button class="action-card" id="photo-action"><span class="action-icon">◉</span><strong>Сфотографировать</strong><span class="muted small">Новый товар</span></button>
        <button class="action-card" id="variant-action"><span class="action-icon">＋</span><strong>Новый вариант</strong><span class="muted small">Для товара из Catalog</span></button>
      </div>
      ${renderActionPanel()}
      <h2>${productCount} ${pluralizeRu(productCount, "товар", "товара", "товаров")} · ${variantCount} ${pluralizeRu(variantCount, "вариант", "варианта", "вариантов")}</h2>
      <div>${productGroups.length ? productGroups.map(renderProductGroup).join("") : '<div class="empty">Добавьте первый товар</div>'}</div>
      ${renderSessionFinish()}
      ${renderDeleteIntakeDraft()}
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
    state.intakeBarcode = { value: "", result: null, unknown: false };
    document.querySelector("#photo-input").click();
  });
  document.querySelector("#variant-action").addEventListener("click", async () => {
    try {
      await saveAllItemForms();
      state.mode = "new_variant";
      renderWorkspace();
    } catch (error) { showToast(error.message, true); }
  });
  document.querySelector("#photo-input")?.addEventListener("change", uploadNewPhoto);
  bindCatalogSearch("variant");
  bindCatalogSearch("product");
  document.querySelectorAll("[data-barcode-camera]").forEach((button) => {
    button.addEventListener("click", () => startBarcodeCamera(button.dataset.barcodeTarget));
  });
  document.querySelectorAll("[data-print-intake-label]").forEach((button) => {
    button.addEventListener("click", () => {
      const quantity = Number(button.dataset.defaultQuantity || 1);
      printVariantLabels(button.dataset.printIntakeLabel, quantity);
    });
  });
  document.querySelectorAll("[data-open-label]").forEach((button) => {
    button.addEventListener("click", () => openVariantLabel(button.dataset.openLabel));
  });
  document.querySelectorAll("[data-open-draft-label]").forEach((button) => {
    button.addEventListener("click", () => openDraftLabel(button.dataset.openDraftLabel));
  });
  document.querySelectorAll("[data-print-draft-label]").forEach((button) => {
    button.addEventListener("click", () => printDraftLabels(
      button.dataset.printDraftLabel,
      Number(button.dataset.defaultQuantity || 1),
    ));
  });
  document.querySelectorAll("[data-manufacturer-barcode]").forEach((input) => {
    input.addEventListener("change", () => identifyDraftManufacturerBarcode(input));
    input.addEventListener("paste", () => setTimeout(() => identifyDraftManufacturerBarcode(input), 0));
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      identifyDraftManufacturerBarcode(input);
    });
  });
  document.querySelector("#known-form")?.addEventListener("submit", addKnownItem);
  document.querySelector("#existing-product-variant-form")?.addEventListener("submit", addVariantToExistingProduct);
  document.querySelectorAll("[data-product-form]").forEach(bindProductAutosave);
  document.querySelectorAll("[data-item-form]").forEach((form) => form.addEventListener("submit", saveItem));
  document.querySelectorAll("[data-replace-item-image]").forEach((input) => input.addEventListener("change", () => replaceDraftImage(input)));
  document.querySelectorAll("[data-add-draft-variant]").forEach((button) => button.addEventListener("click", () => addDraftVariant(button.dataset.addDraftVariant)));
  document.querySelectorAll("[data-abandon-item]").forEach((button) => button.addEventListener("click", () => abandonDraftItem(button.dataset.abandonItem)));
  document.querySelector("#supplier")?.addEventListener("change", saveSupplier);
  document.querySelector("#complete-session")?.addEventListener("click", completeSession);
  document.querySelector("#delete-intake-draft")?.addEventListener("click", deleteIntakeDraft);
  hydrateImages();
  ensureScannerService().setLocalHandler("intake", (value) => {
    void handleIntakeScanner(value);
    return true;
  });
}

function bindCatalogSearch(kind) {
  const form = document.querySelector(`#${kind}-search-form`);
  const input = document.querySelector(`#intake-${kind}-query`);
  if (!form || !input) return;
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    runCatalogSearch(kind, input.value, true);
  });
  input.addEventListener("input", () => {
    clearTimeout(intakeSearchTimer);
    const value = input.value;
    if (kind === "variant" && state.intakeBarcode.value !== value.trim()) {
      state.intakeBarcode = { value: "", result: null, unknown: false };
    }
    intakeSearchTimer = setTimeout(() => runCatalogSearch(kind, value), 400);
  });
  bindCatalogSearchResultActions(kind);
}

function bindCatalogSearchResultActions(kind) {
  document.querySelectorAll(`[data-select-${kind}]`).forEach((button) => {
    button.addEventListener("click", () => selectCatalogSearchResult(kind, button.dataset[`select${kind[0].toUpperCase()}${kind.slice(1)}`]));
  });
  if (kind === "variant") {
    document.querySelector("#search-create-new")?.addEventListener("click", () => document.querySelector("#photo-input").click());
  }
}

async function runCatalogSearch(kind, rawQuery, explicit = false) {
  const query = String(rawQuery || "").trim().replace(/\s+/g, " ");
  const search = kind === "variant" ? state.intakeVariantSearch : state.intakeProductSearch;
  search.query = query;
  search.selected = null;
  document.querySelector(kind === "variant" ? "#known-form" : "#existing-product-variant-form")?.remove();
  const looksLikeIdentifier = /^[\p{L}\p{N}_.\/-]+$/u.test(query) && /[\d_.\/-]/.test(query);
  if (!query || (!explicit && query.length < 2 && !looksLikeIdentifier)) {
    search.items = [];
    search.hasMore = false;
    updateCatalogSearchResults(kind);
    return;
  }
  search.loading = true;
  updateCatalogSearchResults(kind);
  try {
    const page = await api(`/api/catalog/search/${kind === "variant" ? "variants" : "products"}?query=${encodeURIComponent(query)}&limit=12`);
    if (search.query !== query) return;
    search.items = page.items;
    search.hasMore = page.has_more;
    if (kind === "variant" && !page.items.length && (
      /^\d{8,14}$/.test(query) || state.intakeBarcode.value === query
    )) {
      state.intakeBarcode = { value: query, result: null, unknown: true };
    }
  } catch (error) {
    showToast(error.message, true);
  } finally {
    if (search.query === query) {
      search.loading = false;
      updateCatalogSearchResults(kind);
    }
  }
}

function updateCatalogSearchResults(kind) {
  const target = document.querySelector(`#${kind}-search-results`);
  if (!target) return;
  target.innerHTML = kind === "variant" ? renderVariantSearchResults() : renderProductSearchResults();
  bindCatalogSearchResultActions(kind);
}

function selectCatalogSearchResult(kind, id) {
  const search = kind === "variant" ? state.intakeVariantSearch : state.intakeProductSearch;
  search.selected = search.items.find((item) => item.id === id) || null;
  if (!search.selected) return;
  renderWorkspace();
  if (kind === "variant") {
    const input = document.querySelector("#known-retail-price");
    if (input) input.value = search.selected.retail_price ?? "";
    document.querySelector("#known-quantity")?.focus();
  } else {
    document.querySelector("#existing-product-variant-form input[type=file]")?.focus();
  }
}

function pluralizeRu(value, one, few, many) {
  const mod100 = Math.abs(value) % 100;
  const mod10 = mod100 % 10;
  if (mod100 > 10 && mod100 < 20) return many;
  if (mod10 === 1) return one;
  if (mod10 >= 2 && mod10 <= 4) return few;
  return many;
}

function groupIntakeItems(items) {
  const groups = new Map();
  items.forEach((item) => {
    const display = state.itemDisplay.get(item.id);
    const key = item.kind === "new_product"
      ? `draft:${item.id}`
      : item.draft_product_item_id
        ? `draft:${item.draft_product_item_id}`
        : `catalog:${item.product_id || display?.product?.id || item.id}`;
    if (!groups.has(key)) groups.set(key, { key, root: null, product: display?.product || null, items: [] });
    const group = groups.get(key);
    group.items.push(item);
    if (item.kind === "new_product") group.root = item;
    if (!group.product && display?.product) group.product = display.product;
  });
  return [...groups.values()];
}

function renderProductGroup(group) {
  const rootItem = group.root;
  const title = rootItem?.product_title || group.product?.title || "Новый товар";
  const categoryOptions = renderCategoryOptions(state.categories, {
    selectedId: rootItem?.category_id,
    includeRoot: false,
  });
  const productForm = rootItem ? `<form class="drawer" data-product-form="${rootItem.id}">
    <div class="field"><label>Общее фото товара</label><input type="file" accept="image/*" capture="environment" data-replace-item-image="${rootItem.id}"></div>
    <div class="field"><label>Категория</label><select name="category_id" required><option value="">Выберите категорию</option>${categoryOptions}</select></div>
    <div class="field"><label>Название товара</label><input name="product_title" value="${escapeHtml(rootItem.product_title || "")}" required></div>
    <div class="field"><label>Описание <span class="muted">(необязательно)</span></label><textarea name="product_description">${escapeHtml(rootItem.product_description || "")}</textarea></div>
    <span class="muted small" data-save-status role="status" aria-live="polite">Сохранено</span>
    <button class="link-button hidden" data-save-retry type="button">Повторить сохранение</button>
  </form>` : `<p class="muted small">Товар уже существует в Catalog. В приёмке редактируются только данные поступивших вариантов.</p>`;
  return `<section class="card product-group">
    <p class="eyebrow">Товар</p>
    <h2>${escapeHtml(title)}</h2>
    ${rootItem?.image_id ? `<img class="catalog-photo" data-image-id="${rootItem.image_id}" alt="${escapeHtml(title)}">` : ""}
    ${productForm}
    <h3>Варианты · ${group.items.length}</h3>
    <div class="variant-list">${group.items.map((item) => renderVariantCard(item, rootItem)).join("")}</div>
    ${rootItem ? `<button class="button ghost full" type="button" data-add-draft-variant="${rootItem.id}">＋ Добавить вариант этого товара</button><button class="button ghost full danger-text" type="button" data-abandon-item="${rootItem.id}">Удалить товар из приёмки</button>` : ""}
  </section>`;
}

async function deleteIntakeDraft() {
  const confirmed = window.confirm(
    "Удалить эту приёмку?\n\nЧерновик и все его позиции будут удалены.\nОтменить действие будет нельзя.",
  );
  if (!confirmed) return;
  const button = document.querySelector("#delete-intake-draft");
  if (!button) return;
  button.disabled = true;
  try {
    await api(`/api/intake/sessions/${state.session.id}`, { method: "DELETE" });
    state.session = null;
    await loadHome();
    showToast("Черновик приёмки удалён");
  } catch (error) {
    button.disabled = false;
    showToast(error.message, true);
  }
}

function renderActionPanel() {
  return `
    <input class="hidden" id="photo-input" type="file" accept="image/*" capture="environment">
    <section class="card ${state.mode === "known" ? "" : "hidden"}">
      <h2>Найти товар или вариант</h2>
      <p class="muted small">Введите часть названия, SKU или штрихкод. Аппаратный сканер можно использовать прямо в этом поле.</p>
      <form id="variant-search-form" class="search-row barcode-lookup-row">
        <input id="intake-variant-query" name="query" value="${escapeHtml(state.intakeVariantSearch.query)}" autocomplete="off" placeholder="Название, SKU или штрихкод" required autofocus>
        <button class="button" type="submit">Найти</button>
        <button class="button secondary" data-barcode-camera data-barcode-target="intake-variant-query" type="button">📷 Сканировать камерой</button>
      </form>
      <div id="variant-search-results">${renderVariantSearchResults()}</div>
      ${state.intakeVariantSearch.selected ? `<form id="known-form">
        <input name="variant_id" type="hidden" value="${state.intakeVariantSearch.selected.id}">
        <div class="selected-catalog-entity"><span class="muted small">Выбран вариант</span><strong>${escapeHtml(state.intakeVariantSearch.selected.product_title)}${meaningfulVariantSuffix(state.intakeVariantSearch.selected.title)}</strong><span class="muted small">${escapeHtml(state.intakeVariantSearch.selected.sku)} · ${escapeHtml(state.intakeVariantSearch.selected.barcode)}</span></div>
        <div class="field-row">
          <div class="field"><label for="known-quantity">Количество</label><input id="known-quantity" name="quantity" type="number" inputmode="numeric" min="1" required></div>
          <div class="field"><label for="known-price">Закупочная цена, ₽</label><input id="known-price" name="purchase_price" type="number" inputmode="decimal" min="0" step="0.01" required></div>
        </div>
        <div class="field"><label for="known-retail-price">Цена продажи, ₽ <span class="muted">(необязательно)</span></label><input id="known-retail-price" name="retail_price" type="number" inputmode="decimal" min="0" step="0.01"></div>
        <div class="rental-allocation">
          <div class="field"><label for="known-rental-quantity">Из них в аренду, шт.</label><input id="known-rental-quantity" name="rental_quantity" type="number" inputmode="numeric" min="0" value="0"></div>
          <p class="muted small">Оставьте 0, если вся партия предназначена для продажи.</p>
        </div>
        <button class="button full" type="submit">Добавить позицию</button>
      </form>` : ""}
    </section>
    <section class="card ${state.mode === "new_variant" ? "" : "hidden"}">
      <h2>Новый вариант существующего товара</h2>
      <p class="muted small">Найдите родительский товар по названию или по данным любого его варианта.</p>
      <form id="product-search-form" class="search-row">
        <input id="intake-product-query" name="query" value="${escapeHtml(state.intakeProductSearch.query)}" autocomplete="off" placeholder="Название, SKU или штрихкод" required autofocus>
        <button class="button" type="submit">Найти</button>
      </form>
      <div id="product-search-results">${renderProductSearchResults()}</div>
      ${state.intakeProductSearch.selected ? `<form id="existing-product-variant-form">
        <input name="product_id" type="hidden" value="${state.intakeProductSearch.selected.id}">
        <div class="selected-catalog-entity"><span class="muted small">Выбран товар</span><strong>${escapeHtml(state.intakeProductSearch.selected.title)}</strong><span class="muted small">${state.intakeProductSearch.selected.variant_count} ${pluralizeRu(state.intakeProductSearch.selected.variant_count, "вариант", "варианта", "вариантов")}</span></div>
        <div class="field"><label>Фото варианта <span class="muted">(необязательно)</span></label><input name="file" type="file" accept="image/*" capture="environment"></div>
        <button class="button full" type="submit">Добавить вариант в приёмку</button>
      </form>` : ""}
    </section>`;
}

function meaningfulVariantSuffix(title) {
  const visible = visibleVariantTitle(title);
  return visible ? ` · ${escapeHtml(visible)}` : "";
}

function renderVariantSearchResults() {
  const search = state.intakeVariantSearch;
  if (search.loading) return '<p class="muted small">Ищем…</p>';
  if (!search.query.trim()) return '<p class="muted small">Начните вводить название, SKU или штрихкод</p>';
  if (!search.items.length) return '<div class="empty compact-empty">Ничего не найдено<br><button class="button ghost" id="search-create-new" type="button">Создать новый товар</button></div>';
  return `${search.items.map((item) => `<article class="catalog-search-result"><div><strong>${escapeHtml(item.product_title)}</strong><div>${escapeHtml(visibleVariantTitle(item.title, "Единственный вариант"))}</div><div class="muted small">${escapeHtml(item.sku)} · ${escapeHtml(item.barcode)}${item.retail_price === null ? "" : ` · ${formatMoney(item.retail_price)}`}</div></div><button class="button secondary" data-select-variant="${item.id}" type="button">Выбрать</button></article>`).join("")}${search.hasMore ? '<p class="muted small">Найдено много вариантов. Уточните запрос.</p>' : ""}`;
}

function renderProductSearchResults() {
  const search = state.intakeProductSearch;
  if (search.loading) return '<p class="muted small">Ищем…</p>';
  if (!search.query.trim()) return '<p class="muted small">Начните вводить название, SKU или штрихкод</p>';
  if (!search.items.length) return '<div class="empty compact-empty">Ничего не найдено</div>';
  return `${search.items.map((item) => `<article class="catalog-search-result"><div><strong>${escapeHtml(item.title)}</strong><div class="muted small">${item.variant_count} ${pluralizeRu(item.variant_count, "вариант", "варианта", "вариантов")}</div>${item.matched_variant_title ? `<div class="muted small">Совпадение: ${escapeHtml(visibleVariantTitle(item.matched_variant_title, "вариант"))} · ${escapeHtml(item.matched_sku)} · ${escapeHtml(item.matched_barcode)}</div>` : ""}</div><button class="button secondary" data-select-product="${item.id}" type="button">Выбрать</button></article>`).join("")}${search.hasMore ? '<p class="muted small">Найдено много товаров. Уточните запрос.</p>' : ""}`;
}

async function lookupVariantBarcode(value) {
  try {
    return {
      variant: await api(`/api/catalog/variants/lookup/by-barcode?barcode=${encodeURIComponent(value)}`),
    };
  } catch (error) {
    if (error.message === "Variant not found.") return { variant: null };
    throw error;
  }
}

async function identifyDraftManufacturerBarcode(input) {
  const value = input.value.trim();
  const status = document.querySelector(`[data-barcode-status="${input.id}"]`);
  if (!value) {
    if (status) status.textContent = "";
    return;
  }
  input.value = value;
  if (status) status.textContent = "Проверяем штрихкод…";
  try {
    const result = await lookupVariantBarcode(value);
    if (!status) return;
    if (result.variant) {
      const product = state.products.find((item) => item.id === result.variant.product_id);
      status.textContent = `✓ Уже зарегистрирован: ${product?.title || "Товар"} · ${result.variant.title} · ${result.variant.sku}`;
      status.className = "small danger-text";
    } else {
      status.textContent = "✓ Новый штрихкод — будет сохранён как код производителя";
      status.className = "small available-text";
    }
  } catch (error) {
    if (status) {
      status.textContent = error.message;
      status.className = "small danger-text";
    }
    showToast(error.message, true);
  }
}

async function acceptScannedBarcode(inputId, value) {
  const input = document.getElementById(inputId);
  if (!input) return;
  input.value = String(value).trim();
  input.dispatchEvent(new Event("input", { bubbles: true }));
  if (inputId === "intake-variant-query") {
    state.intakeBarcode = { value: input.value, result: null, unknown: false };
    await runCatalogSearch("variant", input.value, true);
  } else await identifyDraftManufacturerBarcode(input);
}

async function loadZxingBrowser() {
  if (window.ZXingBrowser) return window.ZXingBrowser;
  if (zxingLoader) return zxingLoader;
  zxingLoader = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://unpkg.com/@zxing/browser@0.2.1/umd/zxing-browser.min.js";
    script.integrity = "sha384-HRtzk9lZgkbSgvUyQrnfC/GxiXZgwaNyD7hC9wcXlsBpDhkS80ISl73juef2FRuf";
    script.crossOrigin = "anonymous";
    script.onload = () => resolve(window.ZXingBrowser);
    script.onerror = () => reject(new Error("Не удалось загрузить модуль распознавания."));
    document.head.append(script);
  });
  return zxingLoader;
}

async function createNativeBarcodeDetector() {
  if (!("BarcodeDetector" in window)) return null;
  const formats = ["ean_13", "ean_8", "upc_a", "code_128"];
  try {
    const supported = await BarcodeDetector.getSupportedFormats?.();
    if (supported && !formats.every((format) => supported.includes(format))) return null;
    return new BarcodeDetector({ formats });
  } catch {
    return null;
  }
}

function cameraFailureMessage(error) {
  if (!window.isSecureContext) return "Камера доступна только через HTTPS или localhost.";
  if (["NotAllowedError", "SecurityError"].includes(error?.name)) {
    return "Доступ к камере запрещён. Разрешите его в настройках браузера или введите код вручную.";
  }
  if (["NotFoundError", "NotReadableError", "OverconstrainedError"].includes(error?.name)) {
    return "Камера недоступна. Введите штрихкод вручную.";
  }
  return "Сканирование камерой недоступно. Введите штрихкод вручную.";
}

async function startBarcodeCamera(inputId) {
  if (!navigator.mediaDevices?.getUserMedia || !window.isSecureContext) {
    showToast(cameraFailureMessage({}), true);
    document.getElementById(inputId)?.focus();
    return;
  }
  const dialog = document.createElement("dialog");
  dialog.className = "label-dialog barcode-scanner-dialog";
  dialog.innerHTML = `<div><h2>Сканировать штрихкод</h2><div class="scanner-preview"><video autoplay muted playsinline></video><span class="scanner-guide" aria-hidden="true"></span></div><p class="muted small" data-scanner-status>Разрешите камеру и наведите её на EAN, UPC или Code 128.</p><button class="button secondary full" type="button">Закрыть</button></div>`;
  document.body.append(dialog);
  const video = dialog.querySelector("video");
  const scannerStatus = dialog.querySelector("[data-scanner-status]");
  let stream;
  let controls;
  let stopped = false;
  let accepted = false;
  const stop = () => {
    if (stopped) return;
    stopped = true;
    controls?.stop();
    stream?.getTracks().forEach((track) => track.stop());
    dialog.close();
    dialog.remove();
  };
  dialog.querySelector("button").addEventListener("click", stop);
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); stop(); });
  const accept = async (value) => {
    if (accepted || stopped || !value) return;
    accepted = true;
    scannerStatus.textContent = `✓ Распознано: ${value}`;
    scannerStatus.className = "small available-text";
    await new Promise((resolve) => setTimeout(resolve, 350));
    stop();
    await acceptScannedBarcode(inputId, value);
    showToast("Штрихкод распознан и проверен");
  };
  dialog.showModal();
  try {
    const detector = await createNativeBarcodeDetector();
    if (!detector) {
      scannerStatus.textContent = "Запускаем совместимый сканер…";
      const zxing = await loadZxingBrowser();
      if (stopped) return;
      const reader = new zxing.BrowserMultiFormatOneDReader();
      reader.possibleFormats = [
        zxing.BarcodeFormat.EAN_13,
        zxing.BarcodeFormat.EAN_8,
        zxing.BarcodeFormat.UPC_A,
        zxing.BarcodeFormat.CODE_128,
      ];
      controls = await reader.decodeFromConstraints(
        { video: { facingMode: { ideal: "environment" } }, audio: false },
        video,
        (result) => { if (result) accept(result.getText()); },
      );
      if (stopped) controls.stop();
      return;
    }
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" } },
      audio: false,
    });
    if (stopped) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    video.srcObject = stream;
    await video.play();
    const detect = async () => {
      if (stopped) return;
      try {
        const codes = await detector.detect(video);
        if (codes[0]?.rawValue) return accept(codes[0].rawValue);
      } catch {
        scannerStatus.textContent = "Не удалось распознать кадр. Попробуйте ещё раз или закройте сканер для ручного ввода.";
      }
      requestAnimationFrame(detect);
    };
    requestAnimationFrame(detect);
  } catch (error) {
    stop();
    const message = error?.message === "Не удалось загрузить модуль распознавания."
      ? `${error.message} Введите штрихкод вручную.`
      : cameraFailureMessage(error);
    showToast(message, true);
    document.getElementById(inputId)?.focus();
  }
}

function renderItemRequirements(item) {
  return item.missing_requirements.length
    ? item.missing_requirements.map((value) => `<span class="chip warn">${escapeHtml(requirementLabels[value] || value)}</span>`).join("")
    : '<span class="chip good">Позиция заполнена</span>';
}

function updateItemRequirements(item) {
  const chips = document.querySelector(`[data-item-requirements="${item.id}"]`);
  if (chips) chips.innerHTML = renderItemRequirements(item);
}

function renderVariantCard(item, rootItem = null) {
  const display = state.itemDisplay.get(item.id);
  const isExisting = item.kind === "existing_variant";
  const subtitle = isExisting ? visibleVariantTitle(display?.variant?.title, "Единственный вариант") : item.variant_title || (item.kind === "new_product" ? "Единственный вариант" : "Заполните вариант");
  const imageId = isExisting ? display?.imageId : item.kind === "new_variant" ? item.image_id || rootItem?.image_id : rootItem?.image_id;
  return `
    <article class="card variant-card">
      <div class="item">
        ${imageId ? `<img class="item-photo" data-image-id="${imageId}" alt="${escapeHtml(subtitle)}">` : '<div class="photo-placeholder">◎</div>'}
        <div class="item-main">
          <h3 class="item-title">${escapeHtml(subtitle)}</h3>
          <div class="muted small">${display?.variant ? escapeHtml(display.variant.sku) : "Variant"}</div>
          ${item.rental_quantity ? `<div class="rental-summary">В аренду: ${item.rental_quantity} шт.</div>` : ""}
          ${item.reserved_internal_barcode ? `<div class="muted small">Barcode: ${escapeHtml(item.reserved_internal_barcode)} · ${escapeHtml(item.reserved_sku)}</div>` : ""}
          <div class="chips" data-item-requirements="${item.id}" aria-live="polite">${renderItemRequirements(item)}</div>
          ${(display?.variant || item.reserved_internal_barcode) ? `<div class="inline-actions"><button class="button ghost compact" type="button" data-open-draft-label="${item.id}">Открыть PDF</button><button class="button compact" type="button" data-print-draft-label="${item.id}" data-default-quantity="${item.quantity || 1}">Системная печать</button></div>` : ""}
        </div>
      </div>
      <form class="drawer" data-item-form="${item.id}">
        ${item.kind === "new_variant" ? `<div class="field"><label>${item.image_id ? "Заменить фото варианта" : "Фото варианта (необязательно)"}</label><input type="file" accept="image/*" capture="environment" data-replace-item-image="${item.id}"></div>` : ""}
        ${isExisting ? "" : renderNewItemFields(item)}
        <div class="field-row">
          <div class="field"><label>Количество</label><input name="quantity" type="number" inputmode="numeric" min="1" value="${item.quantity ?? ""}" required></div>
          <div class="field"><label>Закупочная цена, ₽</label><input name="purchase_price" type="number" inputmode="decimal" min="0" step="0.01" value="${item.purchase_price ?? ""}" required></div>
        </div>
        <div class="field"><label>Цена продажи, ₽ <span class="muted">(необязательно)</span></label><input name="retail_price" type="number" inputmode="decimal" min="0" step="0.01" value="${item.retail_price ?? ""}"></div>
        <div class="rental-allocation">
          <div class="field"><label>Из них в аренду, шт.</label><input name="rental_quantity" type="number" inputmode="numeric" min="0" ${item.quantity === null ? "" : `max="${item.quantity}"`} value="${item.rental_quantity ?? 0}"></div>
          <p class="muted small">Каждый предмет аренды получит собственный инвентарный номер.</p>
        </div>
        <button class="button secondary full" type="submit">Сохранить позицию</button>
        ${item.kind !== "new_product" ? `<button class="button ghost full danger-text" type="button" data-abandon-item="${item.id}">Удалить вариант из приёмки</button>` : ""}
      </form>
    </article>`;
}

function renderNewItemFields(item) {
  const barcodeInputId = `manufacturer-barcode-${item.id}`;
  if (item.kind === "new_variant") return `
    <div class="field"><label>Название варианта — цвет, размер или исполнение</label><input name="variant_title" value="${escapeHtml(item.variant_title || "")}" required></div>
    <div class="field"><label for="${barcodeInputId}">Штрихкод производителя <span class="muted">(необязательно)</span></label><input id="${barcodeInputId}" name="manufacturer_barcode" data-manufacturer-barcode value="${escapeHtml(item.manufacturer_barcode || "")}" autocomplete="off"><button class="button secondary full" data-barcode-camera data-barcode-target="${barcodeInputId}" type="button">📷 Сканировать камерой</button><div class="small" data-barcode-status="${barcodeInputId}"></div></div>`;
  return `
    <div class="field"><label>Вариант — цвет, размер или исполнение <span class="muted">(необязательно, если вариант один)</span></label><input name="variant_title" value="${escapeHtml(item.variant_title || "")}"></div>
    <div class="field"><label for="${barcodeInputId}">Штрихкод производителя <span class="muted">(необязательно)</span></label><input id="${barcodeInputId}" name="manufacturer_barcode" data-manufacturer-barcode value="${escapeHtml(item.manufacturer_barcode || "")}" autocomplete="off" inputmode="text"><button class="button secondary full" data-barcode-camera data-barcode-target="${barcodeInputId}" type="button">📷 Сканировать камерой</button><div class="small" data-barcode-status="${barcodeInputId}"></div></div>`;
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

function renderDeleteIntakeDraft() {
  if (!state.user?.is_admin) return "";
  return `<section class="card danger-zone">
    <h2>Удаление черновика</h2>
    <p class="muted small">Только для ошибочно созданной незавершённой приёмки.</p>
    <button class="button ghost full danger-text" id="delete-intake-draft" type="button">Удалить черновик приёмки</button>
  </section>`;
}

async function uploadNewPhoto(event) {
  const file = event.target.files[0];
  if (!file) return;
  const data = new FormData();
  data.append("file", file);
  if (state.intakeBarcode.unknown && state.intakeBarcode.value) {
    data.append("manufacturer_barcode", state.intakeBarcode.value);
  }
  showToast("Сохраняем фото…");
  try {
    await saveAllItemForms();
    await api(`/api/intake/sessions/${state.session.id}/items/new`, { method: "POST", body: data });
    state.intakeBarcode = { value: "", result: null, unknown: false };
    state.mode = null;
    await refreshSession();
    showToast("Фото сохранено. Теперь заполните товар.");
  } catch (error) { showToast(error.message, true); }
}

async function addDraftVariant(draftProductItemId) {
  try {
    await saveAllItemForms();
    const data = new FormData();
    data.append("draft_product_item_id", draftProductItemId);
    await api(`/api/intake/sessions/${state.session.id}/items/new`, { method: "POST", body: data });
    await refreshSession();
    showToast("Вариант добавлен. Заполните его данные.");
  } catch (error) { showToast(error.message, true); }
}

async function addVariantToExistingProduct(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const data = new FormData();
  data.append("product_id", form.get("product_id"));
  const file = form.get("file");
  if (file?.size) data.append("file", file);
  try {
    await api(`/api/intake/sessions/${state.session.id}/items/new`, { method: "POST", body: data });
    state.mode = null;
    await refreshSession();
    showToast("Новый вариант добавлен в приёмку");
  } catch (error) { showToast(error.message, true); }
}

async function abandonDraftItem(itemId) {
  if (!window.confirm("Удалить эту позицию из черновика приёмки?")) return;
  try {
    await flushProductAutosaves();
    await api(`/api/intake/sessions/${state.session.id}/items/${itemId}/abandon`, {
      method: "POST",
      body: JSON.stringify({ reason: "Удалено оператором из черновика" }),
    });
    await refreshSession();
    showToast("Позиция удалена из черновика");
  } catch (error) { showToast(error.message, true); }
}

async function replaceDraftImage(input) {
  const file = input.files?.[0];
  if (!file) return;
  const data = new FormData();
  data.append("file", file);
  try {
    await flushProductAutosaves();
    await api(`/api/intake/sessions/${state.session.id}/items/${input.dataset.replaceItemImage}/image`, { method: "PUT", body: data });
    await refreshSession();
    showToast("Фото обновлено");
  } catch (error) { showToast(error.message, true); }
}

function openDraftLabel(itemId, print = false) {
  openAuthenticatedFile(`/api/intake/sessions/${state.session.id}/items/${itemId}/labels/40x30.pdf?dpi=203`, print);
}

async function printDraftLabels(itemId, defaultQuantity) {
  const quantity = promptLabelQuantity(defaultQuantity);
  if (quantity === null) return;
  try {
    const result = await api(`/api/intake/sessions/${state.session.id}/items/${itemId}/labels/40x30/print?quantity=${quantity}`, { method: "POST" });
    showToast(`Задание отправлено на печать · ${result.quantity} шт.`);
  } catch (error) {
    showToast("Не удалось отправить на печать. Откройте PDF и используйте системную печать.", true);
  }
}

async function addKnownItem(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  const payload = {
    variant_id: String(data.get("variant_id")),
    quantity: Number(data.get("quantity")),
    rental_quantity: Number(data.get("rental_quantity") || 0),
    purchase_price: String(data.get("purchase_price")),
    retail_price: nullableText(data.get("retail_price")),
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
  const form = event.currentTarget;
  try {
    await flushProductAutosaves();
    await persistItemForm(form);
    await refreshSession();
    showToast("Позиция сохранена");
  } catch (error) { showToast(error.message, true); }
}

const productAutosaves = new WeakMap();

function bindProductAutosave(form) {
  const entry = { dirty: false, timer: null, running: null, revision: 0 };
  productAutosaves.set(form, entry);
  const change = (immediate) => {
    entry.dirty = true;
    entry.revision += 1;
    clearTimeout(entry.timer);
    form.querySelector("[data-save-status]").textContent = "Ожидает сохранения…";
    entry.timer = setTimeout(() => persistProductForm(form).catch(() => {}), immediate ? 0 : 600);
  };
  form.querySelectorAll("input[name], textarea[name]").forEach((input) => input.addEventListener("input", () => change(false)));
  form.querySelectorAll("select[name]").forEach((input) => input.addEventListener("change", () => change(true)));
  form.addEventListener("submit", (event) => { event.preventDefault(); persistProductForm(form).catch(() => {}); });
  form.querySelector("[data-save-retry]").addEventListener("click", () => persistProductForm(form).catch(() => {}));
}

window.addEventListener("beforeunload", (event) => {
  if ([...document.querySelectorAll("[data-product-form]")].some((form) => productAutosaves.get(form)?.dirty)) {
    event.preventDefault();
    event.returnValue = "";
  }
});

async function flushProductAutosaves() {
  await Promise.all([...document.querySelectorAll("[data-product-form]")].map(persistProductForm));
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
  const retailPrice = nullableText(data.get("retail_price"));
  const payload = {
    quantity: quantity === null ? null : Number(quantity),
    rental_quantity: rentalQuantity === null ? 0 : Number(rentalQuantity),
    purchase_price: purchasePrice,
    retail_price: retailPrice,
  };
  if (item.kind !== "existing_variant") {
    Object.assign(payload, {
      variant_title: nullableText(data.get("variant_title")),
      manufacturer_barcode: nullableText(data.get("manufacturer_barcode")),
    });
  }
  return payload;
}

async function persistProductForm(form) {
  const entry = productAutosaves.get(form);
  if (!entry) return;
  clearTimeout(entry.timer);
  if (entry.running) return entry.running;
  if (!entry.dirty) return;
  entry.running = (async () => {
    try {
      while (entry.dirty) {
        const revision = entry.revision;
        form.querySelector("[data-save-status]").textContent = "Сохраняется…";
        form.querySelector("[data-save-retry]").classList.add("hidden");
        await sendProductForm(form);
        entry.dirty = revision !== entry.revision;
      }
      form.querySelector("[data-save-status]").textContent = "Сохранено";
    } catch (error) {
      form.querySelector("[data-save-status]").textContent = `Не удалось сохранить: ${error.message}`;
      form.querySelector("[data-save-retry]").classList.remove("hidden");
      throw error;
    } finally {
      entry.running = null;
    }
  })();
  return entry.running;
}

async function sendProductForm(form) {
  const item = state.session.items.find((value) => value.id === form.dataset.productForm);
  if (!item) return;
  const data = new FormData(form);
  const updated = await api(`/api/intake/sessions/${state.session.id}/items/${item.id}`, {
    method: "PATCH",
    body: JSON.stringify({
      category_id: nullableText(data.get("category_id")),
      product_title: nullableText(data.get("product_title")),
      product_description: nullableText(data.get("product_description")),
    }),
  });
  state.session.items = state.session.items.map((value) => value.id === updated.id ? updated : value);
  // Refresh only backend-derived hints: never replace unsaved Variant form inputs.
  updateItemRequirements(updated);
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
  const productForms = [...document.querySelectorAll("[data-product-form]")];
  const forms = [...document.querySelectorAll("[data-item-form]")];
  await Promise.all(productForms.map(persistProductForm));
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
    await loadIntakeAqsi();
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
  const aqsiRows = state.result.items.map((mapping) => {
    const source = state.session.items.find((item) => item.id === mapping.item_id);
    const draftRoot = source?.draft_product_item_id ? state.session.items.find((item) => item.id === source.draft_product_item_id) : null;
    const display = source ? state.itemDisplay.get(source.id) : null;
    const title = source?.product_title || draftRoot?.product_title || display?.product?.title || "Товар";
    const variant = source?.variant_title || display?.variant?.title || "";
    const publication = state.intakeAqsi.get(mapping.variant_id);
    const statusText = publication ? aqsiStatusText(publication) : "Не отправлен";
    return `<div class="session-row"><span><strong>${escapeHtml(title)}${variant ? ` · ${escapeHtml(variant)}` : ""}</strong><br><span class="muted small">${escapeHtml(statusText)}</span></span></div>`;
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
      <div class="attention-list"><h3>AQSI</h3>${aqsiRows}<button class="button full" id="publish-intake-aqsi">Отправить готовые товары в AQSI</button><button class="button ghost full" id="refresh-intake-aqsi">Обновить статусы</button></div>
      <button class="button full" id="finish-home" style="margin-top:20px">Готово</button>
    </div>
  </div>`;
  bindTopbar();
  document.querySelector("#finish-home").addEventListener("click", loadHome);
  document.querySelector("#publish-intake-aqsi").addEventListener("click", publishIntakeAqsi);
  document.querySelector("#refresh-intake-aqsi").addEventListener("click", refreshIntakeAqsi);
}

function aqsiStatusText(publication) {
  if (publication.status === "published" && !publication.is_outdated) return "✓ Опубликован";
  if (publication.status === "failed") return `Ошибка: ${publication.last_error || "публикация отклонена"}`;
  if (publication.status === "published") return "Есть изменения — требуется повторная отправка";
  return "Отправлен, ожидаем подтверждения";
}

async function loadIntakeAqsi() {
  state.intakeAqsi = new Map();
  await Promise.all(state.result.items.map(async (mapping) => {
    try {
      const publication = await api(`/api/publishing/aqsi/variants/${mapping.variant_id}`);
      state.intakeAqsi.set(mapping.variant_id, publication);
    } catch (error) {
      if (error.message !== "AQSI publication not found.") throw error;
    }
  }));
}

async function publishIntakeAqsi() {
  const readyIds = state.result.readiness.filter((item) => item.is_ready).map((item) => item.variant_id);
  if (!readyIds.length) {
    showToast("Нет готовых к публикации товаров", true);
    return;
  }
  const results = await Promise.allSettled(readyIds.map((variantId) => api(`/api/publishing/aqsi/variants/${variantId}`, { method: "POST" })));
  await loadIntakeAqsi();
  renderResult();
  const failed = results.filter((result) => result.status === "rejected").length;
  showToast(failed ? `Не удалось отправить: ${failed}. Исправьте причины и повторите.` : "Все готовые товары отправлены в AQSI", failed > 0);
}

async function refreshIntakeAqsi() {
  try {
    await loadIntakeAqsi();
    renderResult();
    showToast("Статусы AQSI обновлены");
  } catch (error) { showToast(error.message, true); }
}

function normalizeCatalogState(value = {}) {
  const modes = ["sale", "rental", "all"];
  const statuses = ["active", "archived", "all"];
  const filters = ["missing_price", "missing_photo", "aqsi_problem", "out_of_stock"];
  const rentalFilters = ["all", "available", "needs_price", "never_rented", "paid_back", "high_expenses", "long_idle"];
  const mode = modes.includes(value.mode) ? value.mode : "sale";
  const sorts = catalogSortOptions(mode).map(([sort]) => sort);
  return {
    mode,
    status: statuses.includes(value.status) ? value.status : "active",
    query: String(value.query || "").trim(),
    categoryId: value.categoryId || "",
    supplierId: value.supplierId || "",
    attention: [...new Set(Array.isArray(value.attention) ? value.attention.filter((item) => filters.includes(item)) : [])],
    productFilter: mode === "rental" && rentalFilters.includes(value.productFilter) ? value.productFilter : "all",
    sort: sorts.includes(value.sort) ? value.sort : catalogDefaultSort(mode),
  };
}

function catalogDefaultSort(mode) {
  return mode === "rental" ? "title" : "newest";
}

function catalogSortOptions(mode) {
  if (mode === "rental") return [
    ["title", "По названию"],
    ["revenue", "По доходу от аренды"],
    ["rental_count", "По количеству аренд"],
    ["profit", "По результату аренды"],
    ["last_rental", "По последней аренде"],
  ];
  return [
    ["newest", "Сначала новые"],
    ["oldest", "Сначала старые"],
    ["title", "По названию"],
    ["price_asc", "Цена: сначала дешевле"],
    ["price_desc", "Цена: сначала дороже"],
    ["stock_asc", "Остаток: сначала меньше"],
    ["stock_desc", "Остаток: сначала больше"],
  ];
}

function catalogUrlParams(value, includeDefaults = true) {
  const catalog = normalizeCatalogState(value);
  const params = new URLSearchParams();
  if (includeDefaults || catalog.mode !== "sale") params.set("mode", catalog.mode);
  if (includeDefaults || catalog.status !== "active") params.set("status", catalog.status);
  if (catalog.query) params.set("query", catalog.query);
  if (catalog.categoryId) params.set("category_id", catalog.categoryId);
  if (catalog.supplierId) params.set("supplier_id", catalog.supplierId);
  catalog.attention.forEach((filter) => params.append("attention", filter));
  if (includeDefaults || catalog.productFilter !== "all") params.set("product_filter", catalog.productFilter);
  if (includeDefaults || catalog.sort !== catalogDefaultSort(catalog.mode)) params.set("sort", catalog.sort);
  return params;
}

async function openOperationsCatalog(options = {}) {
  try {
    const catalog = normalizeCatalogState(options);
    state.operations.catalog = catalog;
    recordRoute("catalog", catalog);
    logicalParent = () => loadHome();
    const params = catalogUrlParams(catalog);
    [state.operations.products, state.categories, state.suppliers] = await Promise.all([
      api(`/api/operations/catalog/products?${params}`),
      api("/api/catalog/categories"),
      api("/api/purchasing/suppliers"),
    ]);
    renderOperationsCatalog(catalog);
  } catch (error) { showToast(error.message, true); }
}

function renderOperationsCatalog(catalog) {
  document.body.classList.remove("catalog-drawer-open");
  const rows = state.operations.products.length
    ? state.operations.products.map((product) => renderCatalogProductCard(product, catalog)).join("")
    : '<div class="empty">Товары не найдены</div>';
  const selectedCategory = state.categories.find((category) => category.id === catalog.categoryId);
  const categoryLabel = selectedCategory?.title || "Все";
  root.innerHTML = `<div class="shell catalog-shell">
    ${topbar(true)}
    <div class="catalog-heading"><div><p class="eyebrow">Каталог</p><h1>Товары</h1></div><button class="button" id="catalog-new-product" type="button">＋ Новый товар</button></div>
    ${renderCatalogSalesContext(catalog)}
    <div class="catalog-workspace">
      <aside class="catalog-sidebar" aria-label="Навигация и фильтры каталога">
        ${renderCatalogCategoryNavigation(catalog)}
        <button class="button secondary full catalog-category-entry" data-open-category-create type="button">＋ Категория</button>
        <button class="button ghost full category-management-entry" data-open-category-management type="button">Управление категориями</button>
        <div class="divider"></div>
        ${renderCatalogFilters(catalog, false)}
      </aside>
      <div class="catalog-main">
        <nav class="catalog-modes" aria-label="Режим каталога">
          ${catalogModeButton("sale", "Продажа", catalog.mode)}
          ${catalogModeButton("rental", "Аренда", catalog.mode)}
          ${catalogModeButton("all", "Все", catalog.mode)}
        </nav>
        <form class="search-row catalog-search" id="operations-product-search" role="search">
          <label class="visually-hidden" for="catalog-query">Название, SKU или штрихкод</label>
          <input id="catalog-query" name="query" value="${escapeHtml(catalog.query)}" placeholder="Название, SKU или штрихкод" autocomplete="off">
          <button class="button" type="submit">Найти</button>
        </form>
        <div class="catalog-mobile-controls">
          <button class="button secondary" id="open-category-drawer" type="button">Категория: ${escapeHtml(categoryLabel)} <span aria-hidden="true">⌄</span></button>
          <button class="button secondary" id="open-filter-drawer" type="button">Фильтры${catalogFilterCount(catalog) ? ` · ${catalogFilterCount(catalog)}` : ""}</button>
        </div>
        <section class="catalog-results" aria-label="Список товаров">
          <div class="catalog-results-head"><span class="muted small">Найдено: ${state.operations.products.length}</span>${renderCatalogSort(catalog)}</div>
          <div class="session-list">${rows}</div>
        </section>
      </div>
    </div>
    <dialog class="catalog-drawer" id="catalog-category-dialog" aria-labelledby="category-dialog-title">
      <div class="catalog-drawer-head"><h2 id="category-dialog-title">Категории</h2><button class="drawer-close" type="button" aria-label="Закрыть">×</button></div>
      ${renderCatalogCategoryNavigation(catalog)}
      <button class="button secondary full catalog-category-entry" data-open-category-create type="button">＋ Категория</button>
      <button class="button ghost full category-management-entry" data-open-category-management type="button">Управление категориями</button>
    </dialog>
    <dialog class="catalog-drawer" id="catalog-filter-dialog" aria-labelledby="filter-dialog-title">
      <form id="catalog-mobile-filter-form">
        <div class="catalog-drawer-head"><h2 id="filter-dialog-title">Фильтры</h2><button class="drawer-close" type="button" aria-label="Закрыть">×</button></div>
        ${renderCatalogFilters(catalog, true)}
        <div class="catalog-drawer-actions"><button class="button secondary" id="catalog-filters-reset" type="button">Сбросить</button><button class="button" type="submit">Показать товары</button></div>
      </form>
    </dialog>
    ${renderCategoryCreateDialog()}
    <dialog class="catalog-action-dialog" id="catalog-action-dialog"><div id="catalog-action-content"></div></dialog>
  </div>`;
  bindTopbar();
  hydrateImages();
  bindImagePreviews();
  bindCatalogShell(catalog);
}

function renderCatalogProductCard(product, catalog) {
  const mode = catalog.mode;
  const visibleVariants = catalogVariantsForMode(product.card_variants, mode);
  const variants = visibleVariants.length
    ? visibleVariants.map((variant) => renderCatalogVariantRow(
      variant,
      mode,
      product.is_active && !product.is_archived,
    )).join("")
    : `<span class="catalog-card-empty muted">${catalog.status === "archived" ? "Нет архивных вариантов" : "Нет активных вариантов"}</span>`;
  const category = product.is_archived
    ? `<span class="chip category-chip">${escapeHtml(product.category_label)}</span>`
    : `<button class="chip category-chip category-action" data-change-product-category="${product.id}" type="button" title="Изменить категорию: ${escapeHtml(product.category_label)}">${escapeHtml(product.category_label)} <span aria-hidden="true">⌄</span></button>`;
  return `<article class="catalog-row catalog-product-row catalog-product-card ${product.is_archived ? "archived" : ""}">
    ${product.primary_image_id ? `<img class="catalog-photo" data-image-id="${product.primary_image_id}" alt="${escapeHtml(product.title)}">` : '<span class="catalog-photo photo-placeholder">◎</span>'}
    <span class="catalog-card-body">
      <span class="catalog-card-heading"><button class="catalog-card-open" data-open-product="${product.id}" type="button"><strong class="catalog-card-title">${escapeHtml(product.title)}</strong>${product.is_test ? '<span class="chip test-chip">ТЕСТ</span>' : ""}${product.is_archived ? '<span class="chip archive-chip">АРХИВ</span>' : ""}</button>${category}</span>
      <span class="catalog-variant-list">${variants}</span>
      ${renderCatalogEconomicsSummary(product, mode)}
      ${product.is_archived ? `<span class="catalog-card-lifecycle-actions"><button class="button secondary compact" data-restore-product="${product.id}" data-restore-title="${escapeHtml(product.title)}" data-restore-context="catalog" type="button">Восстановить товар</button></span>` : ""}
    </span>
    <button class="catalog-card-chevron" data-open-product="${product.id}" type="button" aria-label="Открыть товар ${escapeHtml(product.title)}">›</button>
  </article>`;
}

function catalogVariantsForMode(variants, mode) {
  if (mode === "rental") return variants.filter((variant) => variant.rental_row_visible);
  if (mode === "sale") {
    const saleVariants = variants.filter((variant) => variant.sale_row_visible);
    return saleVariants.length ? saleVariants : variants.filter((variant) => variant.rental_row_visible);
  }
  return variants;
}

function renderCatalogVariantRow(variant, mode, productIsOperational = true) {
  const commercialRows = [];
  if (mode !== "rental" && variant.sale_row_visible) {
    commercialRows.push(renderCatalogSaleRow(
      variant,
      productIsOperational && !variant.is_archived,
    ));
  }
  if (mode !== "sale" && variant.rental_row_visible) commercialRows.push(renderCatalogRentalRow(variant));
  if (mode === "sale" && !variant.sale_row_visible && variant.rental_row_visible) commercialRows.push(renderCatalogRentalRow(variant));
  return `<span class="catalog-variant-row ${variant.is_archived ? "archived" : ""}">
    <span class="catalog-variant-identity">${variant.title ? `<strong>${escapeHtml(variant.title)}</strong>` : ""}${variant.is_archived ? '<span class="chip archive-chip">АРХИВНЫЙ ВАРИАНТ</span>' : ""}<span class="catalog-sku">${escapeHtml(variant.sku)}</span></span>
    <span class="catalog-commercial-rows">${commercialRows.join("")}</span>
    ${variant.is_archived ? `<span class="catalog-variant-lifecycle-actions"><button class="button secondary compact" data-restore-variant="${variant.id}" data-restore-context="catalog" type="button">Восстановить вариант</button></span>` : ""}
  </span>`;
}

function renderCatalogSaleRow(variant, sellable = true) {
  const quantity = Number(variant.sale_quantity);
  const quantityClass = quantity < 0 ? "negative" : quantity === 0 ? "zero" : "positive";
  const price = variant.current_retail_price === null
    ? '<strong class="catalog-price missing">Цена не указана</strong>'
    : `<strong class="catalog-price">${formatMoney(variant.current_retail_price)}</strong>`;
  const add = sellable
    ? `<button class="catalog-sale-add" data-add-sale-variant="${variant.id}" type="button" ${variant.current_retail_price === null ? 'disabled title="У товара не указана цена"' : 'title="Добавить в продажу"'} aria-label="Добавить вариант в продажу">＋</button>`
    : "";
  return `<span class="catalog-commercial-row sale"><span class="catalog-channel-label">Продажа</span>${price}<span class="catalog-quantity ${quantityClass}">${formatQuantity(variant.sale_quantity)}</span>${renderCatalogCardAqsiStatus(variant)}${add}</span>`;
}

function renderCatalogRentalRow(variant) {
  const price = variant.current_rental_price === null
    ? '<strong class="catalog-price missing">Цена аренды не указана</strong>'
    : `<strong class="catalog-price">${formatMoney(variant.current_rental_price)}</strong>`;
  return `<span class="catalog-commercial-row rental"><span class="catalog-channel-label">Аренда</span>${price}<span class="catalog-quantity">${variant.rental_asset_count} экз.</span></span>`;
}

function renderCatalogCardAqsiStatus(variant) {
  if (variant.aqsi_status === "failed") return '<span class="chip danger catalog-aqsi">⚠ AQSI: ошибка</span>';
  if (variant.aqsi_status === "disabled") return '<span class="chip catalog-aqsi">AQSI отключено</span>';
  if (variant.aqsi_status === "published" && variant.aqsi_is_current) return '<span class="chip good catalog-aqsi">✓ AQSI</span>';
  if (variant.aqsi_status === "published") return '<span class="chip warn catalog-aqsi">⚠ Требует обновления</span>';
  if (["pending", "accepted"].includes(variant.aqsi_status)) return '<span class="chip catalog-aqsi">● Публикуется</span>';
  return '<span class="chip warn catalog-aqsi">⚠ Не опубликовано</span>';
}

function renderCatalogEconomicsSummary(product, mode) {
  if (mode === "sale" || !product.rental_economics_applicable) return "";
  const metrics = [
    `<span class="catalog-economics-metric rental"><span class="muted">Результат аренды:</span> <strong>${formatMoney(product.economics.profit)}</strong></span>`,
  ];
  const details = mode === "rental"
    ? `<span>Доход ${formatMoney(product.economics.revenue)}</span><span>${product.economics.rental_count} аренд</span><span>${product.available_asset_count} доступно</span>`
    : `<span>${product.economics.rental_count} аренд · ${product.available_asset_count} доступно</span>`;
  return `<span class="catalog-economics-summary ${mode}"><span class="catalog-economics-metrics">${metrics.join("")}</span>${details}</span>`;
}

function catalogModeButton(value, label, active) {
  return `<button class="catalog-mode ${value === active ? "active" : ""}" data-catalog-mode="${value}" type="button" aria-pressed="${value === active}">${label}</button>`;
}

function catalogFilterCount(catalog) {
  return catalog.attention.length + (catalog.status !== "active" ? 1 : 0) + (catalog.supplierId ? 1 : 0) + (catalog.productFilter !== "all" ? 1 : 0);
}

function renderCatalogCategoryNavigation(catalog) {
  const categories = state.categories.filter((category) => category.is_active && !category.is_archived);
  const children = new Map();
  categories.forEach((category) => {
    const parent = categories.some((item) => item.id === category.parent_id) ? category.parent_id : null;
    children.set(parent, [...(children.get(parent) || []), category]);
  });
  const branch = (parentId, depth = 0, visited = new Set()) => (children.get(parentId) || []).map((category) => {
    if (visited.has(category.id)) return "";
    const nextVisited = new Set(visited).add(category.id);
    return `<li><button class="category-link ${catalog.categoryId === category.id ? "active" : ""}" data-category-id="${category.id}" type="button" style="--category-depth:${Math.min(depth, 3)}" aria-pressed="${catalog.categoryId === category.id}">${escapeHtml(category.title)}</button>${children.has(category.id) ? `<ul>${branch(category.id, depth + 1, nextVisited)}</ul>` : ""}</li>`;
  }).join("");
  return `<div class="catalog-category-nav"><h2>Категории</h2><ul><li><button class="category-link ${catalog.categoryId ? "" : "active"}" data-category-id="" type="button" aria-pressed="${!catalog.categoryId}">Все товары</button></li>${branch(null)}</ul></div>`;
}

function categoryPathLabel(category, categories) {
  const byId = new Map(categories.map((item) => [item.id, item]));
  const parts = [];
  const visited = new Set();
  let cursor = category;
  while (cursor && !visited.has(cursor.id)) {
    visited.add(cursor.id);
    parts.unshift(cursor.title);
    cursor = cursor.parent_id ? byId.get(cursor.parent_id) : null;
  }
  return parts.join(" › ");
}

function categoryDescendantIds(categoryId, categories) {
  const descendants = new Set();
  let changed = true;
  while (changed) {
    changed = false;
    categories.forEach((category) => {
      if (category.parent_id === categoryId || descendants.has(category.parent_id)) {
        if (!descendants.has(category.id)) {
          descendants.add(category.id);
          changed = true;
        }
      }
    });
  }
  return descendants;
}

function flattenCategoryTree(categories) {
  const availableIds = new Set(categories.map((category) => category.id));
  const children = new Map();
  const ordered = [...categories].sort((left, right) => (
    Number(left.sort_order || 0) - Number(right.sort_order || 0)
    || left.title.localeCompare(right.title, "ru")
  ));
  ordered.forEach((category) => {
    const parentId = availableIds.has(category.parent_id) ? category.parent_id : null;
    children.set(parentId, [...(children.get(parentId) || []), category]);
  });
  const result = [];
  const emitted = new Set();
  const append = (parentId, depth = 0, branch = new Set()) => {
    (children.get(parentId) || []).forEach((category) => {
      if (branch.has(category.id) || emitted.has(category.id)) return;
      emitted.add(category.id);
      result.push({ category, depth });
      append(category.id, depth + 1, new Set(branch).add(category.id));
    });
  };
  append(null);
  ordered.filter((category) => !emitted.has(category.id)).forEach((category) => {
    result.push({ category, depth: 0 });
  });
  return result;
}

function renderCategoryOptions(categories, options = {}) {
  const selectedId = options.selectedId || "";
  const excludedIds = options.excludedIds || new Set();
  const activeCategories = categories.filter((category) => (
    category.is_active && !category.is_archived && !excludedIds.has(category.id)
  ));
  const active = flattenCategoryTree(activeCategories)
    .map(({ category }) => ({ category, label: categoryPathLabel(category, categories) }));
  const root = options.includeRoot === false
    ? ""
    : `<option value="" ${selectedId ? "" : "selected"}>Без родительской категории</option>`;
  return root + active.map(({ category, label }) => `<option value="${category.id}" ${category.id === selectedId ? "selected" : ""}>${escapeHtml(label)}</option>`).join("");
}

function renderCategoryCreateDialog() {
  return `<dialog class="catalog-drawer catalog-category-create" id="catalog-category-create-dialog" aria-labelledby="category-create-title">
    <form id="catalog-category-create-form">
      <div class="catalog-drawer-head"><h2 id="category-create-title">Новая категория</h2><button class="drawer-close" type="button" aria-label="Закрыть">×</button></div>
      <div class="field"><label for="catalog-category-title">Название</label><input id="catalog-category-title" name="title" maxlength="255" autocomplete="off" required autofocus></div>
      <div class="field"><label for="catalog-category-parent">Родительская категория <span class="muted">(необязательно)</span></label><select id="catalog-category-parent" name="parent_id">${renderCategoryOptions(state.categories)}</select></div>
      <div class="catalog-drawer-actions"><button class="button secondary" data-category-create-cancel type="button">Отмена</button><button class="button" type="submit">Создать</button></div>
    </form>
  </dialog>`;
}

function renderCatalogFilters(catalog, mobile) {
  const prefix = mobile ? "mobile" : "desktop";
  const attention = [
    ["missing_price", "Нет цены"],
    ["missing_photo", "Нет фото"],
    ["aqsi_problem", "AQSI: проблема / не опубликовано"],
    ["out_of_stock", "Остаток ≤ 0"],
  ].map(([value, label]) => `<label class="catalog-check"><input type="checkbox" name="attention" value="${value}" ${catalog.attention.includes(value) ? "checked" : ""} ${mobile ? "" : "data-desktop-attention"}><span>${label}</span></label>`).join("");
  const suppliers = state.suppliers.filter((supplier) => supplier.is_active).map((supplier) => `<option value="${supplier.id}" ${supplier.id === catalog.supplierId ? "selected" : ""}>${escapeHtml(supplier.display_name || supplier.name)}</option>`).join("");
  const rental = catalog.mode === "rental" ? `<fieldset class="catalog-filter-group"><legend>Аренда</legend>
    ${catalogRentalFilter("all", "Все арендные товары", catalog.productFilter, prefix)}
    ${catalogRentalFilter("available", "Есть доступные", catalog.productFilter, prefix)}
    ${catalogRentalFilter("never_rented", "Не сдавался", catalog.productFilter, prefix)}
    ${catalogRentalFilter("paid_back", "Окупился", catalog.productFilter, prefix)}
    ${catalogRentalFilter("high_expenses", "Высокие расходы", catalog.productFilter, prefix)}
    ${catalogRentalFilter("long_idle", "Давно не сдавался", catalog.productFilter, prefix)}
  </fieldset>` : "";
  return `<fieldset class="catalog-filter-group"><legend>Статус</legend>
      ${catalogStatusFilter("active", "Активные", catalog.status, prefix)}
      ${catalogStatusFilter("archived", "Архивные", catalog.status, prefix)}
      ${catalogStatusFilter("all", "Все", catalog.status, prefix)}
    </fieldset>
    <fieldset class="catalog-filter-group"><legend>Требуют внимания</legend>${attention}</fieldset>
    <div class="field"><label for="${prefix}-catalog-supplier">Поставщик</label><select id="${prefix}-catalog-supplier" name="supplier_id" ${mobile ? "" : "data-desktop-supplier"}><option value="">Все</option>${suppliers}</select></div>${rental}`;
}

function catalogStatusFilter(value, label, active, prefix) {
  return `<label class="catalog-check"><input type="radio" name="status" value="${value}" ${value === active ? "checked" : ""} ${prefix === "desktop" ? "data-desktop-status" : ""}><span>${label}</span></label>`;
}

function catalogRentalFilter(value, label, active, prefix) {
  return `<label class="catalog-check"><input type="radio" name="product_filter" value="${value}" ${value === active ? "checked" : ""} ${prefix === "desktop" ? "data-desktop-rental-filter" : ""}><span>${label}</span></label>`;
}

function renderCatalogSort(catalog) {
  const options = catalogSortOptions(catalog.mode).map(([value, label]) => `<option value="${value}" ${catalog.sort === value ? "selected" : ""}>${label}</option>`).join("");
  return `<div class="field sort-field"><label for="operations-product-sort">Сортировка</label><select id="operations-product-sort">${options}</select></div>`;
}

function openCatalogDrawer(id) {
  const dialog = document.querySelector(id);
  document.body.classList.add("catalog-drawer-open");
  dialog.showModal();
  dialog.querySelector?.("[autofocus]")?.focus();
}

function bindCatalogShell(catalog) {
  bindCatalogSalesContext();
  document.querySelector("#catalog-new-product").addEventListener("click", () => startSession());
  document.querySelectorAll("[data-catalog-mode]").forEach((button) => {
    button.addEventListener("click", () => openOperationsCatalog({ ...catalog, mode: button.dataset.catalogMode, productFilter: "all" }));
  });
  document.querySelector("#operations-product-search").addEventListener("submit", (event) => {
    event.preventDefault();
    openOperationsCatalog({ ...catalog, query: String(new FormData(event.currentTarget).get("query") || "").trim() });
  });
  document.querySelectorAll("[data-category-id]").forEach((button) => {
    button.addEventListener("click", () => openOperationsCatalog({ ...catalog, categoryId: button.dataset.categoryId }));
  });
  document.querySelectorAll("[data-desktop-attention]").forEach((input) => {
    input.addEventListener("change", () => {
      const attention = [...document.querySelectorAll("[data-desktop-attention]:checked")].map((item) => item.value);
      openOperationsCatalog({ ...catalog, attention });
    });
  });
  document.querySelectorAll("[data-desktop-status]").forEach((input) => {
    input.addEventListener("change", () => openOperationsCatalog({ ...catalog, status: input.value }));
  });
  document.querySelector("[data-desktop-supplier]").addEventListener("change", (event) => openOperationsCatalog({ ...catalog, supplierId: event.target.value }));
  document.querySelectorAll("[data-desktop-rental-filter]").forEach((input) => {
    input.addEventListener("change", () => openOperationsCatalog({ ...catalog, productFilter: input.value }));
  });
  document.querySelector("#operations-product-sort").addEventListener("change", (event) => openOperationsCatalog({ ...catalog, sort: event.target.value }));
  document.querySelectorAll("[data-open-product]").forEach((button) => {
    button.addEventListener("click", () => openOperationsProduct(button.dataset.openProduct));
  });
  document.querySelectorAll("[data-add-sale-variant]").forEach((button) => {
    button.addEventListener("click", () => addVariantToActiveSale(button.dataset.addSaleVariant));
  });
  document.querySelectorAll("[data-change-product-category]").forEach((button) => button.addEventListener("click", () => openCatalogCategoryPicker(button.dataset.changeProductCategory)));
  document.querySelectorAll("[data-restore-product]").forEach((button) => button.addEventListener("click", () => openCatalogRestoreConfirmation(button.dataset.restoreProduct, button.dataset.restoreTitle, button.dataset.restoreContext)));
  document.querySelectorAll("[data-restore-variant]").forEach((button) => button.addEventListener("click", () => executeCatalogRestore("variant", button.dataset.restoreVariant, button, button.dataset.restoreContext)));
  document.querySelector("#open-category-drawer").addEventListener("click", () => openCatalogDrawer("#catalog-category-dialog"));
  document.querySelector("#open-filter-drawer").addEventListener("click", () => openCatalogDrawer("#catalog-filter-dialog"));
  document.querySelectorAll(".catalog-drawer").forEach((dialog) => {
    dialog.querySelector(".drawer-close").addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
    dialog.addEventListener("close", () => document.body.classList.remove("catalog-drawer-open"));
  });
  document.querySelectorAll("[data-open-category-create]").forEach((button) => {
    button.addEventListener("click", () => {
      state.operations.categoryManagement = null;
      const categoryDrawer = document.querySelector("#catalog-category-dialog");
      if (categoryDrawer.open) {
        categoryDrawer.addEventListener("close", () => openCatalogDrawer("#catalog-category-create-dialog"), { once: true });
        categoryDrawer.close();
      } else {
        openCatalogDrawer("#catalog-category-create-dialog");
      }
    });
  });
  document.querySelectorAll("[data-open-category-management]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelector("#catalog-category-dialog")?.close();
      openCategoryManagement();
    });
  });
  document.querySelector("[data-category-create-cancel]").addEventListener("click", () => document.querySelector("#catalog-category-create-dialog").close());
  document.querySelector("#catalog-category-create-form").addEventListener("submit", (event) => createCatalogCategory(event, catalog));
  document.querySelector("#catalog-mobile-filter-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    openOperationsCatalog({ ...catalog, status: data.get("status") || "active", attention: data.getAll("attention"), supplierId: data.get("supplier_id") || "", productFilter: data.get("product_filter") || "all" });
  });
  document.querySelector("#catalog-filters-reset").addEventListener("click", () => openOperationsCatalog({ ...catalog, status: "active", attention: [], supplierId: "", productFilter: "all" }));
}

function openCatalogActionDialog(content) {
  const dialog = document.querySelector("#catalog-action-dialog");
  document.querySelector("#catalog-action-content").innerHTML = content;
  document.body.classList.add("catalog-drawer-open");
  if (!dialog.open) dialog.showModal();
  dialog.addEventListener("close", () => document.body.classList.remove("catalog-drawer-open"), { once: true });
  return dialog;
}

function openCatalogRestoreConfirmation(productId, title, context = "catalog") {
  const dialog = openCatalogActionDialog(`<div class="catalog-restore-dialog">
    <div class="catalog-dialog-head"><div><p class="eyebrow">Восстановление из архива</p><h2>Восстановить «${escapeHtml(title)}»?</h2></div><button class="drawer-close" data-action-cancel type="button" aria-label="Закрыть">×</button></div>
    <p>Товар снова появится в рабочем каталоге.</p>
    <p class="muted small">Состояние каждого варианта, остатки, цены и история останутся без изменений.</p>
    <div class="catalog-dialog-actions"><button class="button secondary" data-action-cancel type="button">Отмена</button><button class="button" data-confirm-restore-product type="button">Восстановить</button></div>
  </div>`);
  dialog.querySelectorAll("[data-action-cancel]").forEach((button) => button.addEventListener("click", () => dialog.close()));
  dialog.querySelector("[data-confirm-restore-product]").addEventListener("click", (event) => executeCatalogRestore("product", productId, event.currentTarget, context));
}

async function executeCatalogRestore(entityType, entityId, button, context = "catalog") {
  button.disabled = true;
  const collection = entityType === "product" ? "products" : "variants";
  try {
    await api(`/api/catalog/${collection}/${entityId}/restore`, { method: "POST" });
    document.querySelector("#catalog-action-dialog")?.close();
    if (entityType === "variant" && context === "product") {
      await openOperationsProduct(state.operations.product.id);
    } else {
      await openOperationsCatalog(state.operations.catalog || {});
    }
    showToast(entityType === "product" ? "Товар восстановлен" : "Вариант восстановлен");
  } catch (error) {
    showToast(error.message, true);
    button.disabled = false;
  }
}

function openCatalogCategoryPicker(productId) {
  const product = state.operations.products.find((item) => item.id === productId);
  if (!product) return;
  const activeCategories = state.categories.filter((category) => category.is_active && !category.is_archived);
  const currentIsActive = activeCategories.some((category) => category.id === product.category_id);
  const currentOption = currentIsActive ? "" : `<option value="" selected disabled>${escapeHtml(product.category_label)} · недоступна</option>`;
  const options = currentOption + renderCategoryOptions(activeCategories, {
    selectedId: product.category_id,
    includeRoot: false,
  });
  const dialog = openCatalogActionDialog(`<form id="catalog-category-picker-form">
    <div class="catalog-dialog-head"><div><p class="eyebrow">${escapeHtml(product.title)}</p><h2>Категория товара</h2></div><button class="drawer-close" data-action-cancel type="button" aria-label="Закрыть">×</button></div>
    <div class="field"><label for="catalog-card-category">Активная категория</label><select id="catalog-card-category" name="category_id" required autofocus>${options}</select></div>
    <div class="catalog-dialog-actions"><button class="button secondary" data-action-cancel type="button">Отмена</button><button class="button" type="submit">Сохранить</button></div>
  </form>`);
  dialog.querySelectorAll("[data-action-cancel]").forEach((button) => button.addEventListener("click", () => dialog.close()));
  dialog.querySelector("#catalog-category-picker-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      await api(`/api/catalog/products/${productId}`, {
        method: "PATCH",
        body: JSON.stringify({ category_id: new FormData(event.currentTarget).get("category_id") }),
      });
      dialog.close();
      await openOperationsCatalog(state.operations.catalog || {});
      showToast("Категория обновлена");
    } catch (error) {
      showToast(error.message, true);
      button.disabled = false;
    }
  });
}

async function openCatalogDeletePreflight(entityType, entityId) {
  try {
    const collection = entityType === "product" ? "products" : "variants";
    const preflight = await api(`/api/catalog/${collection}/${entityId}/hard-delete-preflight`);
    renderCatalogDeletePreflight(preflight);
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderCatalogDependencyList(items) {
  return items.length
    ? `<ul class="catalog-dependency-list">${items.map((item) => `<li><strong>${item.count}</strong> · ${escapeHtml(item.label)}</li>`).join("")}</ul>`
    : "";
}

function renderCatalogDeletePreflight(preflight) {
  const blocked = !preflight.can_delete;
  const entityLabel = preflight.entity_type === "product" ? "товар" : "вариант";
  const lastVariant = preflight.entity_type === "variant" && preflight.is_last_variant;
  const dialog = openCatalogActionDialog(`<div class="catalog-delete-dialog">
    <div class="catalog-dialog-head"><div><p class="eyebrow">Безвозвратное действие</p><h2>${blocked ? `Нельзя удалить ${entityLabel} навсегда` : `Удалить «${escapeHtml(preflight.title)}» навсегда?`}</h2></div><button class="drawer-close" data-action-cancel type="button" aria-label="Закрыть">×</button></div>
    ${blocked ? `<p>С объектом связана защищённая бизнес-история:</p>${renderCatalogDependencyList(preflight.blockers)}` : `<p>Будет удалено:</p>${renderCatalogDependencyList(preflight.will_delete)}<p class="danger-note">Отменить это действие будет невозможно.</p>`}
    ${preflight.warnings.map((warning) => `<p class="catalog-delete-warning">⚠ ${escapeHtml(warning)}</p>`).join("")}
    ${lastVariant ? '<p class="catalog-delete-warning">Это последний вариант товара. Товар без вариантов допустим и останется в каталоге.</p>' : ""}
    <div class="catalog-dialog-actions ${lastVariant && !blocked ? "three" : ""}">
      <button class="button secondary" data-action-cancel type="button">Отмена</button>
      ${blocked ? '<button class="button danger" data-archive-entity type="button">Архивировать вместо удаления</button>' : `<button class="button danger" data-confirm-hard-delete type="button">${lastVariant ? "Удалить только вариант" : "Удалить навсегда"}</button>${lastVariant ? '<button class="button danger outline" data-delete-whole-product type="button">Удалить товар целиком</button>' : ""}`}
    </div>
  </div>`);
  dialog.querySelectorAll("[data-action-cancel]").forEach((button) => button.addEventListener("click", () => dialog.close()));
  dialog.querySelector("[data-confirm-hard-delete]")?.addEventListener("click", (event) => executeCatalogDelete(preflight, event.currentTarget, true));
  dialog.querySelector("[data-archive-entity]")?.addEventListener("click", (event) => executeCatalogDelete(preflight, event.currentTarget, false));
  dialog.querySelector("[data-delete-whole-product]")?.addEventListener("click", () => {
    dialog.close();
    openCatalogDeletePreflight("product", preflight.product_id);
  });
}

async function executeCatalogDelete(preflight, button, hardDelete) {
  button.disabled = true;
  const collection = preflight.entity_type === "product" ? "products" : "variants";
  const suffix = hardDelete ? "/hard" : "";
  try {
    await api(`/api/catalog/${collection}/${preflight.entity_id}${suffix}`, { method: "DELETE" });
    document.querySelector("#catalog-action-dialog")?.close();
    if (preflight.entity_type === "variant") {
      await openOperationsProduct(preflight.product_id);
    } else {
      await openOperationsCatalog(state.operations.catalog || {});
    }
    showToast(hardDelete ? "Удалено навсегда" : "Перемещено в архив");
  } catch (error) {
    if (error.status === 409 && error.detail && typeof error.detail === "object") {
      renderCatalogDeletePreflight(error.detail);
      return;
    }
    showToast(error.message, true);
    button.disabled = false;
  }
}

async function openCatalogTestDataPreflight(productId, action) {
  try {
    const preflight = await api(`/api/catalog/products/${productId}/test-data-preflight`);
    renderCatalogTestDataPreflight(preflight, action);
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderCatalogTestDataPreflight(preflight, action) {
  const classify = action === "classify";
  const allowed = classify ? preflight.can_classify : preflight.can_purge;
  const title = classify ? "Пометить как тестовые данные?" : "Удалить тестовые данные навсегда?";
  const dialog = openCatalogActionDialog(`<div class="catalog-delete-dialog">
    <div class="catalog-dialog-head"><div><p class="eyebrow">Административная операция</p><h2>${title}</h2><p><strong>${escapeHtml(preflight.title)}</strong></p></div><button class="drawer-close" data-action-cancel type="button" aria-label="Закрыть">×</button></div>
    ${allowed ? `<p>${classify ? "Граф будет явно классифицирован как TEST:" : "Будет удалено навсегда:"}</p>${renderCatalogDependencyList(preflight.dependencies)}` : `<p>Операция заблокирована зависимостями:</p>${renderCatalogDependencyList(preflight.blockers)}`}
    ${preflight.warnings.map((warning) => `<p class="catalog-delete-warning">⚠ ${escapeHtml(warning)}</p>`).join("")}
    ${classify ? '<p class="muted small">Классификация не удаляет данные. Перед будущей очисткой сервер снова проверит весь граф.</p>' : '<p class="danger-note">Эти данные не попадут в архив. Отменить удаление будет невозможно.</p>'}
    <div class="catalog-dialog-actions"><button class="button secondary" data-action-cancel type="button">Отмена</button>${allowed ? `<button class="button danger" data-confirm-test-data type="button">${classify ? "Пометить как тестовые данные" : "Удалить тест полностью"}</button>` : ""}</div>
  </div>`);
  dialog.querySelectorAll("[data-action-cancel]").forEach((button) => button.addEventListener("click", () => dialog.close()));
  dialog.querySelector("[data-confirm-test-data]")?.addEventListener("click", (event) => executeCatalogTestData(preflight, action, event.currentTarget));
}

async function executeCatalogTestData(preflight, action, button) {
  button.disabled = true;
  const endpoint = action === "classify" ? "classify-test-data" : "purge-test-data";
  try {
    await api(`/api/catalog/products/${preflight.product_id}/${endpoint}`, {
      method: "POST",
      body: JSON.stringify({ confirm: true }),
    });
    document.querySelector("#catalog-action-dialog")?.close();
    if (action === "classify") {
      await openOperationsProduct(preflight.product_id);
      showToast("Товар и его граф помечены как тестовые данные");
    } else {
      await openOperationsCatalog(state.operations.catalog || {});
      showToast("Тестовые данные удалены полностью");
    }
  } catch (error) {
    if (error.status === 409 && error.detail && typeof error.detail === "object") {
      renderCatalogTestDataPreflight(error.detail, action);
      return;
    }
    showToast(error.message, true);
    button.disabled = false;
  }
}

async function openCategoryManagement(status = "active") {
  try {
    const categories = await api("/api/catalog/categories?status=all");
    state.operations.categoryManagement = { status, categories };
    renderCategoryManagement();
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderCategoryManagement() {
  const management = state.operations.categoryManagement;
  if (!management) return;
  const visible = flattenCategoryTree(management.categories)
    .filter(({ category }) => management.status === "archived" ? category.is_archived : !category.is_archived)
    .map(({ category }) => ({ category, label: categoryPathLabel(category, management.categories) }));
  const rows = visible.length
    ? visible.map(({ category, label }) => `<article class="category-management-row ${category.is_archived ? "archived" : ""}">
        <div class="category-management-copy"><strong>${escapeHtml(category.title)}</strong><span class="muted small">${escapeHtml(label)}</span>${!category.is_active ? '<span class="chip warn">Недоступна для товаров</span>' : ""}</div>
        <div class="category-management-actions">
          ${category.is_archived
            ? `<button class="button secondary compact" data-restore-category="${category.id}" type="button">Восстановить</button>`
            : `<button class="button ghost compact" data-edit-category="${category.id}" type="button">Изменить</button><button class="button danger compact" data-archive-category="${category.id}" type="button">Архивировать</button>`}
        </div>
      </article>`).join("")
    : `<div class="empty">${management.status === "archived" ? "Архивных категорий нет" : "Активных категорий нет"}</div>`;
  const dialog = openCatalogActionDialog(`<section class="category-management">
    <div class="catalog-dialog-head"><div><p class="eyebrow">Справочник</p><h2>Управление категориями</h2></div><button class="drawer-close" data-category-management-close type="button" aria-label="Закрыть">×</button></div>
    <div class="category-management-tabs" role="tablist" aria-label="Статус категорий">
      <button class="filter-chip ${management.status === "active" ? "active" : ""}" data-category-management-status="active" type="button">Активные</button>
      <button class="filter-chip ${management.status === "archived" ? "active" : ""}" data-category-management-status="archived" type="button">Архивные</button>
    </div>
    <div class="category-management-list">${rows}</div>
    <button class="button secondary full" data-category-management-create type="button">＋ Категория</button>
  </section>`);
  dialog.querySelector("[data-category-management-close]").addEventListener("click", () => {
    state.operations.categoryManagement = null;
    dialog.close();
  });
  dialog.querySelectorAll("[data-category-management-status]").forEach((button) => {
    button.addEventListener("click", () => openCategoryManagement(button.dataset.categoryManagementStatus));
  });
  dialog.querySelector("[data-category-management-create]").addEventListener("click", () => {
    dialog.close();
    openCatalogDrawer("#catalog-category-create-dialog");
  });
  dialog.querySelectorAll("[data-edit-category]").forEach((button) => {
    button.addEventListener("click", () => openCategoryEdit(button.dataset.editCategory));
  });
  dialog.querySelectorAll("[data-archive-category]").forEach((button) => {
    button.addEventListener("click", () => openCategoryArchiveConfirmation(button.dataset.archiveCategory));
  });
  dialog.querySelectorAll("[data-restore-category]").forEach((button) => {
    button.addEventListener("click", () => executeCategoryRestore(button.dataset.restoreCategory, button));
  });
}

function openCategoryEdit(categoryId) {
  const management = state.operations.categoryManagement;
  const category = management?.categories.find((item) => item.id === categoryId);
  if (!category) return;
  const excludedIds = categoryDescendantIds(category.id, management.categories);
  excludedIds.add(category.id);
  const dialog = openCatalogActionDialog(`<form id="category-edit-form">
    <div class="catalog-dialog-head"><div><p class="eyebrow">Категория</p><h2>Изменить категорию</h2></div><button class="drawer-close" data-category-edit-cancel type="button" aria-label="Закрыть">×</button></div>
    <div class="field"><label for="category-edit-title">Название</label><input id="category-edit-title" name="title" value="${escapeHtml(category.title)}" maxlength="255" required autofocus></div>
    <div class="field"><label for="category-edit-parent">Родительская категория <span class="muted">(необязательно)</span></label><select id="category-edit-parent" name="parent_id">${renderCategoryOptions(management.categories, { selectedId: category.parent_id, excludedIds })}</select></div>
    <p class="muted small">Служебный адрес категории управляется Core автоматически.</p>
    <div class="catalog-dialog-actions"><button class="button secondary" data-category-edit-cancel type="button">Отмена</button><button class="button" type="submit">Сохранить</button></div>
  </form>`);
  dialog.querySelectorAll("[data-category-edit-cancel]").forEach((button) => {
    button.addEventListener("click", () => renderCategoryManagement());
  });
  dialog.querySelector("#category-edit-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector('button[type="submit"]');
    const data = new FormData(event.currentTarget);
    button.disabled = true;
    try {
      await api(`/api/catalog/categories/${category.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          title: String(data.get("title") || "").trim(),
          parent_id: data.get("parent_id") || null,
        }),
      });
      state.categories = await api("/api/catalog/categories");
      await openCategoryManagement(management.status);
      showToast("Категория обновлена");
    } catch (error) {
      showToast(error.message, true);
      button.disabled = false;
    }
  });
}

function openCategoryArchiveConfirmation(categoryId) {
  const management = state.operations.categoryManagement;
  const category = management?.categories.find((item) => item.id === categoryId);
  if (!category) return;
  const dialog = openCatalogActionDialog(`<div>
    <div class="catalog-dialog-head"><div><p class="eyebrow">Архив категории</p><h2>Архивировать «${escapeHtml(category.title)}»?</h2></div><button class="drawer-close" data-category-archive-cancel type="button" aria-label="Закрыть">×</button></div>
    <p>Категория исчезнет из дерева и выбора для товаров, но сохранится в истории.</p>
    <p class="muted small">Категорию с активными товарами или дочерними категориями архивировать нельзя.</p>
    <div class="catalog-dialog-actions"><button class="button secondary" data-category-archive-cancel type="button">Отмена</button><button class="button danger" data-confirm-category-archive type="button">Архивировать</button></div>
  </div>`);
  dialog.querySelectorAll("[data-category-archive-cancel]").forEach((button) => {
    button.addEventListener("click", () => renderCategoryManagement());
  });
  dialog.querySelector("[data-confirm-category-archive]").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await api(`/api/catalog/categories/${category.id}`, { method: "DELETE" });
      state.categories = await api("/api/catalog/categories");
      await openCategoryManagement("active");
      showToast("Категория перемещена в архив");
    } catch (error) {
      showToast(error.message, true);
      button.disabled = false;
    }
  });
}

async function executeCategoryRestore(categoryId, button) {
  button.disabled = true;
  try {
    await api(`/api/catalog/categories/${categoryId}/restore`, { method: "POST" });
    state.categories = await api("/api/catalog/categories");
    await openCategoryManagement("archived");
    showToast("Категория восстановлена");
  } catch (error) {
    showToast(error.message, true);
    button.disabled = false;
  }
}

async function createCatalogCategory(event, catalog) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button[type="submit"]');
  const data = new FormData(form);
  const title = String(data.get("title") || "").trim();
  if (!title) {
    form.querySelector("[name=title]").focus();
    return;
  }
  button.disabled = true;
  const returnToManagement = Boolean(state.operations.categoryManagement);
  try {
    await api("/api/catalog/categories/quick", {
      method: "POST",
      body: JSON.stringify({ title, parent_id: data.get("parent_id") || null }),
    });
    state.categories = await api("/api/catalog/categories");
    document.querySelector("#catalog-category-create-dialog").close();
    renderOperationsCatalog(catalog);
    if (returnToManagement) await openCategoryManagement("active");
    showToast("Категория создана");
  } catch (error) {
    showToast(error.message, true);
    button.disabled = false;
  }
}

async function openOperationsProduct(productId) {
  try {
    recordRoute("product", { productId });
    logicalParent = () => openOperationsCatalog(state.operations.catalog || {});
    [state.operations.product, state.categories, state.operations.imageLinks] = await Promise.all([
      api(`/api/operations/catalog/products/${productId}`),
      api("/api/catalog/categories"),
      api("/api/media/image-links"),
    ]);
    await loadAqsiStates(state.operations.product.variants.filter((variant) => !variant.is_archived));
    renderOperationsProduct();
  } catch (error) { showToast(error.message, true); }
}

function renderVariantCharacteristics(variant, readOnly = false) {
  const rows = Object.entries(variant.attributes || {}).map(([name, value]) => `<div class="characteristic-row">
    <span class="characteristic-name">${escapeHtml(name)}</span>
    <span class="characteristic-value">${escapeHtml(String(value))}</span>
    ${readOnly ? "" : `<span class="characteristic-actions"><button class="link-button" data-edit-characteristic="${variant.id}" data-characteristic-name="${escapeHtml(name)}" type="button">Изменить</button><button class="link-button danger-text" data-delete-characteristic="${variant.id}" data-characteristic-name="${escapeHtml(name)}" type="button">Удалить</button></span>`}
  </div>`).join("");
  return `<section class="variant-characteristics">
    <div class="variant-section-heading"><strong>Характеристики</strong>${readOnly ? "" : `<button class="link-button" data-add-characteristic="${variant.id}" type="button">+ Добавить характеристику</button>`}</div>
    ${rows ? `<div class="characteristic-list">${rows}</div>` : '<p class="muted small characteristic-empty">Не указаны</p>'}
  </section>`;
}

function renderVariantRentalBlock(variant, readOnly = false) {
  if (variant.rental_asset_count === 0) {
    return readOnly ? "" : `<div class="rental-entry-action"><button class="button secondary compact" data-allocate-rental="${variant.id}" type="button">Выделить в аренду</button></div>`;
  }
  return `<section class="commercial-block rental-commercial-block variant-info-section">
    <span class="variant-info-content"><strong>Аренда</strong>
      <span>Цена: ${variant.current_rental_price === null ? "не настроена" : formatMoney(variant.current_rental_price)}</span>
      <span>Залог: ${variant.current_recommended_deposit === null ? "не указан" : formatMoney(variant.current_recommended_deposit)}</span>
      <span class="rental-variant-counts"><span>Экземпляров: ${variant.rental_asset_count}</span><span class="available-text">Доступно: ${variant.available_asset_count}</span><span>Выдано: ${variant.rented_asset_count}</span></span>
    </span>
    ${readOnly ? "" : `<span class="variant-info-action"><button class="link-button" data-set-rental-prices="${variant.id}" type="button">Изменить условия</button>${Number(variant.ordinary_quantity) > 0 ? `<button class="link-button" data-allocate-rental="${variant.id}" type="button">Выделить ещё</button>` : ""}</span>`}
  </section>`;
}

function renderOperationsVariant(variant, product, canDeleteCatalog) {
  const readOnly = product.is_archived || variant.is_archived;
  return `<article class="variant-commercial-card card ${variant.is_archived ? "archived" : ""}">
    ${renderCatalogMedia("catalog_variant", variant.id, product, readOnly)}
    <span class="variant-commercial-main">
      <span class="variant-identity"><span class="variant-info-section"><span class="variant-info-content"><span class="muted small">Название варианта</span><strong>${escapeHtml(visibleVariantTitle(variant.title, "Единственный вариант"))}</strong></span>${readOnly ? "" : `<span class="variant-info-action"><button class="link-button" data-rename-variant="${variant.id}" type="button">Изменить</button></span>`}</span>${variant.is_archived ? '<span class="chip archive-chip">АРХИВНЫЙ ВАРИАНТ</span>' : ""}<span class="muted small">SKU · ${escapeHtml(variant.sku)}</span></span>
      ${renderVariantCharacteristics(variant, readOnly)}
      ${renderVariantBarcodes(variant, readOnly)}
      <span class="commercial-block variant-info-section"><span class="variant-info-content"><strong>Продажа</strong><span>Цена: ${variant.current_retail_price === null ? "не настроена" : formatMoney(variant.current_retail_price)}</span></span>${readOnly ? "" : `<span class="variant-info-action"><button class="link-button" data-set-sale-price="${variant.id}" type="button">Изменить</button></span>`}</span>
      ${renderVariantRentalBlock(variant, readOnly)}
      ${readOnly ? "" : renderAqsiState(variant)}
    </span>
    <span class="catalog-counts"><strong>На учёте ${formatQuantity(variant.physical_quantity)}</strong><span>Для продажи ${formatQuantity(variant.ordinary_quantity)}</span></span>
    ${variant.is_archived ? `<span class="variant-restore-actions"><button class="button secondary compact" data-restore-variant="${variant.id}" data-restore-context="product" type="button">Восстановить вариант</button></span>` : ""}
    ${readOnly ? "" : `<span class="variant-actions">
      ${state.user?.is_admin ? `<button class="button ghost compact" data-adjust-inventory="${variant.id}" type="button">Корректировка остатка</button>` : ""}
      <button class="button ghost compact" data-open-label="${variant.id}" type="button">Открыть PDF</button>
      <button class="button compact" data-print-label="${variant.id}" type="button">Системная печать</button>
      ${renderAqsiAction(variant)}
      ${canDeleteCatalog ? `<button class="button danger compact" data-delete-variant="${variant.id}" type="button">Удалить вариант</button>` : ""}
    </span>`}
  </article>`;
}

function renderOperationsProduct() {
  const product = state.operations.product;
  const canDeleteCatalog = !product.is_archived && (state.user?.is_admin || state.user?.is_superuser);
  const canManageTestData = state.user?.is_admin || state.user?.is_superuser;
  const categoryOptions = renderCategoryOptions(state.categories, {
    selectedId: product.category_id,
    includeRoot: false,
  });
  const variants = product.variants.length
    ? product.variants.map((variant) => renderOperationsVariant(variant, product, canDeleteCatalog)).join("")
    : '<div class="empty">У товара пока нет вариантов</div>';
  const rentalAssets = product.rental_assets.length ? `<div class="section-heading"><h2>Предметы аренды</h2></div><div class="session-list">${product.rental_assets.map(renderOperationsAssetRow).join("")}</div>` : "";
  root.innerHTML = `<div class="shell">
    ${topbar(true)}
    <p class="eyebrow">Карточка товара</p>
    <h1>${escapeHtml(product.title)} ${product.is_test ? '<span class="chip test-chip">ТЕСТ</span>' : ""} ${product.is_archived ? '<span class="chip archive-chip">АРХИВ</span>' : ""}</h1>
    ${renderCatalogMedia("catalog_product", product.id, product, product.is_archived)}
    ${product.description ? `<p>${escapeHtml(product.description)}</p>` : '<p class="muted">Описание не заполнено.</p>'}
    ${product.is_archived ? `<p class="muted">Архивная карточка доступна только для просмотра.</p><div class="catalog-restore-actions"><button class="button secondary compact" data-restore-product="${product.id}" data-restore-title="${escapeHtml(product.title)}" data-restore-context="product" type="button">Восстановить товар</button></div>` : `<details class="card"><summary><strong>Управление товаром</strong></summary>
      <form id="catalog-product-form">
        <div class="field"><label>Название</label><input name="title" value="${escapeHtml(product.title)}" required></div>
        <div class="field"><label>Описание</label><textarea name="description">${escapeHtml(product.description || "")}</textarea></div>
        <div class="field"><label>Категория</label><select name="category_id" required>${categoryOptions}</select></div>
        <button class="button full" type="submit">Сохранить товар</button>
      </form>
      <hr>
      <h3>Добавить вариант</h3>
      <p class="muted small">Административная операция для дополнительной товарной позиции.</p>
      <form id="catalog-variant-create-form">
        <div class="field"><label>Название варианта</label><input name="title" required></div>
        <div class="field"><label>Штрихкод производителя <span class="muted">(необязательно)</span></label><input name="manufacturer_barcode" autocomplete="off"></div>
        <fieldset class="characteristics-builder"><legend>Характеристики <span class="muted">(необязательно)</span></legend><div data-characteristics-builder></div><button class="button secondary compact" data-add-create-characteristic type="button">+ Добавить характеристику</button></fieldset>
        <button class="button full" type="submit">Создать вариант</button>
      </form>
    </details>`}
    <div class="section-heading"><h2>Варианты</h2><span class="muted small">${product.variant_count}</span></div>
    <div class="session-list">${variants}</div>
    ${canDeleteCatalog ? `<div class="catalog-detail-product-actions"><button class="button danger compact" data-delete-product="${product.id}" type="button">Удалить товар</button></div>` : ""}
    ${canManageTestData ? `<section class="card catalog-test-data-actions"><p class="eyebrow">Тестовые данные</p><p class="muted small">Отдельная административная операция с полной серверной проверкой зависимостей.</p>${product.is_test ? `<button class="button danger compact" data-purge-test-product="${product.id}" type="button">Удалить тестовые данные</button>` : `<button class="button secondary compact" data-classify-test-product="${product.id}" type="button">Пометить как тестовые данные</button>`}</section>` : ""}
    ${rentalAssets}
    <dialog class="catalog-action-dialog" id="catalog-action-dialog"><div id="catalog-action-content"></div></dialog>
  </div>`;
  bindTopbar();
  bindOperationsAssetRows();
  hydrateImages();
  bindImagePreviews();
  document.querySelector("#catalog-product-form")?.addEventListener("submit", saveCatalogProduct);
  document.querySelector("#catalog-variant-create-form")?.addEventListener("submit", createCatalogVariant);
  document.querySelector("[data-add-create-characteristic]")?.addEventListener("click", addCreateCharacteristicRow);
  document.querySelectorAll("[data-media-upload]").forEach((input) => input.addEventListener("change", () => uploadCatalogImage(input)));
  document.querySelectorAll("[data-rename-variant]").forEach((button) => button.addEventListener("click", () => openVariantNameEditor(button.dataset.renameVariant)));
  document.querySelectorAll("[data-add-characteristic]").forEach((button) => button.addEventListener("click", () => openCharacteristicEditor(button.dataset.addCharacteristic)));
  document.querySelectorAll("[data-edit-characteristic]").forEach((button) => button.addEventListener("click", () => openCharacteristicEditor(button.dataset.editCharacteristic, button.dataset.characteristicName)));
  document.querySelectorAll("[data-delete-characteristic]").forEach((button) => button.addEventListener("click", () => deleteVariantCharacteristic(button.dataset.deleteCharacteristic, button.dataset.characteristicName)));
  document.querySelectorAll("[data-replace-barcode]").forEach((button) => button.addEventListener("click", () => replaceVariantBarcode(button.dataset.replaceBarcode)));
  document.querySelectorAll("[data-delete-external-barcode]").forEach((button) => button.addEventListener("click", () => deleteExternalBarcode(button.dataset.deleteExternalBarcode)));
  document.querySelectorAll("[data-set-sale-price]").forEach((button) => button.addEventListener("click", () => editCatalogSalePrice(button.dataset.setSalePrice)));
  document.querySelectorAll("[data-set-rental-prices]").forEach((button) => button.addEventListener("click", () => editCatalogRentalPrices(button.dataset.setRentalPrices)));
  document.querySelectorAll("[data-allocate-rental]").forEach((button) => button.addEventListener("click", () => allocateRental(button.dataset.allocateRental)));
  document.querySelectorAll("[data-adjust-inventory]").forEach((button) => button.addEventListener("click", () => adjustInventory(button.dataset.adjustInventory)));
  document.querySelectorAll("[data-publish-aqsi]").forEach((button) => button.addEventListener("click", () => publishCatalogVariant(button.dataset.publishAqsi)));
  document.querySelectorAll("[data-verify-aqsi]").forEach((button) => button.addEventListener("click", () => verifyCatalogVariant(button.dataset.verifyAqsi)));
  document.querySelectorAll("[data-primary-link]").forEach((button) => button.addEventListener("click", () => selectCatalogPrimary(button.dataset.primaryLink)));
  document.querySelectorAll("[data-delete-link]").forEach((button) => button.addEventListener("click", () => deleteCatalogImageLink(button.dataset.deleteLink)));
  document.querySelectorAll("[data-choose-variant-photo]").forEach((button) => button.addEventListener("click", () => openVariantPhotoSelector(button.dataset.chooseVariantPhoto)));
  document.querySelectorAll("[data-use-variant-photo]").forEach((button) => button.addEventListener("click", () => useVariantImageAsProductPrimary(button.dataset.useVariantPhoto, null, button)));
  document.querySelectorAll("[data-open-label]").forEach((button) => button.addEventListener("click", () => openVariantLabel(button.dataset.openLabel)));
  document.querySelectorAll("[data-print-label]").forEach((button) => button.addEventListener("click", () => printVariantLabels(button.dataset.printLabel, 1)));
  document.querySelectorAll("[data-delete-product]").forEach((button) => button.addEventListener("click", () => openCatalogDeletePreflight("product", button.dataset.deleteProduct)));
  document.querySelectorAll("[data-delete-variant]").forEach((button) => button.addEventListener("click", () => openCatalogDeletePreflight("variant", button.dataset.deleteVariant)));
  document.querySelectorAll("[data-restore-product]").forEach((button) => button.addEventListener("click", () => openCatalogRestoreConfirmation(button.dataset.restoreProduct, button.dataset.restoreTitle, button.dataset.restoreContext)));
  document.querySelectorAll("[data-restore-variant]").forEach((button) => button.addEventListener("click", () => executeCatalogRestore("variant", button.dataset.restoreVariant, button, button.dataset.restoreContext)));
  document.querySelectorAll("[data-classify-test-product]").forEach((button) => button.addEventListener("click", () => openCatalogTestDataPreflight(button.dataset.classifyTestProduct, "classify")));
  document.querySelectorAll("[data-purge-test-product]").forEach((button) => button.addEventListener("click", () => openCatalogTestDataPreflight(button.dataset.purgeTestProduct, "purge")));
}

async function openVariantLabel(variantId) {
  openAuthenticatedFile(`/api/labels/variants/${variantId}/40x30.pdf?dpi=203`, false);
}

function promptLabelQuantity(defaultQuantity) {
  const value = window.prompt("Количество этикеток", String(defaultQuantity));
  if (value === null) return null;
  const quantity = Number(value);
  if (!Number.isInteger(quantity) || quantity < 1 || quantity > 500) {
    showToast("Количество этикеток должно быть от 1 до 500", true);
    return null;
  }
  return quantity;
}

async function printVariantLabels(variantId, defaultQuantity) {
  const quantity = promptLabelQuantity(defaultQuantity);
  if (quantity === null) return;
  try {
    const result = await api(`/api/labels/variants/${variantId}/40x30/print?quantity=${quantity}`, { method: "POST" });
    showToast(`Задание отправлено на печать · ${result.quantity} шт.`);
  } catch (error) {
    showToast("Не удалось отправить на печать. Откройте PDF и используйте системную печать.", true);
  }
}

function selectLabelProfile(previous, print, context = {}) {
  return new Promise((resolve) => {
    const dialog = document.createElement("dialog");
    dialog.className = "label-dialog";
    dialog.innerHTML = `
      <form method="dialog">
        <h2>${escapeHtml(context.title || "Размер товарной этикетки")}</h2>
        ${context.summary ? `<p><strong>${escapeHtml(context.summary)}</strong></p>` : ""}
        ${context.detail ? `<p class="muted small">${escapeHtml(context.detail)}</p>` : ""}
        <p class="muted small">PDF откроется в точном физическом размере без полей браузера.</p>
        <label class="label-profile-option">
          <input type="radio" name="profile" value="40x30" ${previous === "40x30" ? "checked" : ""}>
          <span><strong>40 × 30 мм</strong><small>Компактная товарная этикетка</small></span>
        </label>
        <label class="label-profile-option">
          <input type="radio" name="profile" value="58x40" ${previous === "58x40" ? "checked" : ""}>
          <span><strong>58 × 40 мм</strong><small>Крупнее название, цена и штрихкод</small></span>
        </label>
        <div class="actions horizontal-actions">
          <button class="button secondary" value="cancel">Отмена</button>
          <button class="button" value="confirm">${print ? "Открыть и печатать" : "Предпросмотр PDF"}</button>
        </div>
      </form>`;
    document.body.append(dialog);
    dialog.addEventListener("close", () => {
      const selected = dialog.returnValue === "confirm"
        ? dialog.querySelector("input[name=profile]:checked")?.value ?? null
        : null;
      dialog.remove();
      resolve(selected);
    }, { once: true });
    dialog.showModal();
  });
}

async function loadAqsiStates(variants) {
  state.operations.aqsi = new Map();
  await Promise.all(variants.map(async (variant) => {
    try {
      const value = await api(`/api/publishing/aqsi/variants/${variant.id}`);
      state.operations.aqsi.set(variant.id, value);
    } catch (error) {
      if (error.message !== "AQSI publication not found.") throw error;
    }
  }));
}

function renderAqsiState(variant) {
  const publication = state.operations.aqsi.get(variant.id);
  if (!publication) return '<span class="commercial-block"><strong>AQSI</strong><span>Не передан</span></span>';
  const status = publication.status;
  let title = "Отправляется";
  let detail = publication.latest_attempt_at ? formatDate(publication.latest_attempt_at) : "";
  if (status === "accepted") {
    title = "Передан в очередь AQSI";
    detail = "Ожидаем подтверждения AQSI";
  } else if (status === "published" && publication.is_outdated) {
    title = "Есть изменения, не переданные в AQSI";
    detail = publication.published_at ? `Последняя синхронизация: ${formatDate(publication.published_at)}` : "";
  } else if (status === "published") {
    title = "Синхронизирован";
    detail = publication.published_at ? formatDate(publication.published_at) : "";
  } else if (status === "failed") {
    title = "Ошибка синхронизации";
    detail = publication.last_error || "AQSI отклонил операцию";
  }
  return `<span class="commercial-block aqsi-block"><strong>AQSI</strong><span class="${status === "failed" ? "danger-text" : status === "published" && !publication.is_outdated ? "available-text" : ""}">${escapeHtml(title)}</span>${detail ? `<span class="muted small">${escapeHtml(detail)}</span>` : ""}<span class="muted small">Цена: ${variant.current_retail_price === null ? "—" : formatMoney(variant.current_retail_price)} · Штрихкод: ${escapeHtml(aqsiBarcode(variant))}</span></span>`;
}

function aqsiBarcode(variant) {
  return variant.barcode;
}

function renderVariantBarcodes(variant, readOnly = false) {
  const external = variant.barcode_source === "manufacturer";
  return `<span class="commercial-block variant-info-section"><span class="variant-info-content"><strong>Штрихкод</strong><span><span class="barcode-value">${escapeHtml(variant.barcode)}</span> <span class="muted small">${external ? "Внешний" : "Системный"}</span></span></span>${readOnly ? "" : `<span class="variant-info-action barcode-actions"><button class="link-button" data-replace-barcode="${variant.id}">Заменить</button>${external ? `<button class="link-button danger-text" data-delete-external-barcode="${variant.id}">Удалить</button>` : ""}</span>`}</span>`;
}

async function replaceVariantBarcode(variantId) {
  const value = window.prompt("Новый внешний штрихкод");
  if (value === null || !value.trim()) return;
  try {
    await api(`/api/catalog/variants/${variantId}/barcode`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    });
    await openOperationsProduct(state.operations.product.id);
    showToast("Штрихкод заменён");
  } catch (error) { showToast(error.message, true); }
}

async function deleteExternalBarcode(variantId) {
  const variant = state.operations.product.variants.find((item) => item.id === variantId);
  if (!variant || !window.confirm(`Удалить внешний штрихкод ${variant.barcode}?\nCore автоматически создаст новый системный штрихкод.`)) return;
  try {
    await api(`/api/catalog/variants/${variantId}/barcode`, { method: "DELETE" });
    await openOperationsProduct(state.operations.product.id);
    showToast("Создан новый системный штрихкод");
  } catch (error) { showToast(error.message, true); }
}

function renderAqsiAction(variant) {
  const publication = state.operations.aqsi.get(variant.id);
  if (!publication) return `<button class="button ghost compact" data-publish-aqsi="${variant.id}">Передать в AQSI</button>`;
  if (publication.status === "accepted") {
    const busy = ["pending", "processing"].includes(publication.latest_attempt_status);
    return `<button class="button ghost compact" data-verify-aqsi="${variant.id}" ${busy ? "disabled" : ""}>${busy ? "Проверяется" : "Проверить состояние"}</button>`;
  }
  if (publication.status === "failed") return `<button class="button ghost compact" data-publish-aqsi="${variant.id}">Повторить</button>`;
  if (publication.status === "published" && publication.is_outdated) return `<button class="button ghost compact" data-publish-aqsi="${variant.id}">Синхронизировать повторно</button>`;
  return `<button class="button ghost compact" data-publish-aqsi="${variant.id}">Синхронизировать повторно</button>`;
}

function variantPhotoChoices(product) {
  return (product.variants || []).flatMap((variant) => {
    const links = state.operations.imageLinks.filter((link) => link.entity_type === "catalog_variant" && link.entity_id === variant.id);
    const link = links.find((item) => item.role === "primary") || links[0];
    return link ? [{ variant, link }] : [];
  });
}

function renderVariantPhotoSelector(product, choices = variantPhotoChoices(product)) {
  const productPrimary = state.operations.imageLinks.find((link) => link.entity_type === "catalog_product" && link.entity_id === product.id && link.role === "primary");
  return `<div class="catalog-dialog-head"><div><p class="eyebrow">${escapeHtml(product.title)}</p><h2>Выбрать фото из вариантов</h2></div><button class="drawer-close" data-action-cancel type="button" aria-label="Закрыть">×</button></div>
    <div class="variant-photo-options">${choices.map(({ variant, link }) => {
      const current = productPrimary?.image_id === link.image_id;
      return `<button class="variant-photo-option" data-select-variant-photo="${link.id}" type="button" ${current ? "disabled" : ""}>
        <img data-image-id="${link.image_id}" alt="Фото варианта ${escapeHtml(visibleVariantTitle(variant.title, variant.sku))}">
        <span class="variant-photo-copy"><strong>${escapeHtml(visibleVariantTitle(variant.title, "Без названия"))}</strong><span class="muted small">${escapeHtml(variant.sku)}</span>${current ? '<span class="available-text small">Основное фото товара</span>' : ""}</span>
      </button>`;
    }).join("")}</div>`;
}

function renderCatalogMedia(entityType, entityId, product, readOnly = false) {
  const links = state.operations.imageLinks.filter((link) => link.entity_type === entityType && link.entity_id === entityId);
  const ownPrimary = links.find((link) => link.role === "primary");
  const variantFallback = !links.length && entityType === "catalog_variant"
    ? state.operations.imageLinks.find((link) => link.entity_type === "catalog_product" && link.entity_id === product.id && link.role === "primary")
    : null;
  const variantIds = new Set((product.variants || []).map((variant) => variant.id));
  const productVariantPrimaries = entityType === "catalog_product" && !ownPrimary
    ? state.operations.imageLinks.filter((link) => link.entity_type === "catalog_variant" && variantIds.has(link.entity_id) && link.role === "primary")
    : [];
  const productFallback = (product.variants || []).length === 1 && productVariantPrimaries.length === 1 ? productVariantPrimaries[0] : null;
  const photoChoices = variantPhotoChoices(product);
  const displayedVariantLink = entityType === "catalog_variant" ? (ownPrimary || links[0]) : null;
  const productPrimary = state.operations.imageLinks.find((link) => link.entity_type === "catalog_product" && link.entity_id === product.id && link.role === "primary");
  const fallback = variantFallback || productFallback;
  const image = ownPrimary || fallback || links[0];
  const gallery = links.length ? links.map((link) => `<figure class="catalog-media-item">
    <img class="image-preview-trigger" data-image-id="${link.image_id}" data-image-preview="${link.image_id}" tabindex="0" role="button" aria-label="Открыть фото крупно" alt="Фото товара">
    <figcaption><span class="chip ${link.role === "primary" ? "good" : ""}">${link.role === "primary" ? "Основное" : "Галерея"}</span>
      ${readOnly ? "" : `${link.role !== "primary" ? `<button class="link-button" data-primary-link="${link.id}">Сделать основным</button>` : ""}<button class="link-button danger-text" data-delete-link="${link.id}">Отвязать</button>`}</figcaption>
  </figure>`).join("") : '<div class="empty">Фотографий пока нет</div>';
  return `<section class="contextual-media" aria-label="${entityType === "catalog_product" ? "Фото товара" : "Фото варианта"}">
    ${image ? `<img class="catalog-photo image-preview-trigger" data-image-id="${image.image_id}" data-image-preview="${image.image_id}" tabindex="0" role="button" aria-label="Открыть фото крупно" alt="${entityType === "catalog_product" ? "Фото товара" : "Фото варианта"}">` : '<span class="catalog-photo photo-placeholder">◎</span>'}
    ${variantFallback ? '<p class="muted small">Используется общее фото товара</p>' : ""}
    ${productFallback ? '<p class="muted small">Используется фото единственного варианта</p>' : ""}
    ${readOnly ? "" : `<div class="media-actions">
      <label class="button secondary compact">Сфотографировать<input class="media-file-input" type="file" accept="image/*" capture="environment" data-media-upload="${entityType}" data-media-entity="${entityId}" aria-label="Сфотографировать"></label>
      <label class="button secondary compact">Выбрать фото<input class="media-file-input" type="file" accept="image/*" data-media-upload="${entityType}" data-media-entity="${entityId}" aria-label="Выбрать фото"></label>
      ${entityType === "catalog_product" && photoChoices.length ? `<button class="button secondary compact" data-choose-variant-photo="${entityId}" type="button">Выбрать из вариантов</button>` : ""}
      ${displayedVariantLink && productPrimary?.image_id !== displayedVariantLink.image_id ? `<button class="button secondary compact" data-use-variant-photo="${displayedVariantLink.id}" type="button">Сделать основным фото товара</button>` : ""}
      ${displayedVariantLink && productPrimary?.image_id === displayedVariantLink.image_id ? '<span class="chip good">Основное фото товара</span>' : ""}
    </div>`}
    ${links.length ? `<details><summary>Фото: ${links.length}${readOnly ? "" : " · Управление"}</summary><div class="catalog-media-grid">${gallery}</div></details>` : ""}
  </section>`;
}

function openVariantPhotoSelector(productId) {
  const product = state.operations.product;
  const choices = product?.id === productId ? variantPhotoChoices(product) : [];
  if (!choices.length) { showToast("У вариантов пока нет фотографий", true); return; }
  const dialog = openCatalogActionDialog(renderVariantPhotoSelector(product, choices));
  dialog.querySelectorAll("[data-action-cancel]").forEach((button) => button.addEventListener("click", () => dialog.close()));
  dialog.querySelectorAll("[data-select-variant-photo]").forEach((button) => button.addEventListener("click", () => useVariantImageAsProductPrimary(button.dataset.selectVariantPhoto, dialog, button)));
  hydrateImages();
}

async function useVariantImageAsProductPrimary(variantLinkId, dialog = null, button = null) {
  const source = state.operations.imageLinks.find((link) => link.id === variantLinkId && link.entity_type === "catalog_variant");
  const product = state.operations.product;
  if (!source || !product) { showToast("Фото варианта недоступно", true); return; }
  if (button) button.disabled = true;
  try {
    const productLinks = state.operations.imageLinks.filter((link) => link.entity_type === "catalog_product" && link.entity_id === product.id);
    const currentPrimary = productLinks.find((link) => link.role === "primary");
    let target = productLinks.find((link) => link.image_id === source.image_id);
    if (!target) {
      target = await api("/api/media/image-links", { method: "POST", body: JSON.stringify({ image_id: source.image_id, entity_type: "catalog_product", entity_id: product.id, role: currentPrimary ? "gallery" : "primary", sort_order: productLinks.length }) });
    }
    if (target.role !== "primary") await api(`/api/media/image-links/${target.id}/primary`, { method: "POST" });
    dialog?.close();
    await openOperationsProduct(product.id);
    showToast("Основное фото товара изменено");
  } catch (error) {
    showToast(error.message, true);
    if (button?.isConnected) button.disabled = false;
  }
}

async function saveCatalogProduct(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  try {
    await api(`/api/catalog/products/${state.operations.product.id}`, { method: "PATCH", body: JSON.stringify({ title: data.get("title"), description: nullableText(data.get("description")), category_id: data.get("category_id") }) });
    await openOperationsProduct(state.operations.product.id);
    showToast("Карточка товара сохранена");
  } catch (error) { showToast(error.message, true); }
}

function normalizeCharacteristic(name, value) {
  const normalizedName = String(name || "").trim();
  const normalizedValue = String(value || "").trim();
  if (!normalizedName) throw new Error("Укажите название характеристики");
  if (!normalizedValue) throw new Error("Укажите значение характеристики");
  return [normalizedName, normalizedValue];
}

function setVariantCharacteristic(attributes, rawName, rawValue, previousName = null) {
  const [name, value] = normalizeCharacteristic(rawName, rawValue);
  const next = { ...attributes };
  if (previousName !== null) delete next[previousName];
  if (Object.prototype.hasOwnProperty.call(next, name)) throw new Error(`Характеристика «${name}» уже существует`);
  next[name] = value;
  return next;
}

function removeVariantCharacteristic(attributes, name) {
  const next = { ...attributes };
  delete next[name];
  return next;
}

function collectCharacteristicRows(form) {
  const attributes = {};
  form.querySelectorAll("[data-characteristic-row]").forEach((row) => {
    const [name, value] = normalizeCharacteristic(
      row.querySelector('[name="characteristic_name"]').value,
      row.querySelector('[name="characteristic_value"]').value,
    );
    if (Object.prototype.hasOwnProperty.call(attributes, name)) throw new Error(`Характеристика «${name}» уже добавлена`);
    attributes[name] = value;
  });
  return attributes;
}

function addCreateCharacteristicRow() {
  const container = document.querySelector("[data-characteristics-builder]");
  if (!container) return;
  const row = document.createElement("div");
  row.className = "characteristic-builder-row";
  row.dataset.characteristicRow = "";
  row.innerHTML = `<label><span>Название</span><input name="characteristic_name" autocomplete="off" required></label><label><span>Значение</span><input name="characteristic_value" autocomplete="off" required></label><button class="button ghost compact" type="button" aria-label="Удалить характеристику">Удалить</button>`;
  row.querySelector("button").addEventListener("click", () => row.remove());
  container.append(row);
  row.querySelector("input").focus();
}

async function createCatalogVariant(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  try {
    const attributes = collectCharacteristicRows(event.currentTarget);
    await api("/api/catalog/variants", { method: "POST", body: JSON.stringify({ product_id: state.operations.product.id, title: String(data.get("title") || "").trim(), manufacturer_barcode: nullableText(data.get("manufacturer_barcode")), attributes, is_active: true }) });
    await openOperationsProduct(state.operations.product.id);
    showToast("Вариант создан");
  } catch (error) { showToast(error.message, true); }
}

function openVariantNameEditor(variantId) {
  const variant = state.operations.product.variants.find((item) => item.id === variantId);
  if (!variant) return;
  const dialog = openCatalogActionDialog(`<form id="variant-name-form">
    <div class="catalog-dialog-head"><div><p class="eyebrow">Вариант · ${escapeHtml(variant.sku)}</p><h2>Изменить название</h2></div><button class="drawer-close" data-action-cancel type="button" aria-label="Закрыть">×</button></div>
    <div class="field"><label for="variant-title-input">Название варианта</label><input id="variant-title-input" name="title" value="${escapeHtml(variant.title)}" required autofocus></div>
    <p class="muted small">SKU остаётся неизменным.</p>
    <div class="catalog-dialog-actions"><button class="button secondary" data-action-cancel type="button">Отмена</button><button class="button" type="submit">Сохранить</button></div>
  </form>`);
  dialog.querySelectorAll("[data-action-cancel]").forEach((button) => button.addEventListener("click", () => dialog.close()));
  dialog.querySelector("#variant-name-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const title = String(new FormData(event.currentTarget).get("title") || "").trim();
    if (!title) return;
    const button = event.currentTarget.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      await api(`/api/catalog/variants/${variantId}`, { method: "PATCH", body: JSON.stringify({ title }) });
      dialog.close();
      await openOperationsProduct(state.operations.product.id);
      showToast("Название варианта сохранено");
    } catch (error) { showToast(error.message, true); button.disabled = false; }
  });
}

function openCharacteristicEditor(variantId, previousName = null) {
  const variant = state.operations.product.variants.find((item) => item.id === variantId);
  if (!variant) return;
  const editing = previousName !== null;
  const previousValue = editing ? variant.attributes[previousName] : "";
  const dialog = openCatalogActionDialog(`<form id="variant-characteristic-form">
    <div class="catalog-dialog-head"><div><p class="eyebrow">${escapeHtml(visibleVariantTitle(variant.title, variant.sku))}</p><h2>${editing ? "Изменить характеристику" : "Добавить характеристику"}</h2></div><button class="drawer-close" data-action-cancel type="button" aria-label="Закрыть">×</button></div>
    <div class="field"><label for="characteristic-name-input">Название</label><input id="characteristic-name-input" name="name" value="${escapeHtml(previousName || "")}" required autofocus></div>
    <div class="field"><label for="characteristic-value-input">Значение</label><input id="characteristic-value-input" name="value" value="${escapeHtml(String(previousValue))}" required></div>
    <div class="catalog-dialog-actions"><button class="button secondary" data-action-cancel type="button">Отмена</button><button class="button" type="submit">${editing ? "Сохранить" : "Добавить"}</button></div>
  </form>`);
  dialog.querySelectorAll("[data-action-cancel]").forEach((button) => button.addEventListener("click", () => dialog.close()));
  dialog.querySelector("#variant-characteristic-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const button = event.currentTarget.querySelector('button[type="submit"]');
    try {
      const attributes = setVariantCharacteristic(
        variant.attributes,
        data.get("name"),
        data.get("value"),
        editing ? previousName : null,
      );
      button.disabled = true;
      await api(`/api/catalog/variants/${variantId}`, { method: "PATCH", body: JSON.stringify({ attributes }) });
      dialog.close();
      await openOperationsProduct(state.operations.product.id);
      showToast(editing ? "Характеристика сохранена" : "Характеристика добавлена");
    } catch (error) { showToast(error.message, true); button.disabled = false; }
  });
}

async function deleteVariantCharacteristic(variantId, name) {
  const variant = state.operations.product.variants.find((item) => item.id === variantId);
  if (!variant || !window.confirm(`Удалить характеристику «${name}»?`)) return;
  const attributes = removeVariantCharacteristic(variant.attributes, name);
  try {
    await api(`/api/catalog/variants/${variantId}`, { method: "PATCH", body: JSON.stringify({ attributes }) });
    await openOperationsProduct(state.operations.product.id);
    showToast("Характеристика удалена");
  } catch (error) { showToast(error.message, true); }
}

async function editCatalogSalePrice(variantId) {
  const variant = state.operations.product.variants.find((item) => item.id === variantId);
  const amount = window.prompt("Цена продажи, ₽", variant.current_retail_price ?? "");
  if (amount === null || amount === "") return;
  try {
    await api(`/api/pricing/variants/${variantId}/prices`, { method: "POST", body: JSON.stringify({ price_type: "retail", amount, reason: "Catalog Management" }) });
    await openOperationsProduct(state.operations.product.id);
    showToast("Цена продажи сохранена");
  } catch (error) { showToast(error.message, true); }
}

async function editCatalogRentalPrices(variantId) {
  const variant = state.operations.product.variants.find((item) => item.id === variantId);
  const rental = window.prompt("Цена аренды, ₽", variant.current_rental_price ?? "");
  if (rental === null) return;
  const deposit = window.prompt("Рекомендуемый залог, ₽", variant.current_recommended_deposit ?? "");
  if (deposit === null) return;
  try {
    if (rental !== "") await api(`/api/pricing/variants/${variantId}/prices`, { method: "POST", body: JSON.stringify({ price_type: "rental", amount: rental, reason: "Catalog Management" }) });
    if (deposit !== "") await api(`/api/pricing/variants/${variantId}/prices`, { method: "POST", body: JSON.stringify({ price_type: "rental_deposit", amount: deposit, reason: "Catalog Management" }) });
    await openOperationsProduct(state.operations.product.id);
    showToast("Условия аренды сохранены");
  } catch (error) { showToast(error.message, true); }
}

async function allocateRental(variantId) {
  const amount = window.prompt("Сколько единиц выделить в аренду?", "1");
  if (amount === null) return;
  try {
    const result = await api(`/api/operations/catalog/variants/${variantId}/allocate-rental`, { method: "POST", body: JSON.stringify({ quantity: Number(amount) }) });
    await openOperationsProduct(state.operations.product.id);
    showToast(`Созданы: ${result.asset_numbers.join(", ")}`);
  } catch (error) { showToast(error.message, true); }
}

const inventoryAdjustmentReasons = [
  ["stocktake", "Пересчёт остатков"],
  ["shortage", "Недостача"],
  ["damage", "Повреждение / брак"],
  ["gift", "Подарок"],
  ["personal_use", "Личное использование"],
  ["other", "Другое"],
];

function adjustInventory(variantId) {
  const variant = state.operations.product.variants.find((item) => item.id === variantId);
  if (!variant) return;
  const current = Number(variant.physical_quantity);
  const reasonOptions = inventoryAdjustmentReasons.map(([value, label]) => `<option value="${value}">${label}</option>`).join("");
  const dialog = openCatalogActionDialog(`<form id="inventory-adjustment-form">
    <div class="catalog-dialog-head"><div><p class="eyebrow">${escapeHtml(visibleVariantTitle(variant.title, variant.sku))}</p><h2>Корректировка остатка</h2></div><button class="drawer-close" data-action-cancel type="button" aria-label="Закрыть">×</button></div>
    <p>Сейчас на учёте: <strong>${formatQuantity(variant.physical_quantity)}</strong></p>
    <div class="field"><label for="inventory-actual-quantity">Новый фактический остаток</label><input id="inventory-actual-quantity" name="actual_quantity" type="number" step="1" value="${escapeHtml(String(variant.physical_quantity))}" required autofocus></div>
    <div class="field"><label for="inventory-adjustment-reason">Причина</label><select id="inventory-adjustment-reason" name="reason">${reasonOptions}</select></div>
    <div class="field"><label for="inventory-adjustment-comment">Комментарий <span class="muted" data-comment-hint>(необязательно)</span></label><textarea id="inventory-adjustment-comment" name="comment" maxlength="1000"></textarea></div>
    <p class="inventory-change-preview" role="status">Изменение: <strong data-adjustment-delta>0 шт.</strong></p>
    <div class="catalog-dialog-actions"><button class="button secondary" data-action-cancel type="button">Отмена</button><button class="button" type="submit">Записать корректировку</button></div>
  </form>`);
  const form = dialog.querySelector("#inventory-adjustment-form");
  const quantityInput = form.querySelector('[name="actual_quantity"]');
  const reasonSelect = form.querySelector('[name="reason"]');
  const commentInput = form.querySelector('[name="comment"]');
  const updatePreview = () => {
    const value = Number(quantityInput.value);
    const delta = value - current;
    form.querySelector("[data-adjustment-delta]").textContent = Number.isFinite(delta) ? `${delta > 0 ? "+" : ""}${delta} шт.` : "—";
  };
  const updateCommentRequirement = () => {
    const required = reasonSelect.value === "other";
    commentInput.required = required;
    form.querySelector("[data-comment-hint]").textContent = required ? "(обязательно для «Другое»)" : "(необязательно)";
  };
  quantityInput.addEventListener("input", updatePreview);
  reasonSelect.addEventListener("change", updateCommentRequirement);
  dialog.querySelectorAll("[data-action-cancel]").forEach((button) => button.addEventListener("click", () => dialog.close()));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const actual = Number(quantityInput.value);
    const quantityDelta = actual - current;
    if (!Number.isFinite(actual)) return;
    if (quantityDelta === 0) { showToast("Фактический остаток не изменился", true); return; }
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      await api(`/api/operations/catalog/variants/${variantId}/inventory-adjustments`, { method: "POST", body: JSON.stringify({ quantity_delta: String(quantityDelta), reason: reasonSelect.value, comment: nullableText(commentInput.value) }) });
      dialog.close();
      await openOperationsProduct(state.operations.product.id);
      showToast("Корректировка записана в складской ledger");
    } catch (error) { showToast(error.message, true); button.disabled = false; }
  });
}

async function uploadCatalogImage(input) {
  const file = input.files?.[0];
  if (!file) return;
  const entityType = input.dataset.mediaUpload;
  const entityId = input.dataset.mediaEntity;
  const productId = state.operations.product.id;
  const links = state.operations.imageLinks.filter((link) => link.entity_type === entityType && link.entity_id === entityId);
  const upload = new FormData();
  upload.set("file", file);
  const controls = input.closest(".contextual-media").querySelectorAll("input");
  controls.forEach((control) => { control.disabled = true; });
  try {
    const image = await api("/api/media/images/upload", { method: "POST", body: upload });
    await api("/api/media/image-links", { method: "POST", body: JSON.stringify({ image_id: image.id, entity_type: entityType, entity_id: entityId, role: links.some((link) => link.role === "primary") ? "gallery" : "primary", sort_order: links.length }) });
    if (input.isConnected) await openOperationsProduct(productId);
    showToast("Фото добавлено");
  } catch (error) { showToast(error.message, true); }
  finally { controls.forEach((control) => { control.disabled = false; }); input.value = ""; }
}

async function selectCatalogPrimary(linkId) {
  try {
    await api(`/api/media/image-links/${linkId}/primary`, { method: "POST" });
    await openOperationsProduct(state.operations.product.id);
    showToast("Основное фото изменено");
  } catch (error) { showToast(error.message, true); }
}

async function deleteCatalogImageLink(linkId) {
  if (!window.confirm("Отвязать фото от карточки? Сам файл останется в Media.")) return;
  try {
    await api(`/api/media/image-links/${linkId}`, { method: "DELETE" });
    await openOperationsProduct(state.operations.product.id);
    showToast("Фото отвязано");
  } catch (error) { showToast(error.message, true); }
}

async function publishCatalogVariant(variantId) {
  try {
    const result = await api(`/api/publishing/aqsi/variants/${variantId}`, { method: "POST" });
    await openOperationsProduct(state.operations.product.id);
    showToast(result.queued ? "Команда отправлена. Ожидаем AQSI." : "Такая операция уже выполняется.");
  } catch (error) { showToast(error.message, true); }
}

async function verifyCatalogVariant(variantId) {
  try {
    const result = await api(`/api/publishing/aqsi/variants/${variantId}/verify`, { method: "POST" });
    await openOperationsProduct(state.operations.product.id);
    showToast(result.queued ? "Проверка AQSI поставлена в очередь" : "Проверка уже выполняется");
  } catch (error) { showToast(error.message, true); }
}

async function openOperationsAssets(query = "", assetFilter = "all", sort = "asset_number") {
  try {
    const params = new URLSearchParams({ asset_filter: assetFilter, sort });
    if (query) params.set("query", query);
    state.operations.assets = await api(`/api/operations/rental/assets?${params}`);
    renderOperationsAssets(query, assetFilter, sort);
  } catch (error) { showToast(error.message, true); }
}

function renderOperationsAssets(query, assetFilter, sort) {
  const rows = state.operations.assets.length
    ? state.operations.assets.map(renderOperationsAssetRow).join("")
    : '<div class="empty">Предметы аренды не найдены</div>';
  root.innerHTML = `<div class="shell">
    ${topbar(true)}
    <p class="eyebrow">Каталог</p>
    <h1>Предметы аренды</h1>
    <form class="search-row" id="operations-asset-search">
      <input name="query" value="${escapeHtml(query)}" placeholder="RENT-, товар, вариант или SKU" autocomplete="off">
      <button class="button" type="submit">Найти</button>
    </form>
    <div class="filter-bar">
      ${assetFilterButton("all", "Все", assetFilter)}
      ${assetFilterButton("available", "Available", assetFilter)}
      ${assetFilterButton("rented", "Rented", assetFilter)}
      ${assetFilterButton("maintenance", "Maintenance", assetFilter)}
      ${assetFilterButton("lost", "Lost", assetFilter)}
    </div>
    <div class="field sort-field"><label>Сортировка</label><select id="operations-asset-sort">
      <option value="asset_number" ${sort === "asset_number" ? "selected" : ""}>По инвентарному номеру</option>
      <option value="product" ${sort === "product" ? "selected" : ""}>По товару</option>
      <option value="status" ${sort === "status" ? "selected" : ""}>По статусу</option>
    </select></div>
    <div class="session-list">${rows}</div>
  </div>`;
  bindTopbar();
  bindOperationsAssetRows();
  document.querySelector("#operations-asset-search").addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = String(new FormData(event.currentTarget).get("query") || "").trim();
    if (/^rent-\d+$/i.test(value)) {
      try {
        const asset = await api(`/api/rental/assets/by-number/${encodeURIComponent(value)}`);
        await openOperationsAsset(asset.id);
        return;
      } catch (error) {
        if (error.message !== "Rental asset not found.") {
          showToast(error.message, true);
          return;
        }
      }
    }
    openOperationsAssets(value, assetFilter, sort);
  });
  document.querySelectorAll("[data-asset-filter]").forEach((button) => {
    button.addEventListener("click", () => openOperationsAssets(query, button.dataset.assetFilter, sort));
  });
  document.querySelector("#operations-asset-sort").addEventListener("change", (event) => openOperationsAssets(query, assetFilter, event.target.value));
}

function assetFilterButton(value, label, active) {
  return `<button class="filter-chip ${value === active ? "active" : ""}" data-asset-filter="${value}">${label}</button>`;
}

function renderOperationsAssetRow(asset) {
  return `<article class="asset-catalog-row">
    <button class="asset-main" data-operations-asset="${asset.id}">
      <span><strong>${escapeHtml(asset.product_title)} · ${escapeHtml(asset.variant_title)}</strong><br><span class="muted small">${escapeHtml(asset.asset_number)} · ${escapeHtml(asset.sku)}</span></span>
      <span class="chips compact-chips"><span class="chip ${asset.availability === "available" ? "good" : asset.is_lost ? "warn" : ""}">${asset.is_lost ? "LOST" : escapeHtml(availabilityLabel(asset.availability))}</span><span class="chip">${formatMoney(asset.economics.net_income)}</span>${asset.economics.flags.map(efficiencyChip).join("")}</span>
    </button>
    <span class="inline-actions"><button class="button ghost compact" data-print-rental-label="${asset.id}">Печать RENT</button>${asset.current_order_id ? `<button class="button ghost compact" data-asset-order="${asset.current_order_id}">${escapeHtml(asset.current_order_number)} →</button>` : ""}</span>
  </article>`;
}

function bindOperationsAssetRows() {
  document.querySelectorAll("[data-operations-asset]").forEach((button) => {
    button.addEventListener("click", () => openOperationsAsset(button.dataset.operationsAsset));
  });
  document.querySelectorAll("[data-asset-order]").forEach((button) => {
    button.addEventListener("click", () => openRentalReturnOrder(button.dataset.assetOrder));
  });
  document.querySelectorAll("[data-print-rental-label]").forEach((button) => {
    button.addEventListener("click", () => openRentalAssetLabel(button.dataset.printRentalLabel));
  });
}

async function openRentalAssetLabel(assetId, print = true) {
  const asset = state.operations.asset?.id === assetId
    ? state.operations.asset
    : state.operations.assets.find((item) => item.id === assetId)
      || state.operations.product?.rental_assets.find((item) => item.id === assetId);
  const previous = localStorage.getItem("core.rental-label-profile") || "40x30";
  const profile = await selectLabelProfile(previous, print, {
    title: "Инвентарная этикетка RentalAsset",
    summary: asset?.asset_number || "RENT",
    detail: asset ? `${asset.product_title} · ${asset.variant_title} · ${availabilityLabel(asset.availability)}` : "Code 128",
  });
  if (profile === null) return;
  localStorage.setItem("core.rental-label-profile", profile);
  openAuthenticatedFile(`/api/labels/rental-assets/${assetId}/${profile}.pdf?dpi=203`, print);
}

async function openOperationsAsset(assetId) {
  try {
    logicalParent = () => openOperationsProduct(state.operations.asset?.product_id || state.operations.product?.id);
    state.operations.asset = await api(`/api/operations/rental/assets/${assetId}/passport`);
    renderOperationsAsset();
  } catch (error) { showToast(error.message, true); }
}

function renderOperationsAsset() {
  const asset = state.operations.asset;
  const economics = asset.economics;
  const timeline = asset.timeline.length ? asset.timeline.map((event) => `
    <article class="timeline-row">
      <span class="timeline-dot"></span>
      <div><strong>${escapeHtml(event.title)}</strong><div class="muted small">${formatDate(event.occurred_at)}${event.detail ? ` · ${escapeHtml(event.detail)}` : ""}</div>
      <div class="inline-actions">${event.order_id ? `<button class="link-button" data-passport-order="${event.order_id}">${escapeHtml(event.order_number)} →</button>` : ""}${event.customer_id ? `<button class="link-button" data-passport-customer="${event.customer_id}">${escapeHtml(event.customer_name)} →</button>` : ""}</div></div>
    </article>`).join("") : '<div class="empty">История пока пуста</div>';
  const maintenance = asset.maintenance.length ? asset.maintenance.map((record) => `
    <article class="session-row"><span><strong>${escapeHtml(maintenanceTypeLabel(record.service_type))} · ${formatMoney(record.cost)}</strong><br><span class="muted small">${formatDate(record.performed_at)} · ${escapeHtml(record.performer_name || "Исполнитель не указан")}</span><br>${escapeHtml(record.result)}${record.comment ? `<br><span class="muted small">${escapeHtml(record.comment)}</span>` : ""}</span></article>`).join("") : '<div class="empty">Обслуживаний пока нет</div>';
  const damages = asset.damages.length ? asset.damages.map((record) => `
    <article class="session-row"><span><strong>${escapeHtml(damageSeverityLabel(record.severity))}: ${escapeHtml(record.description)}</strong><br><span class="muted small">${formatDate(record.created_at)} · ${escapeHtml(record.recorded_by_name || "Автор не указан")}${record.order_number ? ` · ${escapeHtml(record.order_number)}` : ""}</span>${record.comment ? `<br>${escapeHtml(record.comment)}` : ""}</span></article>`).join("") : '<div class="empty">Повреждений не зафиксировано</div>';
  const photos = asset.condition_photos.length ? asset.condition_photos.map((photo) => `
    <figure class="condition-photo"><img data-image-id="${photo.image_id}" alt="Состояние ${photo.stage === "before" ? "до" : "после"} аренды"><figcaption>${photo.stage === "before" ? "До аренды" : "После аренды"} · ${formatDate(photo.created_at)}</figcaption></figure>`).join("") : '<div class="empty">Фотографий состояния пока нет</div>';
  root.innerHTML = `<div class="shell">
    ${topbar(true)}
    <p class="eyebrow">Предмет аренды</p>
    <h1>${escapeHtml(asset.asset_number)}</h1>
    <h2>${escapeHtml(asset.product_title)} · ${escapeHtml(asset.variant_title)}</h2>
    <section class="card order-facts">
      <div><span class="muted small">SKU</span><strong>${escapeHtml(asset.sku)}</strong></div>
      <div><span class="muted small">Состояние</span><strong>${escapeHtml(conditionLabel(asset.condition))}</strong></div>
      <div><span class="muted small">Доступность</span><strong>${escapeHtml(availabilityLabel(asset.availability))}</strong></div>
      <div><span class="muted small">Текущая аренда</span><strong>${escapeHtml(asset.current_order_number || "Нет")}</strong></div>
      <div><span class="muted small">Завершённых аренд</span><strong>${asset.completed_rental_count}</strong></div>
      <div><span class="muted small">Поступил</span><strong>${formatDate(asset.created_at)}</strong></div>
    </section>
    <div class="actions horizontal-actions">
      <button class="button secondary" id="asset-open-product">← К товару</button>
      <button class="button" id="asset-print-label">Печать инвентарной этикетки</button>
      ${asset.current_order_id ? `<button class="button" id="asset-open-order">Открыть аренду →</button>` : ""}
      ${asset.purpose === "rental" && asset.availability === "available" ? '<button class="button ghost" id="asset-withdraw-for-sale">Вывести из аренды</button>' : ""}
    </div>
    <div class="section-heading"><h2>Экономика</h2><span class="chips">${economics.flags.map(efficiencyChip).join("")}</span></div>
    <section class="card order-facts">
      <div><span class="muted small">Стоимость приобретения</span><strong>${economics.acquisition_cost === null ? "Не зафиксирована" : formatMoney(economics.acquisition_cost)}</strong></div>
      <div><span class="muted small">Доход</span><strong>${formatMoney(economics.revenue)}</strong></div>
      <div><span class="muted small">Расходы</span><strong>${formatMoney(economics.expenses)}</strong></div>
      <div><span class="muted small">Чистый доход</span><strong>${formatMoney(economics.net_income)}</strong></div>
      <div><span class="muted small">Количество аренд</span><strong>${economics.rental_count}</strong></div>
      <div><span class="muted small">Средняя аренда</span><strong>${formatMoney(economics.average_rental_revenue)}</strong></div>
      <div><span class="muted small">Последняя аренда</span><strong>${economics.last_rental_at ? formatDate(economics.last_rental_at) : "Нет"}</strong></div>
      <div><span class="muted small">Дата окупаемости</span><strong>${economics.payback_at ? formatDate(economics.payback_at) : "Не окупился"}</strong></div>
    </section>
    <div class="section-heading"><h2>История</h2><span class="muted small">Сдавался: ${asset.rental_count}</span></div>
    <div class="timeline">${timeline}</div>
    <div class="section-heading"><h2>Обслуживание</h2></div>
    <div class="session-list">${maintenance}</div>
    <form class="card" id="asset-maintenance-form">
      <h3>Добавить обслуживание</h3>
      <div class="field-row"><div class="field"><label>Тип</label><select name="service_type"><option value="preventive">Профилактика</option><option value="repair">Ремонт</option><option value="cleaning">Чистка</option><option value="part_replacement">Замена деталей</option></select></div><div class="field"><label>Результат</label><input name="result" required></div></div>
      <div class="field-row"><div class="field"><label>Стоимость, ₽</label><input name="cost" type="number" min="0" step="0.01" value="0" required></div><div class="field"><label>Комментарий</label><textarea name="comment"></textarea></div></div>
      <button class="button secondary full" type="submit">Сохранить обслуживание</button>
    </form>
    <div class="section-heading"><h2>Повреждения</h2></div>
    <div class="session-list">${damages}</div>
    <form class="card" id="asset-damage-form">
      <h3>Зафиксировать повреждение</h3>
      <div class="field"><label>Описание</label><textarea name="description" required></textarea></div>
      <div class="field-row"><div class="field"><label>Серьёзность</label><select name="severity"><option value="minor">Незначительное</option><option value="moderate">Среднее</option><option value="major">Серьёзное</option><option value="critical">Критическое</option></select></div><div class="field"><label>Комментарий</label><input name="comment"></div></div>
      <button class="button secondary full" type="submit">Сохранить повреждение</button>
    </form>
    <div class="section-heading"><h2>Фото состояния</h2></div>
    <div class="condition-photos">${photos}</div>
    <form class="card" id="asset-photo-form">
      <div class="field-row"><div class="field"><label>Момент</label><select name="stage"><option value="before">До аренды</option><option value="after">После аренды</option></select></div><div class="field"><label>Фото</label><input name="file" type="file" accept="image/*" capture="environment" required></div></div>
      <button class="button secondary full" type="submit">Добавить фото</button>
    </form>
  </div>`;
  bindTopbar();
  hydrateImages();
  document.querySelector("#asset-open-product").addEventListener("click", () => openOperationsProduct(asset.product_id));
  document.querySelector("#asset-print-label").addEventListener("click", () => openRentalAssetLabel(asset.id));
  document.querySelector("#asset-open-order")?.addEventListener("click", () => openRentalReturnOrder(asset.current_order_id));
  document.querySelector("#asset-withdraw-for-sale")?.addEventListener("click", withdrawAssetForSale);
  document.querySelectorAll("[data-passport-order]").forEach((button) => button.addEventListener("click", () => openRentalReturnOrder(button.dataset.passportOrder)));
  document.querySelectorAll("[data-passport-customer]").forEach((button) => button.addEventListener("click", () => selectRentalCustomer(button.dataset.passportCustomer)));
  document.querySelector("#asset-maintenance-form").addEventListener("submit", addAssetMaintenance);
  document.querySelector("#asset-damage-form").addEventListener("submit", addAssetDamage);
  document.querySelector("#asset-photo-form").addEventListener("submit", addAssetConditionPhoto);
}

async function withdrawAssetForSale() {
  const asset = state.operations.asset;
  if (!window.confirm(`Вывести ${asset.asset_number} из аренды и вернуть единицу в обычный остаток?`)) return;
  try {
    await api(`/api/operations/rental/assets/${asset.id}/withdraw-for-sale`, { method: "POST" });
    await openOperationsProduct(asset.product_id);
    showToast(`${asset.asset_number} выведен из аренды. История сохранена.`);
  } catch (error) { showToast(error.message, true); }
}

async function addAssetMaintenance(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  try {
    await api(`/api/operations/rental/assets/${state.operations.asset.id}/maintenance`, { method: "POST", body: JSON.stringify({ service_type: data.get("service_type"), result: data.get("result"), cost: data.get("cost"), comment: nullableText(data.get("comment")) }) });
    await openOperationsAsset(state.operations.asset.id);
    showToast("Обслуживание сохранено");
  } catch (error) { showToast(error.message, true); }
}

async function addAssetDamage(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  try {
    await api(`/api/operations/rental/assets/${state.operations.asset.id}/damages`, { method: "POST", body: JSON.stringify({ description: data.get("description"), severity: data.get("severity"), comment: nullableText(data.get("comment")) }) });
    await openOperationsAsset(state.operations.asset.id);
    showToast("Повреждение сохранено");
  } catch (error) { showToast(error.message, true); }
}

async function addAssetConditionPhoto(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  try {
    await api(`/api/operations/rental/assets/${state.operations.asset.id}/condition-photos`, { method: "POST", body: data });
    await openOperationsAsset(state.operations.asset.id);
    showToast("Фото состояния сохранено");
  } catch (error) { showToast(error.message, true); }
}

function maintenanceTypeLabel(value) {
  return ({ preventive: "Профилактика", repair: "Ремонт", cleaning: "Чистка", part_replacement: "Замена деталей" })[value] || value;
}

function damageSeverityLabel(value) {
  return ({ minor: "Незначительное", moderate: "Среднее", major: "Серьёзное", critical: "Критическое" })[value] || value;
}

async function openRentalHub() {
  try {
    recordRoute("rental");
    logicalParent = () => loadHome();
    state.rental.drafts = await api("/rental/orders?order_status=issued");
    renderRentalHub();
  } catch (error) { showToast(error.message, true); }
}

function renderRentalHub() {
  const activeRows = state.rental.drafts.length
    ? state.rental.drafts.map((order) => `
      <button class="session-row" data-hub-order="${order.id}">
        <span><strong>${escapeHtml(order.order_number)}</strong>${order.is_overdue ? '<span class="chip warn inline-chip">Просрочен</span>' : ""}<br><span class="muted small">${escapeHtml(order.customer_name_snapshot)} · возврат ${formatShortDate(order.planned_return_at)}</span></span>
        <span aria-hidden="true">→</span>
      </button>`).join("")
    : '<div class="empty">Активных аренд нет. Можно оформить новую.</div>';
  root.innerHTML = `<div class="shell">
    ${topbar(true)}
    <p class="eyebrow">Операции</p>
    <h1>Аренда</h1>
    <p class="muted">Выдача и возврат остаются отдельными процессами, но доступны из одного раздела.</p>
    <div class="actions rental-actions">
      <button class="action-card" id="hub-active"><span class="action-icon">●</span><strong>Активные аренды</strong><span class="muted small">${state.rental.drafts.length} договоров</span></button>
      <button class="action-card" id="hub-new"><span class="action-icon">↗</span><strong>Новая аренда</strong><span class="muted small">Клиент и предметы аренды</span></button>
      <button class="action-card" id="hub-return"><span class="action-icon">↙</span><strong>Возврат</strong><span class="muted small">Осмотр и завершение</span></button>
    </div>
    <div class="section-heading" id="active-rentals"><h2>Активные аренды</h2><span class="muted small">${state.rental.drafts.length}</span></div>
    <div class="session-list">${activeRows}</div>
  </div>`;
  bindTopbar();
  document.querySelector("#hub-active").addEventListener("click", () => document.querySelector("#active-rentals").scrollIntoView({ behavior: "smooth" }));
  document.querySelector("#hub-new").addEventListener("click", openRentalHome);
  document.querySelector("#hub-return").addEventListener("click", () => openRentalReturnHome());
  document.querySelectorAll("[data-hub-order]").forEach((button) => {
    button.addEventListener("click", () => openRentalReturnOrder(button.dataset.hubOrder));
  });
}

async function openRentalHome() {
  try {
    logicalParent = () => openRentalHub();
    const drafts = await api("/rental/orders?order_status=draft");
    state.rental = {
      customers: [],
      customer: null,
      drafts,
      order: null,
      assets: [],
      returnAssets: new Map(),
    };
    renderRentalHome();
  } catch (error) { showToast(error.message, true); }
}

function renderRentalHome() {
  const draftRows = state.rental.drafts.length
    ? state.rental.drafts.map((order) => `
      <button class="session-row" data-rental-draft="${order.id}">
        <span><strong>${escapeHtml(order.order_number)}</strong><br><span class="muted small">${escapeHtml(order.customer_name_snapshot)} · ${formatShortDate(order.planned_return_at)}</span></span>
        <span aria-hidden="true">→</span>
      </button>`).join("")
    : '<div class="empty">Черновиков аренды нет</div>';
  root.innerHTML = `<div class="shell">
    ${topbar(true)}
    <p class="eyebrow">Rental checkout</p>
    <h1>Новая аренда</h1>
    <p class="muted">Найдите клиента по имени, телефону или номеру.</p>
    <form class="search-row" id="customer-search-form">
      <input name="query" autocomplete="off" placeholder="Имя, телефон или CUST-номер" autofocus>
      <button class="button" type="submit">Найти</button>
    </form>
    <div id="customer-results">${renderCustomerResults()}</div>
    <button class="button secondary full" id="show-customer-create">＋ Создать нового клиента</button>
    <form class="card hidden" id="customer-create-form">
      <h2>Новый клиент</h2>
      <div class="field"><label>Имя</label><input name="full_name" maxlength="255" required></div>
      <div class="field"><label>Телефон</label><input name="phone" type="tel" maxlength="32" required></div>
      <div class="field"><label>Электронная почта <span class="muted">(необязательно)</span></label><input name="email" type="email" maxlength="320"></div>
      <div class="field"><label>Комментарий <span class="muted">(необязательно)</span></label><textarea name="note"></textarea></div>
      <button class="button full" type="submit">Создать и продолжить</button>
    </form>
    <h2 style="margin-top:28px">Черновики</h2>
    <div class="session-list">${draftRows}</div>
  </div>`;
  bindTopbar();
  document.querySelector("#customer-search-form").addEventListener("submit", searchCustomers);
  document.querySelector("#show-customer-create").addEventListener("click", () => {
    document.querySelector("#customer-create-form").classList.toggle("hidden");
  });
  document.querySelector("#customer-create-form").addEventListener("submit", createRentalCustomer);
  bindCustomerResults();
  document.querySelectorAll("[data-rental-draft]").forEach((button) => {
    button.addEventListener("click", () => openRentalDraft(button.dataset.rentalDraft));
  });
}

function renderCustomerResults() {
  if (!state.rental.customers.length) return "";
  return `<div class="session-list">${state.rental.customers.map((customer) => `
    <button class="session-row" data-rental-customer="${customer.id}" ${customer.status !== "active" ? "disabled" : ""}>
      <span><strong>${escapeHtml(customer.full_name)}</strong><br><span class="muted small">${escapeHtml(customer.phone)} · ${escapeHtml(customer.customer_number)}</span></span>
      <span>${customer.status === "active" ? "→" : "Неактивен"}</span>
    </button>`).join("")}</div>`;
}

function bindCustomerResults() {
  document.querySelectorAll("[data-rental-customer]").forEach((button) => {
    button.addEventListener("click", () => selectRentalCustomer(button.dataset.rentalCustomer));
  });
}

async function searchCustomers(event) {
  event.preventDefault();
  const query = new FormData(event.currentTarget).get("query");
  try {
    state.rental.customers = await api(`/api/customers?query=${encodeURIComponent(String(query || ""))}`);
    document.querySelector("#customer-results").innerHTML = state.rental.customers.length
      ? renderCustomerResults()
      : '<div class="empty" style="margin:16px 0">Клиент не найден. Создайте его ниже.</div>';
    bindCustomerResults();
  } catch (error) { showToast(error.message, true); }
}

async function createRentalCustomer(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  const payload = {
    full_name: String(data.get("full_name")),
    phone: String(data.get("phone")),
    email: nullableText(data.get("email")),
    note: nullableText(data.get("note")),
  };
  try {
    state.rental.customer = await api("/api/customers", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.rental.customerHistory = null;
    renderRentalCustomer();
    showToast("Клиент создан");
  } catch (error) { showToast(error.message, true); }
}

async function selectRentalCustomer(customerId) {
  try {
    recordRoute("customer", { customerId });
    logicalParent = () => openRentalHub();
    [state.rental.customer, state.rental.customerHistory] = await Promise.all([
      api(`/api/customers/${customerId}`),
      api(`/api/operations/rental/customers/${customerId}/history`),
    ]);
    renderRentalCustomer();
  } catch (error) { showToast(error.message, true); }
}

function renderRentalCustomer() {
  const customer = state.rental.customer;
  const history = state.rental.customerHistory;
  const now = new Date();
  const tomorrow = new Date(now.getTime() + 24 * 60 * 60 * 1000);
  root.innerHTML = `<div class="shell">
    ${topbar(true)}
    <p class="eyebrow">Клиент выбран</p>
    <section class="card customer-card">
      <span class="chip good">${escapeHtml(customer.customer_number)}</span>
      <h1>${escapeHtml(customer.full_name)}</h1>
      <p><a href="tel:${escapeHtml(customer.phone)}">${escapeHtml(customer.phone)}</a>${customer.email ? ` · ${escapeHtml(customer.email)}` : ""}</p>
      ${customer.note ? `<p class="muted">${escapeHtml(customer.note)}</p>` : ""}
    </section>
    ${history ? renderCustomerRentalHistory(history) : ""}
    <form class="card" id="rental-create-form">
      <h2>Новый договор</h2>
      <div class="field-row">
        <div class="field"><label>Начало</label><input name="planned_start_at" type="datetime-local" value="${toLocalInput(now)}" required></div>
        <div class="field"><label>Возврат</label><input name="planned_return_at" type="datetime-local" value="${toLocalInput(tomorrow)}" required></div>
      </div>
      <div class="field"><label>Залог, ₽</label><input name="deposit_amount" type="number" inputmode="decimal" min="0" step="0.01" value="0" required></div>
      <div class="field"><label>Комментарий <span class="muted">(необязательно)</span></label><textarea name="note"></textarea></div>
      <button class="button full" type="submit">Создать черновик</button>
    </form>
  </div>`;
  bindTopbar();
  document.querySelector("#rental-create-form").addEventListener("submit", createRentalDraft);
  document.querySelectorAll("[data-customer-order]").forEach((button) => {
    button.addEventListener("click", () => openRentalReturnOrder(button.dataset.customerOrder));
  });
}

function renderCustomerRentalHistory(history) {
  const rows = [...history.active_rentals, ...history.completed_rentals];
  const contracts = rows.length ? rows.map((order) => `
    <button class="session-row" data-customer-order="${order.order_id}">
      <span><strong>${escapeHtml(order.order_number)}</strong><br><span class="muted small">${order.status === "issued" ? "Активна" : "Завершена"} · ${order.item_count} поз.</span></span><span>→</span>
    </button>`).join("") : '<div class="empty">Аренд пока нет</div>';
  return `<section class="card">
    <div class="section-heading"><h2>История аренд</h2><span class="chip">Всего: ${history.rental_count}</span></div>
    <div class="chips"><span class="chip good">Активные: ${history.active_rentals.length}</span><span class="chip">Завершённые: ${history.completed_rentals.length}</span></div>
    <p class="muted small">${history.current_debt_amount === null ? "Учёт задолженности пока не ведётся." : `Текущая задолженность: ${escapeHtml(history.current_debt_amount)} ₽`}</p>
    <div class="session-list">${contracts}</div>
  </section>`;
}

async function createRentalDraft(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  try {
    state.rental.order = await api("/rental/orders", {
      method: "POST",
      body: JSON.stringify({
        customer_id: state.rental.customer.id,
        planned_start_at: new Date(String(data.get("planned_start_at"))).toISOString(),
        planned_return_at: new Date(String(data.get("planned_return_at"))).toISOString(),
        deposit_amount: String(data.get("deposit_amount")),
        note: nullableText(data.get("note")),
      }),
    });
    await loadDefaultRentalAssets();
    renderRentalDraft();
  } catch (error) { showToast(error.message, true); }
}

async function openRentalDraft(orderId) {
  try {
    recordRoute("order", { orderId });
    logicalParent = () => selectRentalCustomer(state.rental.order?.customer_id);
    state.rental.order = await api(`/rental/orders/${orderId}`);
    state.rental.customer = await api(`/api/customers/${state.rental.order.customer_id}`);
    await loadDefaultRentalAssets();
    renderRentalDraft();
  } catch (error) { showToast(error.message, true); }
}

function renderRentalDraft() {
  const order = state.rental.order;
  const total = order.items.reduce(
    (sum, item) => sum + Number(item.agreed_price) - Number(item.discount),
    0,
  );
  const items = order.items.length ? order.items.map((item) => `
    <article class="session-row rental-line">
      <span><strong>${escapeHtml(item.title_snapshot)}</strong><br><span class="muted small">${escapeHtml(item.asset_number_snapshot)} · ${formatMoney(Number(item.agreed_price) - Number(item.discount))}</span></span>
      <button class="button danger compact" data-remove-rental-item="${item.id}" type="button">Удалить</button>
    </article>`).join("") : '<div class="empty">Добавьте первый предмет аренды</div>';
  root.innerHTML = `<div class="shell">
    ${topbar(true)}
    <p class="eyebrow">Черновик · ${escapeHtml(order.order_number)}</p>
    <h1>${escapeHtml(order.customer_name_snapshot)}</h1>
    <p class="muted">${escapeHtml(order.customer_phone_snapshot)}</p>
    <form class="card" id="rental-draft-form">
      <h2>Условия аренды</h2>
      <div class="field-row">
        <div class="field"><label>Начало</label><input name="planned_start_at" type="datetime-local" value="${toLocalInput(new Date(order.planned_start_at))}" required></div>
        <div class="field"><label>Возврат</label><input name="planned_return_at" type="datetime-local" value="${toLocalInput(new Date(order.planned_return_at))}" required></div>
      </div>
      <div class="field"><label>Залог, ₽</label><input name="deposit_amount" type="number" inputmode="decimal" min="0" step="0.01" value="${escapeHtml(order.deposit_amount)}" required></div>
      <div class="field"><label>Комментарий</label><textarea name="note">${escapeHtml(order.note || "")}</textarea></div>
      <button class="button secondary full" type="submit">Сохранить условия</button>
    </form>
    <section class="card">
      <h2>Добавить предмет аренды</h2>
      <p class="muted small">Отсканируйте RENT-номер или найдите по товару, варианту либо SKU.</p>
      <form class="search-row" id="rental-asset-search-form">
        <input name="query" autocomplete="off" placeholder="RENT-000001 или название" required>
        <button class="button" type="submit">Найти</button>
      </form>
      <div id="rental-asset-results">${renderRentalAssetResults()}</div>
    </section>
    <h2>Предметы аренды · ${order.items.length}</h2>
    <div class="session-list">${items}</div>
    <section class="checkout-summary">
      <div><span class="muted small">Стоимость</span><strong>${formatMoney(total)}</strong></div>
      <div><span class="muted small">Залог</span><strong>${formatMoney(Number(order.deposit_amount))}</strong></div>
      <button class="button full" id="issue-rental-order" ${order.items.length ? "" : "disabled"}>Выдать</button>
    </section>
  </div>`;
  bindTopbar();
  document.querySelector("#rental-draft-form").addEventListener("submit", saveRentalDraft);
  document.querySelector("#rental-asset-search-form").addEventListener("submit", searchRentalAssets);
  bindRentalAssetResults();
  document.querySelectorAll("[data-remove-rental-item]").forEach((button) => {
    button.addEventListener("click", () => removeRentalItem(button.dataset.removeRentalItem));
  });
  document.querySelector("#issue-rental-order").addEventListener("click", issueRentalOrder);
}

async function saveRentalDraft(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  try {
    state.rental.order = await api(`/rental/orders/${state.rental.order.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        planned_start_at: new Date(String(data.get("planned_start_at"))).toISOString(),
        planned_return_at: new Date(String(data.get("planned_return_at"))).toISOString(),
        deposit_amount: String(data.get("deposit_amount")),
        note: nullableText(data.get("note")),
      }),
    });
    renderRentalDraft();
    showToast("Условия сохранены");
  } catch (error) { showToast(error.message, true); }
}

async function searchRentalAssets(event) {
  event.preventDefault();
  const query = new FormData(event.currentTarget).get("query");
  try {
    const matches = await api(`/api/rental/assets?query=${encodeURIComponent(String(query))}`);
    state.rental.assets = [
      ...state.rental.assets,
      ...matches.filter((match) => !state.rental.assets.some((asset) => asset.id === match.id)),
    ];
    document.querySelector("#rental-asset-results").innerHTML = renderRentalAssetResults()
      || '<div class="empty" style="margin-top:14px">Предмет аренды не найден</div>';
    bindRentalAssetResults();
  } catch (error) { showToast(error.message, true); }
}

function renderRentalAssetResults() {
  return state.rental.assets.map((asset) => {
    const alreadyAdded = state.rental.order?.items.some((item) => item.rental_asset_id === asset.id);
    const available = asset.availability === "available" && !alreadyAdded;
    return `<article class="asset-result">
      <div><strong>${escapeHtml(asset.product_title)} · ${escapeHtml(asset.variant_title)}</strong>
      <div class="muted small">${escapeHtml(asset.asset_number)} · ${escapeHtml(conditionLabel(asset.condition))} · ${escapeHtml(availabilityLabel(asset.availability))}${asset.recommended_deposit === null ? "" : ` · залог ${formatMoney(asset.recommended_deposit)}`}</div></div>
      <div class="asset-price"><input data-asset-price="${asset.id}" type="number" inputmode="decimal" min="0" step="0.01" placeholder="Цена, ₽" value="${asset.suggested_rental_price ?? ""}" ${available ? "" : "disabled"}>
      <button class="button compact" data-add-rental-asset="${asset.id}" ${available ? "" : "disabled"}>${alreadyAdded ? "Добавлен" : "Добавить"}</button></div>
    </article>`;
  }).join("");
}

function bindRentalAssetResults() {
  document.querySelectorAll("[data-add-rental-asset]").forEach((button) => {
    button.addEventListener("click", () => addRentalAsset(button.dataset.addRentalAsset));
  });
}

async function addRentalAsset(assetId) {
  const input = document.querySelector(`[data-asset-price="${assetId}"]`);
  if (!input.value) return showToast("Укажите стоимость аренды", true);
  try {
    state.rental.order = await api(`/rental/orders/${state.rental.order.id}/items`, {
      method: "POST",
      body: JSON.stringify({
        rental_asset_id: assetId,
        agreed_price: input.value,
        discount: "0",
      }),
    });
    const asset = state.rental.assets.find((item) => item.id === assetId);
    if (asset?.recommended_deposit !== null && asset?.recommended_deposit !== undefined) {
      const currentDeposit = Number(state.rental.order.deposit_amount);
      state.rental.order = await api(`/rental/orders/${state.rental.order.id}`, {
        method: "PATCH",
        body: JSON.stringify({ deposit_amount: String(currentDeposit + Number(asset.recommended_deposit)) }),
      });
    }
    await loadDefaultRentalAssets();
    renderRentalDraft();
    showToast("Предмет аренды добавлен");
  } catch (error) { showToast(error.message, true); }
}

async function loadDefaultRentalAssets() {
  state.rental.assets = await api("/api/rental/assets?availability=available&limit=100");
}

async function removeRentalItem(itemId) {
  try {
    await api(`/rental/orders/${state.rental.order.id}/items/${itemId}`, { method: "DELETE" });
    state.rental.order = await api(`/rental/orders/${state.rental.order.id}`);
    renderRentalDraft();
    showToast("Позиция удалена");
  } catch (error) { showToast(error.message, true); }
}

async function issueRentalOrder() {
  if (!window.confirm(`Выдать заказ ${state.rental.order.order_number}? После выдачи редактирование будет недоступно.`)) return;
  const button = document.querySelector("#issue-rental-order");
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span> Выдаём';
  try {
    state.rental.order = await api(`/rental/orders/${state.rental.order.id}/issue`, { method: "POST" });
    renderRentalIssued();
  } catch (error) {
    showToast(error.message, true);
    button.disabled = false;
    button.textContent = "Выдать";
  }
}

function renderRentalIssued() {
  const order = state.rental.order;
  root.innerHTML = `<div class="shell">
    ${topbar()}
    <div class="result" style="margin-top:36px">
      <div class="result-mark">✓</div>
      <p class="eyebrow">Аренда выдана</p>
      <h1>${escapeHtml(order.order_number)}</h1>
      <p><strong>${escapeHtml(order.customer_name_snapshot)}</strong><br>${escapeHtml(order.customer_phone_snapshot)}</p>
      <div class="chips" style="justify-content:center">
        <span class="chip good">Предметов аренды: ${order.items.length}</span>
        <span class="chip">Возврат: ${formatShortDate(order.planned_return_at)}</span>
      </div>
      <button class="button full" id="rental-issued-done" style="margin-top:20px">Готово</button>
    </div>
  </div>`;
  bindTopbar();
  document.querySelector("#rental-issued-done").addEventListener("click", loadHome);
}

async function openRentalReturnHome(query = "") {
  try {
    logicalParent = () => openRentalHub();
    const suffix = query ? `&query=${encodeURIComponent(query)}` : "";
    const orders = await api(`/rental/orders?order_status=issued${suffix}`);
    state.rental.drafts = orders;
    state.rental.order = null;
    state.rental.returnAssets = new Map();
    renderRentalReturnHome(query);
  } catch (error) { showToast(error.message, true); }
}

function renderRentalReturnHome(query = "") {
  const rows = state.rental.drafts.length
    ? state.rental.drafts.map((order) => `
      <button class="session-row" data-return-order="${order.id}">
        <span>
          <strong>${escapeHtml(order.order_number)}</strong>
          ${order.is_overdue ? '<span class="chip warn inline-chip">Просрочен</span>' : ""}
          <br><span class="muted small">${escapeHtml(order.customer_name_snapshot)} · ${escapeHtml(order.customer_phone_snapshot)} · возврат ${formatShortDate(order.planned_return_at)}</span>
        </span>
        <span aria-hidden="true">→</span>
      </button>`).join("")
    : '<div class="empty">Активные аренды не найдены</div>';
  root.innerHTML = `<div class="shell">
    ${topbar(true)}
    <p class="eyebrow">Rental return</p>
    <h1>Возврат аренды</h1>
    <p class="muted">Поиск по договору, клиенту, телефону или инвентарному номеру.</p>
    <form class="search-row" id="return-order-search-form">
      <input name="query" value="${escapeHtml(query)}" autocomplete="off" placeholder="RORD-, RENT-, имя или телефон">
      <button class="button" type="submit">Найти</button>
    </form>
    <div class="session-list">${rows}</div>
  </div>`;
  bindTopbar();
  document.querySelector("#return-order-search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    openRentalReturnHome(String(new FormData(event.currentTarget).get("query") || "").trim());
  });
  document.querySelectorAll("[data-return-order]").forEach((button) => {
    button.addEventListener("click", () => openRentalReturnOrder(button.dataset.returnOrder));
  });
}

async function openRentalReturnOrder(orderId) {
  try {
    recordRoute("order", { orderId });
    state.rental.order = await api(`/rental/orders/${orderId}`);
    logicalParent = () => selectRentalCustomer(state.rental.order.customer_id);
    const assetRows = await Promise.all(state.rental.order.items.map(async (item) => {
      const matches = await api(`/api/operations/rental/assets?query=${encodeURIComponent(item.asset_number_snapshot)}`);
      return [item.rental_asset_id, matches.find((asset) => asset.id === item.rental_asset_id)];
    }));
    state.rental.returnAssets = new Map(assetRows);
    state.operations.assets = [
      ...state.operations.assets,
      ...assetRows.map(([, asset]) => asset).filter((asset) => asset && !state.operations.assets.some((current) => current.id === asset.id)),
    ];
    renderRentalReturnOrder();
  } catch (error) { showToast(error.message, true); }
}

function renderRentalReturnOrder() {
  const order = state.rental.order;
  const total = order.items.reduce(
    (sum, item) => sum + Number(item.agreed_price) - Number(item.discount),
    0,
  );
  const activeItems = order.items.filter((item) => item.status === "issued");
  const itemCards = order.items.map((item) => {
    const asset = state.rental.returnAssets.get(item.rental_asset_id);
    const completed = item.status !== "issued";
    return `<article class="card return-item ${completed ? "completed-item" : ""}" data-return-item="${item.id}">
      <div class="return-item-head">
        ${completed ? "" : `<input class="return-check" type="checkbox" data-return-select="${item.id}" aria-label="Выбрать ${escapeHtml(item.asset_number_snapshot)}">`}
        <div>
          <h3>${escapeHtml(item.title_snapshot)}</h3>
          <div class="muted small">${escapeHtml(item.asset_number_snapshot)} · ${escapeHtml(itemStatusLabel(item.status))}${asset ? ` · ${escapeHtml(availabilityLabel(asset.availability))}` : ""}</div>
        </div>
        ${asset ? `<button class="button ghost compact" data-return-open-asset="${asset.id}">Предмет аренды →</button>` : ""}
      </div>
      ${completed ? `<div class="chips"><span class="chip ${item.status === "lost" ? "warn" : "good"}">${escapeHtml(itemStatusLabel(item.status))}</span></div>` : `
      <div class="field-row">
        <div class="field"><label>Результат</label><select data-return-outcome="${item.id}">
          <option value="returned">Возвращён</option>
          <option value="lost">LOST — не возвращён</option>
        </select></div>
        <div class="field"><label>Осмотр</label><select data-return-condition="${item.id}">
          <option value="good">Без замечаний</option>
          <option value="fair">Имеются замечания</option>
          <option value="damaged">Повреждён</option>
          <option value="unusable">Непригоден</option>
        </select></div>
      </div>
      <div class="field-row">
        <div class="field"><label>Итоговая сумма, ₽</label><input data-return-charge="${item.id}" type="number" inputmode="decimal" min="0" step="0.01" value="${Number(item.agreed_price) - Number(item.discount)}" required></div>
        <div class="field"><label>Комментарий</label><input data-return-note="${item.id}" placeholder="Необязательно"></div>
      </div>
      <div class="damage-capture" data-return-damage-block="${item.id}">
        <div class="field"><label>Повреждение <span class="muted">(если обнаружено)</span></label><textarea data-return-damage-description="${item.id}" placeholder="Опишите повреждение"></textarea></div>
        <div class="field-row"><div class="field"><label>Серьёзность</label><select data-return-damage-severity="${item.id}"><option value="minor">Незначительное</option><option value="moderate">Среднее</option><option value="major">Серьёзное</option><option value="critical">Критическое</option></select></div><div class="field"><label>Комментарий</label><input data-return-damage-comment="${item.id}" placeholder="Необязательно"></div></div>
      </div>`}
    </article>`;
  }).join("");
  root.innerHTML = `<div class="shell">
    ${topbar(true)}
    <p class="eyebrow">Активная аренда · ${escapeHtml(order.order_number)}</p>
    <h1>${escapeHtml(order.customer_name_snapshot)}</h1>
    <p class="muted">${escapeHtml(order.customer_phone_snapshot)}</p>
    <button class="button ghost compact" id="return-open-customer">Клиент →</button>
    <section class="card order-facts">
      <div><span class="muted small">Срок</span><strong>${formatShortDate(order.planned_start_at)} — ${formatShortDate(order.planned_return_at)}</strong></div>
      <div><span class="muted small">Стоимость</span><strong>${formatMoney(total)}</strong></div>
      <div><span class="muted small">Залог</span><strong>${formatMoney(Number(order.deposit_amount))}</strong></div>
      <div><span class="muted small">Статус</span><strong>${order.is_overdue ? "Просрочен" : "Активен"}</strong></div>
    </section>
    <div class="section-heading"><h2>Предметы аренды · ${order.items.length}</h2><span class="muted small">Ожидают: ${activeItems.length}</span></div>
    <div>${itemCards}</div>
    ${activeItems.length ? `<section class="checkout-summary">
      <button class="button secondary" id="complete-selected-items" disabled>Завершить выбранные</button>
      <button class="button" id="complete-all-items">Завершить все</button>
    </section>` : ""}
  </div>`;
  bindTopbar();
  document.querySelectorAll("[data-return-select]").forEach((checkbox) => {
    checkbox.addEventListener("change", updateReturnSelection);
  });
  document.querySelectorAll("[data-return-outcome]").forEach((select) => {
    select.addEventListener("change", () => updateReturnOutcome(select.dataset.returnOutcome));
  });
  document.querySelectorAll("[data-return-open-asset]").forEach((button) => {
    button.addEventListener("click", () => openOperationsAsset(button.dataset.returnOpenAsset));
  });
  document.querySelector("#return-open-customer").addEventListener("click", () => selectRentalCustomer(order.customer_id));
  document.querySelector("#complete-selected-items")?.addEventListener("click", () => completeRentalItems(false));
  document.querySelector("#complete-all-items")?.addEventListener("click", () => completeRentalItems(true));
}

function updateReturnSelection() {
  const count = document.querySelectorAll("[data-return-select]:checked").length;
  const button = document.querySelector("#complete-selected-items");
  if (button) {
    button.disabled = count === 0;
    button.textContent = count ? `Завершить выбранные · ${count}` : "Завершить выбранные";
  }
}

function updateReturnOutcome(itemId) {
  const outcome = document.querySelector(`[data-return-outcome="${itemId}"]`).value;
  const condition = document.querySelector(`[data-return-condition="${itemId}"]`);
  condition.disabled = outcome === "lost";
  const damageBlock = document.querySelector(`[data-return-damage-block="${itemId}"]`);
  if (damageBlock) damageBlock.classList.toggle("hidden", outcome === "lost");
}

function buildReturnCompletion(itemId) {
  const outcome = document.querySelector(`[data-return-outcome="${itemId}"]`).value;
  const note = nullableText(document.querySelector(`[data-return-note="${itemId}"]`).value);
  return {
    item_id: itemId,
    outcome,
    ...(outcome === "returned"
      ? { condition: document.querySelector(`[data-return-condition="${itemId}"]`).value }
      : {}),
    charged_amount: document.querySelector(`[data-return-charge="${itemId}"]`).value,
    note,
  };
}

async function completeRentalItems(all) {
  const itemIds = all
    ? state.rental.order.items.filter((item) => item.status === "issued").map((item) => item.id)
    : [...document.querySelectorAll("[data-return-select]:checked")].map((input) => input.dataset.returnSelect);
  if (!itemIds.length) return;
  const hasLost = itemIds.some((id) => document.querySelector(`[data-return-outcome="${id}"]`).value === "lost");
  const prompt = all ? "Завершить все оставшиеся позиции?" : `Завершить выбранные позиции: ${itemIds.length}?`;
  if (!window.confirm(`${prompt}${hasLost ? " Среди них есть LOST." : ""}`)) return;
  try {
    const damageEntries = itemIds.map((itemId) => {
      const description = nullableText(document.querySelector(`[data-return-damage-description="${itemId}"]`)?.value);
      const item = state.rental.order.items.find((value) => value.id === itemId);
      return description && item ? {
        assetId: item.rental_asset_id,
        payload: {
          order_item_id: itemId,
          description,
          severity: document.querySelector(`[data-return-damage-severity="${itemId}"]`).value,
          comment: nullableText(document.querySelector(`[data-return-damage-comment="${itemId}"]`).value),
        },
      } : null;
    }).filter(Boolean);
    state.rental.order = await api(`/rental/orders/${state.rental.order.id}/complete-items`, {
      method: "POST",
      body: JSON.stringify({ items: itemIds.map(buildReturnCompletion) }),
    });
    const damageResults = await Promise.allSettled(damageEntries.map((entry) => api(`/api/operations/rental/assets/${entry.assetId}/damages`, { method: "POST", body: JSON.stringify(entry.payload) })));
    const damageFailed = damageResults.some((result) => result.status === "rejected");
    if (state.rental.order.status === "closed") {
      renderRentalReturnCompleted();
      if (damageFailed) showToast("Возврат завершён, но часть повреждений не сохранилась.", true);
      return;
    }
    await openRentalReturnOrder(state.rental.order.id);
    showToast(damageFailed ? "Возврат сохранён, но часть повреждений не сохранилась." : "Частичный возврат сохранён. Договор остаётся активным.", damageFailed);
  } catch (error) { showToast(error.message, true); }
}

function renderRentalReturnCompleted() {
  const order = state.rental.order;
  root.innerHTML = `<div class="shell">
    ${topbar()}
    <div class="result" style="margin-top:36px">
      <div class="result-mark">✓</div>
      <p class="eyebrow">Возврат завершён</p>
      <h1>${escapeHtml(order.order_number)}</h1>
      <p>Все позиции договора завершены. Заказ закрыт автоматически.</p>
      <div class="chips" style="justify-content:center">
        <span class="chip good">Возвращено: ${order.items.filter((item) => item.status === "returned").length}</span>
        ${order.items.some((item) => item.status === "lost") ? `<span class="chip warn">LOST: ${order.items.filter((item) => item.status === "lost").length}</span>` : ""}
      </div>
      <button class="button full" id="rental-return-done" style="margin-top:20px">Готово</button>
    </div>
  </div>`;
  bindTopbar();
  document.querySelector("#rental-return-done").addEventListener("click", loadHome);
}

function toLocalInput(date) {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

function formatShortDate(value) {
  return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(value));
}

function formatMoney(value) {
  return new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 2 }).format(value);
}

function formatQuantity(value) {
  return `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 }).format(Number(value))} шт.`;
}

function formatPercent(value) {
  return new Intl.NumberFormat("ru-RU", { style: "percent", maximumFractionDigits: 1 }).format(Number(value));
}

function efficiencyChip(value) {
  const labels = { paid_back: "Окупился", not_paid_back: "Не окупился", never_rented: "Нет аренд", high_expenses: "Высокие расходы", long_idle: "Давно не сдавался" };
  return `<span class="chip ${value === "paid_back" ? "good" : value === "high_expenses" ? "warn" : ""}">${labels[value] || escapeHtml(value)}</span>`;
}

function conditionLabel(value) {
  return { new: "Новое", good: "Хорошее", fair: "Удовлетворительное", damaged: "Повреждено", unusable: "Непригодно" }[value] || value;
}

function availabilityLabel(value) {
  return { available: "Доступен", rented: "Выдан", maintenance: "Обслуживание" }[value] || value;
}

function itemStatusLabel(value) {
  return { prepared: "Подготовлен", issued: "Выдан", returned: "Возвращён", lost: "LOST", cancelled: "Отменён" }[value] || value;
}

async function loadImageUrl(id) {
  let url = state.imageUrls.get(id);
  if (url) return url;
  const response = await fetch(`/api/media/images/${id}/source`, {
    headers: { Authorization: `Bearer ${state.token}` },
  });
  if (!response.ok) throw new Error("Не удалось открыть изображение");
  url = URL.createObjectURL(await response.blob());
  state.imageUrls.set(id, url);
  return url;
}

async function hydrateImages() {
  await Promise.all([...document.querySelectorAll("[data-image-id]")].map(async (element) => {
    const id = element.dataset.imageId;
    try {
      element.src = await loadImageUrl(id);
    } catch { /* a missing preview must not block intake */ }
  }));
}

function bindImagePreviews() {
  document.querySelectorAll("[data-image-preview]").forEach((element) => {
    element.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      openImagePreview(element.dataset.imagePreview, element.alt);
    });
    element.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      event.stopPropagation();
      openImagePreview(element.dataset.imagePreview, element.alt);
    });
  });
}

async function openImagePreview(imageId, alt = "Фото товара") {
  const dialog = document.createElement("dialog");
  dialog.className = "image-preview-dialog";
  dialog.setAttribute("aria-label", "Просмотр фотографии");
  dialog.innerHTML = `<button class="image-preview-close" type="button" aria-label="Закрыть">×</button>
    <div class="image-preview-loading">Загрузка фотографии…</div>
    <img alt="${escapeHtml(alt || "Фото товара")}">`;
  document.body.append(dialog);
  const close = () => dialog.close();
  dialog.querySelector(".image-preview-close").addEventListener("click", close);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) close();
  });
  dialog.addEventListener("close", () => dialog.remove(), { once: true });
  dialog.showModal();
  try {
    const url = await loadImageUrl(imageId);
    if (!dialog.isConnected) return;
    dialog.querySelector("img").src = url;
    dialog.querySelector("img").classList.add("ready");
    dialog.querySelector(".image-preview-loading").remove();
  } catch (error) {
    if (dialog.isConnected) dialog.querySelector(".image-preview-loading").textContent = error.message;
  }
}

function formatDate(value) {
  return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

if (state.token) bootstrap(); else renderLogin();
