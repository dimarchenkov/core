const root = document.querySelector("#app");
const toast = document.querySelector("#toast");
let logicalParent = () => loadHome();
let restoringHistory = false;
let zxingLoader = null;
let intakeSearchTimer = null;
let scannerService = null;
let salesProgressTimer = null;
let salesProgressSaleId = null;
let salesProgressStartedAt = 0;
let salesFiscalPendingObservedAt = 0;
let checkoutHealthTimer = null;
let checkoutHealthView = { state: "READY", detail: "", context: "" };
let checkoutHealthPollTimer = null;
let checkoutHealthRequestSequence = 0;
let checkoutHealthRequestInFlight = null;
let checkoutHealthCache = null;
let checkoutHealthFetchedAt = 0;
let intakeRealtimeController = null;
let intakeRealtimeSessionId = null;
let intakeRealtimeReconnectTimer = null;
let intakeRealtimePollTimer = null;
let intakeRealtimeRefreshTimer = null;
let intakeRealtimeRefreshInFlight = null;
let intakeRealtimePendingVersion = 0;
let intakeRealtimeForceRefresh = false;
let intakeRealtimeRetryMs = 1000;
let intakeRealtimeStatus = "connecting";
let intakeWriteChain = Promise.resolve();

function recordRoute(name, data = {}) {
  if (name !== "intake") scannerService?.clearLocalHandler();
  if (name !== "intake") stopIntakeRealtime();
  const route = { name, ...data };
  if (restoringHistory) return;
  if (window.history.state?.coreRoute?.name === name
      && JSON.stringify(window.history.state.coreRoute) === JSON.stringify(route)) return;
  const method = window.history.state?.coreRoute ? "pushState" : "replaceState";
  window.history[method]({ coreRoute: route }, "", routeUrl(route));
}

function routeUrl(route) {
  if (route.name === "product") return `${window.location.pathname}#product/${route.productId}`;
  if (route.name === "collection") return `${window.location.pathname}#collection/${route.collectionId}`;
  if (route.name === "collections") return `${window.location.pathname}#collections`;
  if (route.name === "customer") return `${window.location.pathname}#customer/${route.customerId}`;
  if (route.name === "order") return `${window.location.pathname}#order/${route.orderId}`;
  if (route.name === "intake") return `${window.location.pathname}#intake/${route.sessionId}`;
  if (route.name === "sale") return `${window.location.pathname}#sale${route.saleId ? `/${route.saleId}` : ""}`;
  if (route.name === "operations") return `${window.location.pathname}#operations${route.state && route.state !== "all" ? `?state=${route.state}` : ""}`;
  if (route.name === "operation-detail") return `${window.location.pathname}#operation/${route.saleId}`;
  if (route.name === "analytics") {
    const params = new URLSearchParams();
    params.set("period", route.period || "today");
    if (route.startDate) params.set("start_date", route.startDate);
    if (route.endDate) params.set("end_date", route.endDate);
    if (route.ranking && route.ranking !== "revenue") params.set("ranking", route.ranking);
    return `${window.location.pathname}#analytics?${params.toString()}`;
  }
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
  if (name === "collection" && id) return { name, collectionId: id };
  if (name === "customer" && id) return { name, customerId: id };
  if (name === "order" && id) return { name, orderId: id };
  if (name === "intake" && id) return { name, sessionId: id };
  if (name === "sale") return { name, saleId: id || undefined };
  if (name === "operations") {
    const params = new URLSearchParams(queryString);
    return { name, state: params.get("state") || "all" };
  }
  if (name === "operation" && id) return { name: "operation-detail", saleId: id };
  if (name === "analytics") {
    const params = new URLSearchParams(queryString);
    return {
      name,
      period: params.get("period") || "today",
      startDate: params.get("start_date") || undefined,
      endDate: params.get("end_date") || undefined,
      ranking: params.get("ranking") || "revenue",
    };
  }
  if (name === "catalog") {
    const params = new URLSearchParams(queryString);
    return {
      name,
      mode: params.get("mode") || undefined,
      status: params.get("status") || undefined,
      query: params.get("query") || undefined,
      categoryId: params.get("category_id") || undefined,
      collectionId: params.get("collection_id") || undefined,
      supplierId: params.get("supplier_id") || undefined,
      attention: params.getAll("attention"),
      productFilter: params.get("product_filter") || undefined,
      sort: params.get("sort") || undefined,
    };
  }
  if (["workspace", "rental", "settings", "collections"].includes(name)) return { name };
  return null;
}

async function restoreRoute(route) {
  restoringHistory = true;
  try {
    await flushProductAutosaves();
    if (!route || route.name === "workspace") await loadHome();
    else if (route.name === "catalog") await openOperationsCatalog(route);
    else if (route.name === "product") await openOperationsProduct(route.productId);
    else if (route.name === "collections") await openCollections();
    else if (route.name === "collection") await openCollection(route.collectionId);
    else if (route.name === "rental") await openRentalHub();
    else if (route.name === "settings") await openSettings();
    else if (route.name === "sale") await openSalesWorkspace(route.saleId);
    else if (route.name === "operations") await openCheckoutOperations(route.state);
    else if (route.name === "operation-detail") await openCheckoutOperationDetail(route.saleId);
    else if (route.name === "analytics") await openSalesAnalytics(route);
    else if (route.name === "customer") await selectRentalCustomer(route.customerId);
    else if (route.name === "order") await openRentalOrder(route.orderId);
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
    tariffs: [],
    selectedAssetId: null,
    selectedTariffId: null,
    editingItemId: null,
    itemEditorOpen: false,
    readyOrders: [],
    activeOrders: [],
    historyOrders: [],
    workspaceView: "current",
    issuePreview: null,
    agreement: null,
    agreementPreview: null,
    depositEditing: false,
    returnPreview: null,
    returnItemIds: [],
    timerHandle: null,
    returnAssets: new Map(),
  },
  operations: {
    catalog: null,
    categoryManagement: null,
    collections: [],
    collection: null,
    collectionVariants: [],
    collectionSearch: [],
    variantCollections: new Map(),
    products: [],
    product: null,
    imageLinks: [],
    assets: [],
    asset: null,
    aqsi: new Map(),
    tariffs: new Map(),
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

let intakeCompletionInFlight = false;

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
  const active = state.sales.drafts.find((sale) => sale.id === candidate && sale.status === "draft") || null;
  state.sales.activeId = active?.id || null;
  state.sales.active = active;
  storeActiveSaleId(state.sales.activeId);
  return active;
}

function rememberSale(sale, makeActive = true) {
  state.sales.drafts = [sale, ...state.sales.drafts.filter((item) => item.id !== sale.id)]
    .filter((item) => ["draft", "fiscalization_pending", "fiscalization_failed"].includes(item.status));
  if (makeActive) {
    state.sales.activeId = sale.status === "draft" ? sale.id : null;
    state.sales.active = sale.status === "draft" ? sale : null;
    storeActiveSaleId(state.sales.activeId);
  }
  updateSalesIndicator();
}

function selectActiveSale(saleId) {
  const sale = state.sales.drafts.find((item) => item.id === saleId) || null;
  state.sales.activeId = sale?.status === "draft" ? sale.id : null;
  state.sales.active = sale?.status === "draft" ? sale : null;
  storeActiveSaleId(state.sales.activeId);
  updateSalesIndicator();
  return sale;
}

function renderSalesIndicator() {
  const sale = state.sales.active;
  const attention = state.sales.drafts.filter((item) => ["payment_pending", "fiscalization_pending", "fiscalization_failed"].includes(item.status));
  const summary = sale
    ? `<strong>🛒 Продажа #${sale.sale_number}</strong><span>${sale.item_quantity} поз. · ${formatMoney(sale.total_amount)}</span>`
    : attention.length
      ? `<strong>⚠ Чеки требуют внимания · ${attention.length}</strong><span>Фискализация продолжается на сервере</span>`
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
      detail = typeof payload.detail === "string" ? payload.detail : payload.detail?.message || detail;
    } catch { /* response is not JSON */ }
    const error = new Error(detail);
    error.status = response.status;
    error.detail = responseDetail;
    if (response.status === 409 && responseDetail?.code === "stale_intake_version") {
      scheduleIntakeRefresh(responseDetail.current_version, true);
    }
    throw error;
  }
  if (response.status === 204) return null;
  return response.json();
}

function serializeIntakeWrite(action) {
  const next = intakeWriteChain.then(action, action);
  intakeWriteChain = next.catch(() => {});
  return next;
}

function updateIntakeRealtimeStatus(status) {
  intakeRealtimeStatus = status;
  const indicator = document.querySelector("#intake-sync-status");
  if (!indicator) return;
  indicator.classList.toggle("connected", status === "connected");
  indicator.textContent = status === "connected" ? "● Синхронизировано" : "● Переподключение…";
}

function startIntakeRealtime(sessionId) {
  stopIntakeRealtime();
  intakeRealtimeSessionId = sessionId;
  intakeRealtimeRetryMs = 1000;
  updateIntakeRealtimeStatus("connecting");
  connectIntakeRealtime();
}

function stopIntakeRealtime() {
  intakeRealtimeController?.abort();
  intakeRealtimeController = null;
  intakeRealtimeSessionId = null;
  clearTimeout(intakeRealtimeReconnectTimer);
  clearTimeout(intakeRealtimeRefreshTimer);
  clearInterval(intakeRealtimePollTimer);
  intakeRealtimeReconnectTimer = null;
  intakeRealtimeRefreshTimer = null;
  intakeRealtimePollTimer = null;
  intakeRealtimePendingVersion = 0;
  intakeRealtimeForceRefresh = false;
}

function startIntakeFallbackPolling() {
  if (intakeRealtimePollTimer) return;
  intakeRealtimePollTimer = setInterval(() => scheduleIntakeRefresh(null, true), 25000);
}

async function connectIntakeRealtime() {
  const sessionId = intakeRealtimeSessionId;
  if (!sessionId || !state.token) return;
  const controller = new AbortController();
  intakeRealtimeController = controller;
  let connected = false;
  try {
    const response = await fetch(`/api/intake/sessions/${sessionId}/events`, {
      headers: { Authorization: `Bearer ${state.token}`, Accept: "text/event-stream" },
      cache: "no-store",
      signal: controller.signal,
    });
    if (response.status === 401) {
      logout();
      return;
    }
    if (!response.ok || !response.body) throw new Error(`SSE ${response.status}`);
    connected = true;
    intakeRealtimeRetryMs = 1000;
    clearInterval(intakeRealtimePollTimer);
    intakeRealtimePollTimer = null;
    updateIntakeRealtimeStatus("connected");
    scheduleIntakeRefresh(null, true);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (intakeRealtimeSessionId === sessionId) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replaceAll("\r\n", "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = block.split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (data) {
          try {
            const event = JSON.parse(data);
            if (event.intake_id === sessionId && event.version > (state.session?.version || 0)) {
              scheduleIntakeRefresh(event.version);
            }
          } catch { /* malformed transport notification is ignored */ }
        }
        boundary = buffer.indexOf("\n\n");
      }
    }
  } catch (error) {
    if (error.name === "AbortError") return;
  } finally {
    if (intakeRealtimeSessionId !== sessionId || controller.signal.aborted) return;
    intakeRealtimeController = null;
    updateIntakeRealtimeStatus("reconnecting");
    startIntakeFallbackPolling();
    const delay = connected ? 1000 : intakeRealtimeRetryMs;
    intakeRealtimeRetryMs = Math.min(intakeRealtimeRetryMs * 2, 15000);
    intakeRealtimeReconnectTimer = setTimeout(connectIntakeRealtime, delay);
  }
}

function scheduleIntakeRefresh(version = null, force = false) {
  if (!intakeRealtimeSessionId) return;
  if (!force && version !== null && version <= (state.session?.version || 0)) return;
  if (version !== null) intakeRealtimePendingVersion = Math.max(intakeRealtimePendingVersion, version);
  intakeRealtimeForceRefresh ||= force;
  clearTimeout(intakeRealtimeRefreshTimer);
  intakeRealtimeRefreshTimer = setTimeout(() => refreshIntakeFromServer(), 180);
}

async function refreshIntakeFromServer() {
  if (intakeRealtimeRefreshInFlight) return intakeRealtimeRefreshInFlight;
  const sessionId = intakeRealtimeSessionId;
  if (!sessionId || state.session?.id !== sessionId) return;
  intakeRealtimePendingVersion = 0;
  intakeRealtimeForceRefresh = false;
  intakeRealtimeRefreshInFlight = (async () => {
    try {
      const latest = await api(`/api/intake/sessions/${sessionId}`);
      const changed = latest.version > (state.session?.version || 0)
        || latest.status !== state.session?.status;
      state.session = latest;
      if (!changed) return;
      await loadItemDisplays();
      if (latest.status === "completed") {
        state.result = await api(`/api/intake/sessions/${sessionId}/complete`);
        stopIntakeRealtime();
        await loadIntakeAqsi();
        renderResult();
        return;
      }
      if (latest.status !== "draft") stopIntakeRealtime();
      renderWorkspace();
    } catch (error) {
      if (error.status === 404) {
        stopIntakeRealtime();
        state.session = null;
        await loadHome();
        showToast("Приёмка удалена на другом устройстве");
      }
    } finally {
      intakeRealtimeRefreshInFlight = null;
      if (intakeRealtimeSessionId === sessionId && (
        intakeRealtimeForceRefresh
        || intakeRealtimePendingVersion > (state.session?.version || 0)
      )) scheduleIntakeRefresh(intakeRealtimePendingVersion, intakeRealtimeForceRefresh);
    }
  })();
  return intakeRealtimeRefreshInFlight;
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
  stopIntakeRealtime();
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
        <button class="action-card" id="open-analytics"><span class="action-icon">↗</span><strong>Аналитика</strong><span class="muted small">Выручка: продажи и аренда</span></button>
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
  document.querySelector("#open-analytics").addEventListener("click", () => openSalesAnalytics());
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
    const [aqsi, agreement] = await Promise.all([
      api("/api/settings/integrations/aqsi"),
      api("/api/settings/rental/agreement"),
    ]);
    renderSettings(aqsi, agreement);
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

function catalogSyncStatusLabel(reconciliation) {
  if (!reconciliation) return "Не проверялся";
  if (reconciliation.status === "healthy") return "Согласован";
  if (reconciliation.status === "repairing") return "Восстанавливается";
  if (reconciliation.status === "needs_attention") return "Требует внимания";
  if (reconciliation.status === "failed") return "Ошибка проверки";
  return "Проверяется";
}

function renderAqsiReconciliation(reconciliation) {
  if (!reconciliation) {
    return `<div class="settings-warning"><strong>Каталог AQSI ещё не сверялся</strong><p class="muted small">Подключение к API не подтверждает наличие товаров. Запустите сверку каталога.</p></div>`;
  }
  const good = reconciliation.status === "healthy";
  const warning = reconciliation.status === "needs_attention" || reconciliation.status === "failed";
  return `<div class="settings-warning ${good ? "settings-ok" : ""}">
    <div class="section-heading"><strong>Состояние каталога</strong><span class="chip ${good ? "good" : warning ? "warn" : ""}">${escapeHtml(catalogSyncStatusLabel(reconciliation))}</span></div>
    <div class="settings-facts">
      <div><span class="muted small">Ожидается из Core</span><strong>${reconciliation.expected_count}</strong></div>
      <div><span class="muted small">AQSI, управляется Core</span><strong>${reconciliation.remote_managed_count}</strong></div>
      <div><span class="muted small">Отсутствуют</span><strong>${reconciliation.missing_count}</strong></div>
      <div><span class="muted small">Не совпадают</span><strong>${reconciliation.mismatched_count}</strong></div>
      <div><span class="muted small">Чужие товары AQSI</span><strong>${reconciliation.unmanaged_remote_count}</strong></div>
      <div><span class="muted small">Кандидаты на удаление</span><strong>${reconciliation.deletion_candidate_count}</strong></div>
    </div>
    ${reconciliation.destructive_actions_blocked ? `<p class="muted small">Удаление в AQSI заблокировано. Core не удаляет внешние товары автоматически${reconciliation.deletion_guard_triggered ? " — сработал предохранитель массового удаления" : ""}.</p>` : ""}
    ${reconciliation.error_message ? `<p class="muted small">${escapeHtml(reconciliation.error_message)}</p>` : ""}
    <p class="muted small">Последняя сверка: ${formatDate(reconciliation.completed_at || reconciliation.started_at)}</p>
  </div>`;
}

function renderSettings(aqsi, agreement) {
  const integration = aqsi.integration;
  const configuration = integration?.configuration || {};
  const savedAt = integration?.credential_rotated_at || integration?.credential_saved_at;
  root.innerHTML = `<div class="shell settings-shell">
    ${topbar(true)}
    <p class="eyebrow">Настройки</p>
    <h1>Настройки</h1>
    <p class="muted">Интеграции и операционные правила Core.</p>
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
        ${renderAqsiReconciliation(aqsi.catalog_reconciliation)}
        <label class="settings-toggle"><input id="aqsi-auto-sync" type="checkbox" ${configuration.catalog_sync_enabled ? "checked" : ""} ${integration.enabled && savedAt ? "" : "disabled"}> <span>Автоматическая синхронизация каталога</span></label>
        <p class="muted small">Каждые 5 минут Core сравнивает полное множество управляемых товаров, восстанавливает отсутствующие и исправляет расхождения. Удаление в AQSI не выполняется автоматически.</p>
        <div class="inline-actions"><button class="button secondary" id="sync-aqsi-now" type="button" ${integration.enabled && savedAt ? "" : "disabled"}>Проверить и восстановить</button><button class="button ghost" id="refresh-aqsi-sync" type="button">Обновить статус</button></div>
      </div>` : `<form id="new-aqsi-form">
        <div class="field"><label for="new-aqsi-key">API key</label><input id="new-aqsi-key" name="api_key" type="password" autocomplete="new-password" required></div>
        <label class="settings-toggle"><input name="enabled" type="checkbox" checked> <span>Интеграция включена</span></label>
        <button class="button" type="submit">Подключить AQSI</button>
      </form>`}
    </section>
    ${renderAgreementSettings(agreement)}
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
  document.querySelector("#refresh-aqsi-sync")?.addEventListener("click", () => openSettings());
  document.querySelector("#agreement-lessor-form")?.addEventListener("submit", saveAgreementLessorSettings);
  document.querySelector("#agreement-template-preview")?.addEventListener("click", previewAgreementTemplate);
  document.querySelector("#agreement-template-form")?.addEventListener("submit", saveAgreementTemplate);
}

function renderAgreementSettings(context) {
  const settings = context.settings;
  const template = context.current_template;
  const placeholders = context.supported_placeholders.map((name) => `<code>{{ ${escapeHtml(name)} }}</code>`).join("");
  const history = context.history.map((item) => `<div class="settings-version-row"><span><strong>Версия ${item.template_version}</strong><small>${formatDate(item.created_at)}${item.change_note ? ` · ${escapeHtml(item.change_note)}` : ""}</small></span><code>${escapeHtml(item.content_hash.slice(0, 12))}…</code></div>`).join("");
  return `<section class="card agreement-settings-card">
    <div class="section-heading"><div><p class="eyebrow">Аренда → Договор</p><h2>Договор проката</h2></div><span class="chip good">Версия ${template.template_version}</span></div>
    <p class="muted small">Паспортные данные в Core не сохраняются: клиент заполняет пустые поля на распечатанном договоре.</p>
    <form id="agreement-lessor-form" class="settings-secret-form">
      <strong>Реквизиты арендодателя</strong>
      <div class="field"><label>Наименование / Ф.И.О.</label><input name="lessor_name" value="${escapeHtml(settings.lessor_name)}" required></div>
      <div class="field-row"><div class="field"><label>ИНН</label><input name="lessor_inn" value="${escapeHtml(settings.lessor_inn)}" required></div><div class="field"><label>ОГРНИП</label><input name="lessor_ogrnip" value="${escapeHtml(settings.lessor_ogrnip)}" required></div></div>
      <div class="field"><label>Город договора</label><input name="agreement_city" value="${escapeHtml(settings.agreement_city)}" required></div>
      <div class="field"><label>Адрес <span class="muted">(необязательно)</span></label><input name="lessor_address" value="${escapeHtml(settings.lessor_address)}"></div>
      <div class="field"><label>Телефон <span class="muted">(необязательно)</span></label><input name="lessor_phone" value="${escapeHtml(settings.lessor_phone)}"></div>
      <button class="button secondary" type="submit">Сохранить реквизиты</button>
    </form>
    <div class="divider"></div>
    <form id="agreement-template-form" class="agreement-template-form">
      <div class="section-heading"><strong>Markdown-шаблон</strong><span class="muted small">Текущая версия ${template.template_version}</span></div>
      <textarea name="markdown_source" rows="22" spellcheck="false" required>${escapeHtml(template.markdown_source)}</textarea>
      <div class="field"><label>Комментарий к новой версии</label><input name="change_note" maxlength="500" placeholder="Что изменилось"></div>
      <div class="inline-actions"><button class="button ghost" type="button" id="agreement-template-preview">Предпросмотр</button><button class="button" type="submit">Сохранить новую версию</button></div>
    </form>
    <div id="agreement-template-preview-result" class="agreement-preview-shell hidden"></div>
    <details><summary>Доступные placeholders</summary><div class="placeholder-reference">${placeholders}</div></details>
    <details><summary>История версий</summary><div class="settings-version-list">${history}</div></details>
  </section>`;
}

async function saveAgreementLessorSettings(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  try {
    await api("/api/settings/rental/agreement/lessor", { method: "PUT", body: JSON.stringify({
      lessor_name: String(data.get("lessor_name") || ""), lessor_inn: String(data.get("lessor_inn") || ""),
      lessor_ogrnip: String(data.get("lessor_ogrnip") || ""), agreement_city: String(data.get("agreement_city") || ""),
      lessor_address: String(data.get("lessor_address") || ""), lessor_phone: String(data.get("lessor_phone") || ""),
    }) });
    showToast("Реквизиты договора сохранены");
    await openSettings();
  } catch (error) { showToast(error.message, true); }
}

async function previewAgreementTemplate() {
  const form = document.querySelector("#agreement-template-form");
  const target = document.querySelector("#agreement-template-preview-result");
  try {
    const preview = await api("/api/settings/rental/agreement/preview", { method: "POST", body: JSON.stringify({ markdown_source: String(new FormData(form).get("markdown_source")) }) });
    target.innerHTML = preview.rendered_html;
    target.classList.remove("hidden");
  } catch (error) { showToast(error.message, true); }
}

async function saveAgreementTemplate(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  try {
    await api("/api/settings/rental/agreement/templates", { method: "POST", body: JSON.stringify({ markdown_source: String(data.get("markdown_source")), change_note: nullableText(data.get("change_note")) }) });
    showToast("Новая версия шаблона сохранена");
    await openSettings();
  } catch (error) { showToast(error.message, true); }
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

function analyticsQuery(filters) {
  const params = new URLSearchParams({
    period: filters.period || "today",
    ranking: filters.ranking || "revenue",
  });
  if (filters.period === "custom" && filters.startDate && filters.endDate) {
    params.set("start_date", filters.startDate);
    params.set("end_date", filters.endDate);
  }
  return params.toString();
}

async function openSalesAnalytics(options = {}) {
  const filters = {
    period: options.period || "today",
    startDate: options.startDate,
    endDate: options.endDate,
    ranking: options.ranking || "revenue",
  };
  recordRoute("analytics", filters);
  logicalParent = () => loadHome();
  root.innerHTML = `<div class="shell analytics-shell">${topbar(true)}<section class="analytics-loading"><strong>Собираем аналитику выручки…</strong></section></div>`;
  bindTopbar();
  try {
    const report = await api(`/api/analytics/sales?${analyticsQuery(filters)}`);
    renderSalesAnalytics(report, {
      ...filters,
      startDate: report.summary.period.start_date,
      endDate: report.summary.period.end_date,
    });
  } catch (error) {
    showToast(error.message, true);
    root.innerHTML = `<div class="shell analytics-shell">${topbar(true)}<section class="empty"><strong>Не удалось загрузить аналитику</strong><p>${escapeHtml(error.message)}</p><button class="button" id="analytics-retry" type="button">Повторить</button></section></div>`;
    bindTopbar();
    document.querySelector("#analytics-retry")?.addEventListener("click", () => openSalesAnalytics(filters));
  }
}

function analyticsPeriodLabel(period) {
  if (period.start_date === period.end_date) return formatAnalyticsDate(period.start_date);
  return `${formatAnalyticsDate(period.start_date)} — ${formatAnalyticsDate(period.end_date)}`;
}

function formatAnalyticsDate(value, options = {}) {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: options.short ? "short" : "2-digit",
    ...(options.year === false ? {} : { year: "numeric" }),
  }).format(new Date(`${value}T12:00:00`));
}

function renderAnalyticsBars(rows, valueKey, formatter, emptyLabel) {
  const maximum = Math.max(...rows.map((row) => Number(row[valueKey])), 0);
  if (!maximum) return `<div class="analytics-chart-empty">${emptyLabel}</div>`;
  return `<div class="analytics-chart" role="img" aria-label="${escapeHtml(emptyLabel)}">${rows.map((row) => {
    const value = Number(row[valueKey]);
    const height = value ? Math.max(6, Math.round((value / maximum) * 100)) : 2;
    return `<div class="analytics-bar-column" title="${formatAnalyticsDate(row.date)}: ${escapeHtml(formatter(value))}" aria-label="${formatAnalyticsDate(row.date)}: ${escapeHtml(formatter(value))}">
      <span class="analytics-bar-value">${escapeHtml(formatter(value))}</span>
      <span class="analytics-bar-track"><span class="analytics-bar" style="height:${height}%"></span></span>
      <span class="analytics-bar-date">${formatAnalyticsDate(row.date, { short: true, year: false })}</span>
    </div>`;
  }).join("")}</div>`;
}

function renderSalesAnalytics(report, filters) {
  const summary = report.summary;
  const periodPresets = [
    ["today", "Сегодня"],
    ["7_days", "7 дней"],
    ["30_days", "30 дней"],
    ["this_month", "Этот месяц"],
  ];
  const presetButtons = periodPresets.map(([value, label]) => `<button class="filter-chip ${filters.period === value ? "active" : ""}" data-analytics-period="${value}" type="button">${label}</button>`).join("");
  const productRows = report.top_products.length
    ? report.top_products.map((item, index) => `<tr><td><span class="analytics-rank">${index + 1}</span>${escapeHtml(item.product_title)}</td><td>${item.units}</td><td>${formatMoney(item.revenue)}</td></tr>`).join("")
    : '<tr><td colspan="3" class="muted">Нет продаж товаров за период</td></tr>';
  const variantRows = report.top_variants.length
    ? report.top_variants.map((item, index) => `<tr><td><span class="analytics-rank">${index + 1}</span><strong>${escapeHtml(item.display_label)}</strong><small>${escapeHtml(item.sku || "Без SKU")}</small></td><td>${item.units}</td><td>${formatMoney(item.revenue)}</td></tr>`).join("")
    : '<tr><td colspan="3" class="muted">Нет продаж вариантов за период</td></tr>';
  const freeSaleRows = report.top_free_sale_items.length
    ? report.top_free_sale_items.map((item, index) => `<tr><td><span class="analytics-rank">${index + 1}</span>${escapeHtml(item.display_name)}</td><td>${item.units}</td><td>${formatMoney(item.revenue)}</td></tr>`).join("")
    : '<tr><td colspan="3" class="muted">Нет свободных продаж за период</td></tr>';
  const rentalRows = report.top_rental_items.length
    ? report.top_rental_items.map((item, index) => `<tr><td><span class="analytics-rank">${index + 1}</span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.asset_number)}</small></td><td>${item.rental_count}</td><td>${formatMoney(item.revenue)}</td></tr>`).join("")
    : '<tr><td colspan="3" class="muted">Нет завершённых аренд за период</td></tr>';
  const weekdayRows = report.weekdays.map((item) => `<tr><td><strong>${escapeHtml(item.label)}</strong><small>${item.observed_days} ${item.observed_days === 1 ? "день" : "дн."}</small></td><td>${formatMoney(item.revenue)}<small>в среднем ${formatMoney(item.average_revenue)}</small></td><td>${item.receipt_count}<small>средний чек ${formatMoney(item.average_receipt)}</small></td></tr>`).join("");
  const dailyRows = report.daily.map((item) => `<tr><td>${formatAnalyticsDate(item.date)}</td><td><strong>${formatMoney(item.total_revenue)}</strong></td><td>${formatMoney(item.sales_revenue)}</td><td>${formatMoney(item.rental_revenue)}</td><td>${item.sale_count}</td><td>${item.rental_count}</td><td>${item.items_sold}</td></tr>`).join("");
  const payments = report.payment_methods.length
    ? `<section class="analytics-section"><div class="analytics-section-head"><div><p class="eyebrow">Оплата</p><h2>Способы оплаты</h2></div></div><div class="payment-breakdown">${report.payment_methods.map((item) => `<article class="card"><span>${escapeHtml(item.label)}</span><strong>${formatMoney(item.revenue)}</strong><small>${item.receipt_count} чек.</small></article>`).join("")}</div></section>`
    : "";
  root.innerHTML = `<div class="shell analytics-shell">
    ${topbar(true)}
    <header class="analytics-heading"><div><p class="eyebrow">Аналитика · Бизнес</p><h1>Выручка</h1><p class="muted">${analyticsPeriodLabel(summary.period)} · ${escapeHtml(summary.period.timezone)}</p></div><button class="button secondary compact" id="analytics-refresh" type="button">Обновить</button></header>
    <nav class="filter-bar analytics-presets" aria-label="Период аналитики">${presetButtons}<button class="filter-chip ${filters.period === "custom" ? "active" : ""}" id="analytics-custom-toggle" type="button">Произвольный</button></nav>
    <form class="analytics-custom ${filters.period === "custom" ? "" : "hidden"}" id="analytics-custom-form"><label>С<input name="start_date" type="date" value="${filters.startDate || summary.period.start_date}" required></label><label>По<input name="end_date" type="date" value="${filters.endDate || summary.period.end_date}" required></label><button class="button" type="submit">Показать</button></form>
    <section class="analytics-kpis analytics-kpis-business" aria-label="Основные показатели">
      <article class="analytics-kpi"><span>Общая выручка</span><strong>${formatMoney(summary.business_revenue)}</strong></article>
      <article class="analytics-kpi"><span>Продажи</span><strong>${formatMoney(summary.sales_revenue)}</strong></article>
      <article class="analytics-kpi"><span>Аренда</span><strong>${formatMoney(summary.rental_revenue)}</strong></article>
      <article class="analytics-kpi"><span>Количество продаж</span><strong>${summary.sale_count}</strong></article>
      <article class="analytics-kpi"><span>Количество аренд</span><strong>${summary.rental_count}</strong></article>
      <article class="analytics-kpi"><span>Продано единиц</span><strong>${summary.items_sold}</strong></article>
    </section>
    <section class="analytics-section analytics-sources"><div class="analytics-section-head"><div><p class="eyebrow">Источники бизнеса</p><h2>Выручка</h2></div></div><div class="analytics-table-wrap"><table class="analytics-table"><thead><tr><th>Источник</th><th>Выручка</th><th>Операций</th></tr></thead><tbody><tr><td><strong>Всего</strong></td><td><strong>${formatMoney(summary.business_revenue)}</strong></td><td>—</td></tr><tr><td>Продажи</td><td>${formatMoney(summary.sales_revenue)}</td><td>${summary.sale_count}</td></tr><tr><td>Аренда</td><td>${formatMoney(summary.rental_revenue)}</td><td>${summary.rental_count}</td></tr></tbody></table></div></section>
    <section class="analytics-section analytics-sources"><div class="analytics-section-head"><div><p class="eyebrow">Состав продаж</p><h2>Продажи по источнику</h2></div><strong class="free-sale-share">Доля свободной продажи по выручке: ${formatPercent(summary.free_sale_revenue_share)}</strong></div><div class="analytics-table-wrap"><table class="analytics-table"><thead><tr><th>Источник</th><th>Выручка</th><th>Единиц</th></tr></thead><tbody><tr><td><strong>Всего продаж</strong></td><td><strong>${formatMoney(summary.sales_revenue)}</strong></td><td><strong>${summary.items_sold}</strong></td></tr><tr><td>Каталог</td><td>${formatMoney(summary.catalog_revenue)}</td><td>${summary.catalog_units}</td></tr><tr><td>Свободная продажа</td><td>${formatMoney(summary.free_sale_revenue)}</td><td>${summary.free_sale_units}</td></tr></tbody></table></div><p class="muted small source-share-note">Доля свободной продажи по единицам: ${formatPercent(summary.free_sale_units_share)}</p></section>
    <section class="analytics-section analytics-rental-summary"><div class="analytics-section-head"><div><p class="eyebrow">Аренда</p><h2>Завершённые аренды</h2><p class="muted small">Признание в дату закрытия заказа; залог и компенсация повреждений не входят.</p></div></div><div class="payment-breakdown analytics-rental-metrics"><article class="card"><span>Выручка аренды</span><strong>${formatMoney(summary.rental_revenue)}</strong></article><article class="card"><span>Количество аренд</span><strong>${summary.rental_count}</strong></article><article class="card"><span>Средняя аренда</span><strong>${formatMoney(summary.average_rental_amount)}</strong></article><article class="card"><span>Из них просрочка</span><strong>${formatMoney(summary.overdue_revenue)}</strong></article></div></section>
    ${summary.sale_count === 0 && summary.rental_count === 0 ? '<section class="empty analytics-empty"><strong>В выбранном периоде операций нет</strong><p>Все показатели равны нулю. Выберите другой период или обновите данные после завершения операции.</p></section>' : ""}
    <section class="analytics-section"><div class="analytics-section-head"><div><p class="eyebrow">Динамика</p><h2>Выручка по дням</h2><p class="muted small">Общая выручка равна сумме продаж и завершённых аренд.</p></div></div><div class="analytics-trend-grid"><div><h3>Всего</h3>${renderAnalyticsBars(report.timeline, "total_revenue", (value) => formatMoney(value), "Нет выручки за выбранный период")}</div><div><h3>Продажи</h3>${renderAnalyticsBars(report.timeline, "sales_revenue", (value) => formatMoney(value), "Нет продаж за выбранный период")}</div><div><h3>Аренда</h3>${renderAnalyticsBars(report.timeline, "rental_revenue", (value) => formatMoney(value), "Нет завершённых аренд за выбранный период")}</div></div></section>
    <section class="analytics-section"><div class="analytics-section-head"><div><p class="eyebrow">Лидеры</p><h2>Товары</h2></div><div class="analytics-ranking" aria-label="Сортировка лидеров"><button class="filter-chip ${filters.ranking === "units" ? "active" : ""}" data-analytics-ranking="units" type="button">По штукам</button><button class="filter-chip ${filters.ranking === "revenue" ? "active" : ""}" data-analytics-ranking="revenue" type="button">По выручке</button></div></div><div class="analytics-table-wrap"><table class="analytics-table"><thead><tr><th>Товар</th><th>Шт.</th><th>Выручка</th></tr></thead><tbody>${productRows}</tbody></table></div></section>
    <section class="analytics-section"><div class="analytics-section-head"><div><h2>Варианты</h2><p class="muted small">Цвет, размер и тип сохраняют отдельную идентичность</p></div></div><div class="analytics-table-wrap"><table class="analytics-table"><thead><tr><th>Product · Variant / SKU</th><th>Шт.</th><th>Выручка</th></tr></thead><tbody>${variantRows}</tbody></table></div></section>
    <section class="analytics-section"><div class="analytics-section-head"><div><h2>Топ свободной продажи</h2><p class="muted small">Группировка только по регистру и пробелам; с Catalog по названию не объединяется</p></div></div><div class="analytics-table-wrap"><table class="analytics-table"><thead><tr><th>Наименование</th><th>Продано</th><th>Выручка</th></tr></thead><tbody>${freeSaleRows}</tbody></table></div></section>
    <section class="analytics-section"><div class="analytics-section-head"><div><h2>Топ аренды</h2><p class="muted small">Физические RentalAsset; названия и суммы берутся из исторических снимков завершённых заказов.</p></div></div><div class="analytics-table-wrap"><table class="analytics-table"><thead><tr><th>RentalAsset</th><th>Аренд</th><th>Выручка</th></tr></thead><tbody>${rentalRows}</tbody></table></div></section>
    <section class="analytics-section"><div class="analytics-section-head"><div><p class="eyebrow">Продажи · дни недели</p><h2>Средняя и общая эффективность</h2><p class="muted small">Этот блок остаётся Sales-only; среднее включает дни без продаж.</p></div></div><div class="analytics-table-wrap"><table class="analytics-table weekday-table"><thead><tr><th>День</th><th>Выручка продаж</th><th>Продажи</th></tr></thead><tbody>${weekdayRows}</tbody></table></div></section>
    ${payments}
    <section class="analytics-section"><div class="analytics-section-head"><div><p class="eyebrow">Точные данные</p><h2>По дням</h2></div></div><div class="analytics-table-wrap"><table class="analytics-table daily-table"><thead><tr><th>Дата</th><th>Всего</th><th>Продажи</th><th>Аренда</th><th>Кол-во продаж</th><th>Кол-во аренд</th><th>Продано единиц</th></tr></thead><tbody>${dailyRows}</tbody></table></div></section>
    <aside class="analytics-limitations"><strong>Границы первой версии</strong>${report.limitations.map((item) => `<p>${escapeHtml(item)}</p>`).join("")}</aside>
  </div>`;
  bindTopbar();
  document.querySelector("#analytics-refresh")?.addEventListener("click", () => openSalesAnalytics(filters));
  document.querySelectorAll("[data-analytics-period]").forEach((button) => button.addEventListener("click", () => openSalesAnalytics({ period: button.dataset.analyticsPeriod, ranking: filters.ranking })));
  document.querySelectorAll("[data-analytics-ranking]").forEach((button) => button.addEventListener("click", () => openSalesAnalytics({ ...filters, ranking: button.dataset.analyticsRanking })));
  document.querySelector("#analytics-custom-toggle")?.addEventListener("click", () => document.querySelector("#analytics-custom-form")?.classList.toggle("hidden"));
  document.querySelector("#analytics-custom-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    void openSalesAnalytics({ period: "custom", startDate: data.get("start_date"), endDate: data.get("end_date"), ranking: filters.ranking });
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
  const persistentCheckoutHealth = document.querySelector("#checkout-health");
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
    <section id="checkout-health" class="checkout-health checkout-health-busy" aria-live="polite">
      <span class="checkout-health-icon" aria-hidden="true">…</span>
      <div class="checkout-health-copy"><strong>Проверяем состояние</strong><small>Получаем данные CORE и провайдеров…</small></div>
      <span class="checkout-health-context"></span>
      <button class="button ghost compact checkout-health-details" type="button">Подробнее</button>
    </section>
    <div class="sale-heading">
      <div><p class="eyebrow">Продажа</p><h1>${sale ? `Продажа #${sale.sale_number}` : "Продажи"}</h1></div>
      <div class="sale-heading-actions"><button class="button secondary" id="checkout-operations" type="button">Чеки и операции</button><button class="button secondary" id="new-sale" type="button">＋ Новая продажа</button></div>
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
  if (persistentCheckoutHealth) {
    document.querySelector("#checkout-health")?.replaceWith(persistentCheckoutHealth);
  }
  bindTopbar();
  const checkoutHealthDetails = document.querySelector("#checkout-health .checkout-health-details");
  if (checkoutHealthDetails) checkoutHealthDetails.onclick = () => openCheckoutOperations(
    checkoutHealthView.state === "ATTENTION" ? "attention" : "all",
  );
  document.querySelector("#new-sale").addEventListener("click", createAndOpenSale);
  document.querySelector("#checkout-operations").addEventListener("click", () => openCheckoutOperations());
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
  document.querySelector("#emergency-pos-payment")?.addEventListener("click", () => openCheckoutOperationDetail(sale.id));
  document.querySelector("#defer-sale")?.addEventListener("click", deferActiveSale);
  document.querySelector("#cancel-sale")?.addEventListener("click", () => cancelSale(sale.id));
  if (editable && sale.items.length) void hydrateCheckoutButton(sale.id);
  scheduleCheckoutProgress(sale);
  ensureCheckoutHealthPolling();
}

async function hydrateCheckoutHealth() {
  const target = document.querySelector("#checkout-health");
  if (!target) return;
  if (checkoutHealthRequestInFlight) return checkoutHealthRequestInFlight;
  const requestSequence = ++checkoutHealthRequestSequence;
  checkoutHealthRequestInFlight = (async () => {
    try {
      const health = await api("/api/sales/operations/health");
      if (requestSequence !== checkoutHealthRequestSequence) return;
      checkoutHealthCache = health;
      checkoutHealthFetchedAt = Date.now();
      if (!document.querySelector("#checkout-health")) return;
      renderCheckoutHealth(health);
    } catch (error) {
      if (requestSequence !== checkoutHealthRequestSequence) return;
      scheduleCheckoutHealthView({ state: "OFFLINE", detail: "Не удалось получить состояние CORE и AQSI.", context: "" });
    } finally {
      if (requestSequence === checkoutHealthRequestSequence) checkoutHealthRequestInFlight = null;
    }
  })();
  return checkoutHealthRequestInFlight;
}

function ensureCheckoutHealthPolling() {
  if (checkoutHealthCache && Date.now() - checkoutHealthFetchedAt < 5000) {
    renderCheckoutHealth(checkoutHealthCache);
  } else {
    void hydrateCheckoutHealth();
  }
  if (checkoutHealthPollTimer) return;
  checkoutHealthPollTimer = setInterval(() => {
    if (document.querySelector("#checkout-health")) void hydrateCheckoutHealth();
  }, 15000);
}

function renderCheckoutHealth(health) {
  const sale = state.sales.active;
  const payment = sale?.payments?.[sale.payments.length - 1];
  const paymentConfigured = health.payment_provider_configured ?? health.configured;
  const fiscalConfigured = health.fiscal_provider_configured ?? health.configured;
  const paymentReachable = health.payment_provider_reachable ?? health.api_reachable;
  const fiscalReachable = health.fiscal_provider_reachable ?? health.api_reachable;
  const needsAttention = payment?.status === "unknown"
    || sale?.fiscalization?.status === "unknown"
    || (sale?.status === "fiscalization_failed" && !sale?.fiscalization?.operator_dismissed);
  let view;
  if (!paymentConfigured) {
    view = { state: "OFFLINE", detail: "Провайдер оплаты не настроен.", context: "" };
  } else if (!fiscalConfigured) {
    view = { state: "OFFLINE", detail: "Провайдер фискализации не настроен.", context: "" };
  } else if (paymentReachable === false) {
    view = { state: "OFFLINE", detail: "Нет связи с провайдером оплаты. CORE повторит проверку автоматически.", context: "" };
  } else if (fiscalReachable === false) {
    view = { state: "OFFLINE", detail: "Нет связи с провайдером фискализации. CORE повторит проверку автоматически.", context: "" };
  } else if (health.physical_device_ready === false) {
    view = { state: "OFFLINE", detail: "Физическая касса сообщает о недоступности.", context: "" };
  } else if (!health.worker_running) {
    view = { state: "ATTENTION", detail: "Фоновая обработка не отвечает. Откройте подробности.", context: "" };
  } else if (health.stalled_job_count > 0) {
    view = { state: "ATTENTION", detail: "Есть зависшая фоновая операция.", context: "" };
  } else if (health.manual_review_required) {
    view = { state: "ATTENTION", detail: "Операция требует ручной проверки.", context: "" };
  } else if (health.failed_fiscalization_count > 0) {
    view = { state: "ATTENTION", detail: "Оплата подтверждена, но есть несформированный чек.", context: "" };
  } else if (needsAttention) {
    view = { state: "ATTENTION", detail: "Последняя операция требует проверки.", context: checkoutHealthSaleContext(sale) };
  } else if (health.local_active_operation) {
    view = {
      state: "BUSY",
      detail: sale?.status === "fiscalization_pending" ? "Фискализируем чек…" : "Проверяем состояние оплаты…",
      context: checkoutHealthSaleContext(sale),
    };
  } else {
    view = {
      state: "READY",
      detail: health.physical_device_ready === true
        ? "Провайдеры и физическая касса доступны."
        : "Провайдеры доступны. Готовность устройства проверяется при операции.",
      context: "",
    };
  }
  scheduleCheckoutHealthView(view);
}

function checkoutHealthSaleContext(sale) {
  if (!sale || !["payment_pending", "fiscalization_pending", "fiscalization_failed"].includes(sale.status)) return "";
  return `Продажа #${sale.sale_number} · ${formatMoney(sale.total_amount)}`;
}

function scheduleCheckoutHealthView(view) {
  clearTimeout(checkoutHealthTimer);
  const target = document.querySelector("#checkout-health");
  const sameView = view.state === checkoutHealthView.state
    && view.detail === checkoutHealthView.detail
    && view.context === checkoutHealthView.context;
  if (sameView && target?.classList.contains(`checkout-health-${view.state.toLowerCase()}`)) return;
  checkoutHealthTimer = setTimeout(() => applyCheckoutHealthView(view), 650);
}

function applyCheckoutHealthView(view) {
  const target = document.querySelector("#checkout-health");
  if (!target) return;
  const labels = {
    READY: ["✓", "CORE готов к работе"],
    BUSY: ["…", "Операция выполняется"],
    ATTENTION: ["!", "Требует внимания"],
    OFFLINE: ["×", "Провайдер недоступен"],
  };
  const [icon, title] = labels[view.state];
  target.className = `checkout-health checkout-health-${view.state.toLowerCase()}`;
  target.querySelector(".checkout-health-icon").textContent = icon;
  target.querySelector(".checkout-health-copy strong").textContent = title;
  target.querySelector(".checkout-health-copy small").textContent = view.detail;
  target.querySelector(".checkout-health-context").textContent = view.context;
  target.querySelector(".checkout-health-details").hidden = view.state === "READY";
  checkoutHealthView = view;
}

function incidentResolution(sale, kind) {
  return (sale.incident_resolutions || []).find((item) => item.incident_kind === kind);
}

function paymentIncidentResolved(sale) {
  return incidentResolution(sale, "payment_duplicate_risk")?.resolution_status === "resolved";
}

function fiscalVerificationState(sale) {
  if (sale.fiscalization?.status === "succeeded") return "verified";
  const resolution = incidentResolution(sale, "fiscal_receipt_verification");
  if (resolution?.resolution_type === "fiscal_receipt_verified") return "verified";
  if (resolution?.resolution_type === "fiscal_receipt_missing") return "missing";
  if (resolution?.resolution_type === "fiscal_verification_unknown") return "unknown";
  if (sale.fiscalization?.external_receipt_confirmed && sale.payments?.some((item) => item.emergency_fallback)) return "operator_reported";
  return sale.fiscalization?.status === "unknown" ? "unknown" : "not_required";
}

function manualReviewPayment(sale) {
  return (sale.payments || []).find((payment) => payment.duplicate_payment_risk && !paymentIncidentResolved(sale))
    || (sale.payments || []).find((payment) => payment.accounting_superseded
      && ["pending", "unknown"].includes(payment.status));
}

async function openCheckoutOperations(filterState = "all") {
  try {
    const [operations, health] = await Promise.all([
      api(`/api/sales/operations/history?state=${encodeURIComponent(filterState)}`),
      api("/api/sales/operations/health"),
    ]);
    recordRoute("operations", { state: filterState });
    logicalParent = () => openSalesWorkspace();
    const paymentLabel = (value) => ({ pending: "Выполняется", unknown: "Требует сверки", succeeded: "Завершена", failed: "Ошибка", canceled: "Отменена" }[value] || "—");
    const fiscalLabel = (sale) => {
      const verification = fiscalVerificationState(sale);
      if (sale.fiscalization?.operator_dismissed) return "Неактуальна · чек не сформирован";
      if (verification === "verified") return "Проверен";
      if (verification === "operator_reported") return "Сообщён оператором · не проверен";
      if (verification === "missing") return "Отсутствует · требуется действие";
      if (verification === "unknown") return "Результат проверки неизвестен";
      if (sale.fiscalization?.status === "failed") return "Ошибка";
      return sale.fiscalization?.status === "skipped" ? "Без чека" : sale.fiscalization ? "Выполняется" : "Не начата";
    };
    const providerHealthLabel = (configured, reachable) => !configured
      ? "⚠ Не настроен"
      : reachable === false ? "⚠ Недоступен" : reachable === true ? "● Доступен" : "— Не проверен";
    const deviceHealthLabel = health.physical_device_ready === true
      ? "● Доступна"
      : health.physical_device_ready === false ? "⚠ Недоступна" : "— Готовность неизвестна";
    const rows = operations.length ? operations.map((sale) => {
      const reviewPayment = manualReviewPayment(sale);
      const fallback = sale.payments?.find((item) => item.emergency_fallback);
      const payment = reviewPayment || sale.payments?.[sale.payments.length - 1];
      const duplicateRisk = Boolean(reviewPayment?.duplicate_payment_risk);
      const paymentResolved = paymentIncidentResolved(sale);
      const fiscalState = fiscalVerificationState(sale);
      const fiscalAttention = ["operator_reported", "missing", "unknown"].includes(fiscalState);
      const unknown = payment?.status === "unknown" || duplicateRisk || fiscalAttention;
      const providerId = payment?.external_id || sale.fiscalization?.external_id;
      const needsReconcile = Boolean(providerId) && ((reviewPayment && ["pending", "unknown"].includes(reviewPayment.status)) || ["payment_pending", "fiscalization_pending"].includes(sale.status));
      const paymentStatus = paymentResolved ? "Оплата сверена" : duplicateRisk ? "Возможное двойное списание" : fallback ? "Оплачено на кассе вручную" : reviewPayment ? "Исходная операция: требует проверки" : paymentLabel(payment?.status);
      const reviewNote = paymentResolved ? "<small>Одно списание · проверено оператором.</small>" : fallback ? `<small>Исходная операция провайдера: ${duplicateRisk ? "позднее подтверждена · требуется проверка" : "результат не завершён"}.</small>` : reviewPayment ? "<small>Продажа сверена, автоматическая обработка этой операции отключена.</small>" : "";
      const canFallback = !fallback && !sale.fiscalization && sale.status === "payment_pending" && payment?.payment_method === "card" && ["pending", "unknown"].includes(payment?.status) && (state.user?.is_admin || state.user?.is_superuser);
      return `<article class="operation-card${reviewPayment || unknown ? " operation-card-attention" : ""}"><div><small>${new Date(sale.created_at).toLocaleString("ru-RU")}</small><h3>Продажа #${sale.sale_number}</h3><strong>${formatMoney(sale.total_amount)}</strong></div><div><span>Оплата: <strong>${paymentStatus}</strong></span><span>Чек: <strong>${fiscalLabel(sale)}</strong></span>${unknown && !providerId ? "<small>Идентификатор провайдера не получен · нужна ручная проверка</small>" : ""}${reviewNote}</div><div class="operation-actions"><button class="button secondary compact" data-open-operation="${sale.id}" type="button">Подробнее</button>${needsReconcile ? `<button class="button compact" data-reconcile-operation="${sale.id}" type="button">Проверить</button>` : ""}${canFallback ? `<button class="button warning compact" data-emergency-operation="${sale.id}" type="button">Решить инцидент</button>` : ""}</div></article>`;
    }).join("") : '<section class="empty"><strong>Операций нет</strong><p>Для выбранного фильтра история пуста.</p></section>';
    root.innerHTML = `<div class="shell operations-shell">${topbar(true)}<div class="page-title"><div><p class="eyebrow">Касса</p><h1>Чеки и операции</h1></div><button class="button secondary" id="operations-back" type="button">К продажам</button></div><section class="operation-health-grid"><article><span>Провайдер оплаты</span><strong>${providerHealthLabel(health.payment_provider_configured ?? health.configured, health.payment_provider_reachable ?? health.api_reachable)}</strong><small>${escapeHtml(health.payment_integration_name || "Не назначен")}</small></article><article><span>Провайдер чека</span><strong>${providerHealthLabel(health.fiscal_provider_configured ?? health.configured, health.fiscal_provider_reachable ?? health.api_reachable)}</strong><small>${escapeHtml(health.fiscal_integration_name || "Не назначен")}</small></article><article><span>Физическая касса</span><strong>${deviceHealthLabel}</strong><small>${health.physical_device_ready == null ? "Cloud API не подтверждает готовность устройства" : "Состояние получено от провайдера"}</small></article><article><span>Фоновая обработка</span><strong>${health.worker_running ? "● Работает" : "⚠ Не отвечает"}</strong><small>${health.queued_job_count} ожидают · ${health.processing_job_count} выполняются${health.stalled_job_count ? ` · зависших ${health.stalled_job_count}` : ""}</small></article></section>${health.message ? `<p class="operation-health-message">${escapeHtml(health.message)}</p>` : ""}<nav class="operation-filters">${[["all", "Все"], ["attention", "Требуют внимания"], ["pending", "В процессе"], ["failed", "Ошибки"], ["completed", "Завершённые"]].map(([value, label]) => `<button class="button ${filterState === value ? "" : "secondary"} compact" data-operation-filter="${value}" type="button">${label}</button>`).join("")}</nav><section class="operation-list">${rows}</section></div>`;
    bindTopbar();
    document.querySelector("#operations-back")?.addEventListener("click", () => openSalesWorkspace());
    document.querySelectorAll("[data-operation-filter]").forEach((button) => button.addEventListener("click", () => openCheckoutOperations(button.dataset.operationFilter)));
    document.querySelectorAll("[data-open-operation]").forEach((button) => button.addEventListener("click", () => openCheckoutOperationDetail(button.dataset.openOperation)));
    document.querySelectorAll("[data-emergency-operation]").forEach((button) => button.addEventListener("click", () => openCheckoutOperationDetail(button.dataset.emergencyOperation)));
    document.querySelectorAll("[data-reconcile-operation]").forEach((button) => button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await api(`/api/sales/operations/${button.dataset.reconcileOperation}/reconcile`, { method: "POST" });
        await openCheckoutOperations(filterState);
      } catch (error) { showToast(error.message, true); button.disabled = false; }
    }));
  } catch (error) { showToast(error.message, true); }
}

const financialEventLabels = {
  payment_created: "Создана попытка оплаты",
  payment_command_prepared: "Команда оплаты подготовлена",
  payment_dispatch_started: "Отправка команды оплаты начата",
  payment_operation_id_received: "Получен ID операции оплаты",
  payment_approved: "Оплата подтверждена",
  payment_declined: "Оплата отклонена",
  payment_canceled: "Оплата отменена",
  payment_outcome_unknown: "Результат оплаты неизвестен",
  fiscalization_created: "Создано обязательство сформировать чек",
  fiscal_command_prepared: "Команда чека подготовлена",
  fiscal_dispatch_started: "Отправка команды чека начата",
  fiscal_operation_id_received: "Получен ID операции чека",
  fiscalization_succeeded: "Чек сформирован",
  fiscalization_failed: "Чек не сформирован",
  fiscal_outcome_unknown: "Результат формирования чека неизвестен",
  reconciliation_requested: "Запрошена сверка",
  reconciliation_succeeded: "Сверка завершена",
  reconciliation_inconclusive: "Сверка не дала окончательного результата",
  manual_review_required: "Требуется ручная проверка",
  emergency_fallback_requested: "Запрошено аварийное завершение",
  emergency_fallback_confirmed: "Аварийное завершение подтверждено",
  external_payment_recorded: "Оплата на кассе записана",
  late_provider_approval_detected: "Обнаружено возможное двойное списание",
  payment_incident_reviewed: "Инцидент оплаты проверен",
  single_charge_operator_confirmed: "Подтверждено одно списание",
  payment_incident_resolved: "Инцидент оплаты разрешён",
  fiscal_receipt_missing_reported: "Отсутствие фискального чека подтверждено",
  fiscal_receipt_verified: "Фискальный чек проверен",
  fiscal_verification_unknown_recorded: "Результат проверки чека неизвестен",
};

async function openCheckoutOperationDetail(saleId) {
  try {
    const detail = await api(`/api/sales/operations/${saleId}/detail`);
    const sale = detail.sale;
    recordRoute("operation-detail", { saleId });
    logicalParent = () => openCheckoutOperations();
    const payment = sale.payments?.find((item) => item.id === detail.manual_review_payment_id)
      || sale.payments?.[sale.payments.length - 1];
    const fiscal = sale.fiscalization;
    const identifiers = [
      ...detail.correlation_ids.map((value) => ["Correlation ID", value]),
      ...detail.provider_operation_ids.map((value) => ["Provider operation ID", value]),
    ];
    const identifierRows = identifiers.length
      ? identifiers.map(([label, value]) => `<div class="operation-identifier"><span>${label}</span><code>${escapeHtml(value)}</code><button class="button ghost compact" data-copy-operation-id="${escapeHtml(value)}" type="button">Копировать</button></div>`).join("")
      : "<p class=\"muted\">Provider ID не получен.</p>";
    const timeline = detail.events.length
      ? detail.events.map((event) => `<li class="financial-event"><time>${formatDate(event.occurred_at)}</time><div><strong>${escapeHtml(financialEventLabels[event.event_type] || event.event_type)}</strong>${event.previous_status || event.new_status ? `<small>${escapeHtml(event.previous_status || "—")} → ${escapeHtml(event.new_status || "—")}</small>` : ""}${event.safe_message ? `<p>${escapeHtml(event.safe_message)}</p>` : ""}${event.provider_operation_id ? `<code>${escapeHtml(event.provider_operation_id)}</code>` : ""}</div></li>`).join("")
      : '<li class="empty">События появятся после следующего значимого перехода.</li>';
    const canReconcile = detail.provider_operation_ids.length > 0 && (["payment_pending", "fiscalization_pending"].includes(sale.status) || (detail.emergency_fallback_recorded && payment && ["pending", "unknown"].includes(payment.status)));
    const paymentHeading = detail.payment_incident_unresolved ? "Требует проверки" : detail.emergency_fallback_recorded ? "Оплачено на кассе" : ({ succeeded: "Оплачено", canceled: "Отменено", failed: "Не выполнено", pending: "Выполняется", unknown: "Результат неизвестен" })[payment?.status] || "Не начата";
    const fiscalHeading = ({ verified: "Проверен", operator_reported: "Нужно проверить", missing: "Чек отсутствует", unknown: "Результат неизвестен", not_required: fiscal?.status === "skipped" ? "Не сформирован в CORE" : "Не начат" })[detail.fiscal_verification_state] || "Не начат";
    const summaryTitle = detail.payment_incident_unresolved ? "Нужно проверить исходную оплату" : detail.fiscal_action_required ? "Нужно проверить фискальный чек" : detail.manual_review_required ? "Нужна ручная проверка" : "Операция завершена";
    const summaryText = detail.operator_instruction || (detail.manual_review_required ? "Сохранённые финансовые факты не изменены." : "Дополнительных действий не требуется.");
    const canResolve = detail.manual_review_required || detail.emergency_fallback_eligible || detail.emergency_fallback_recorded;
    const itemRows = sale.items.map((item) => `<li><div><strong>${escapeHtml(item.display_label_snapshot)}</strong><small>${escapeHtml(String(item.quantity))} × ${formatMoney(item.unit_price)}</small></div><b>${formatMoney(item.line_total)}</b></li>`).join("");
    const detailActions = canReconcile ? `<button class="button secondary compact" id="detail-reconcile" type="button">Проверить статус</button>` : "";
    root.innerHTML = `<div class="shell operations-shell">${topbar(true)}<div class="page-title"><div><p class="eyebrow">Операция</p><h1>Продажа #${sale.sale_number}</h1><p>${formatMoney(sale.total_amount)} · ${formatDate(sale.created_at)}</p></div><button class="button secondary" id="operation-detail-back" type="button">К операциям</button></div><section class="operation-incident-summary ${detail.manual_review_required ? "attention" : "resolved"}"><div><strong>${escapeHtml(summaryTitle)}</strong><p>${escapeHtml(summaryText)}</p><small>Оплата: ${escapeHtml(paymentHeading)} · Фискальный чек: ${escapeHtml(fiscalHeading)}</small></div>${canResolve ? '<button class="button compact" id="resolve-incident" type="button">Решить инцидент</button>' : ""}</section><section class="operation-sale-items"><h2>Состав продажи</h2><ol>${itemRows}</ol><footer><span>Итого</span><strong>${formatMoney(sale.total_amount)}</strong></footer></section><details class="operation-technical"><summary>Технические подробности</summary><div class="operation-technical-body"><p><strong>Состояние восстановления:</strong> ${escapeHtml(detail.recovery_state)}</p><p><strong>Провайдер:</strong> ${escapeHtml(detail.provider_identity || "Не назначен")}</p><section class="operation-identifiers"><h2>Идентификаторы</h2>${identifierRows}</section>${payment?.error_message || fiscal?.error_message ? `<section class="operation-error"><h2>Последняя безопасная ошибка</h2><p>${escapeHtml(payment?.error_message || fiscal?.error_message)}</p></section>` : ""}<section class="financial-timeline"><div class="section-heading"><h2>История событий</h2><div class="operation-actions">${detailActions}</div></div><ol>${timeline}</ol></section></div></details></div>`;
    bindTopbar();
    document.querySelector("#operation-detail-back")?.addEventListener("click", () => openCheckoutOperations());
    document.querySelectorAll("[data-copy-operation-id]").forEach((button) => button.addEventListener("click", async () => {
      await navigator.clipboard.writeText(button.dataset.copyOperationId);
      showToast("Идентификатор скопирован");
    }));
    document.querySelector("#detail-reconcile")?.addEventListener("click", async (event) => {
      event.currentTarget.disabled = true;
      try {
        await api(`/api/sales/operations/${saleId}/reconcile`, { method: "POST" });
        await openCheckoutOperationDetail(saleId);
      } catch (error) { showToast(error.message, true); event.currentTarget.disabled = false; }
    });
    document.querySelector("#resolve-incident")?.addEventListener("click", () => openIncidentResolutionDialog(detail));
  } catch (error) { showToast(error.message, true); }
}

function openIncidentResolutionDialog(detail) {
  const sale = detail.sale;
  const original = sale.payments?.find((item) => item.accounting_superseded && !item.emergency_fallback);
  const dialog = document.createElement("dialog");
  dialog.className = "label-dialog checkout-dialog";
  const option = (value, label, hint, enabled = true) => `<label class="incident-choice ${enabled ? "" : "disabled"}"><input type="radio" name="incident-choice" value="${value}" ${enabled ? "" : "disabled"}><span><strong>${label}</strong><small>${hint}</small></span></label>`;
  dialog.innerHTML = `<form method="dialog"><p class="eyebrow">Решение инцидента</p><h2>Продажа #${escapeHtml(String(sale.sale_number))}</h2><section class="incident-choices">${option("correction_receipt_formed", "Чек коррекции сформирован", "Запишем подтверждение оператора, не создавая новый чек.", detail.fiscal_action_required)}${option("external_pos_payment_confirmed", "Оплата прошла на кассе", "Учтём оплату на физической кассе и продолжим проверку исходной операции.", detail.emergency_fallback_eligible || detail.emergency_fallback_recorded)}${option("original_operation_canceled", "Исходная операция отменена", "Доступно только после финального статуса отмены от провайдера.", original?.status === "canceled")}${option("manual_reviewed", "Другое — проверено вручную", "Сохраним проверку, не меняя неизвестные финансовые факты.")}</section><label>Комментарий оператора <span class="muted">(необязательно)</span><textarea id="incident-resolution-note" maxlength="1000" placeholder="Коротко опишите, что проверили"></textarea></label><label class="confirmation-check"><input id="incident-resolution-confirmation" type="checkbox"> <span>Я проверил операцию и подтверждаю выбранный результат.</span></label><p class="muted">Действие не выполняет возврат, повторную оплату или повторную фискализацию.</p><div class="sale-actions"><button class="button secondary" value="cancel">Отмена</button><button class="button" id="submit-incident-resolution" value="default">Сохранить</button></div></form>`;
  document.body.append(dialog);
  dialog.addEventListener("close", () => dialog.remove());
  dialog.querySelector("#submit-incident-resolution").addEventListener("click", async (event) => {
    event.preventDefault();
    const choice = dialog.querySelector('input[name="incident-choice"]:checked')?.value;
    const confirmation = dialog.querySelector("#incident-resolution-confirmation").checked;
    if (!choice || !confirmation) {
      showToast("Выберите результат и подтвердите проверку.", true);
      return;
    }
    event.currentTarget.disabled = true;
    try {
      const result = await api(`/api/sales/operations/${sale.id}/resolve-incident`, {
        method: "POST",
        body: JSON.stringify({ choice, confirmation, operator_note: dialog.querySelector("#incident-resolution-note").value.trim() || null }),
      });
      showToast(result.message);
      dialog.close();
      await openCheckoutOperationDetail(sale.id);
    } catch (error) {
      showToast(error.message, true);
      event.currentTarget.disabled = false;
    }
  });
  dialog.showModal();
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
  const fallback = sale.payments?.find((item) => item.emergency_fallback);
  const fiscalVerification = fiscalVerificationState(sale);
  if (fiscal?.operator_dismissed) {
    return `<section class="checkout-state"><strong>Операция неактуальна</strong><p>Отмечена по решению оператора. Оплата ${formatMoney(sale.total_amount)} подтверждена, чек не сформирован.</p><small>История сохранена. Повторная обработка отключена.</small></section>`;
  }
  if (sale.status === "cancelled") return "";
  if (sale.status === "draft" && !["canceled", "failed"].includes(payment?.status)) return "";
  const messages = {
    payment_pending: payment?.status === "unknown"
      ? ["⚠ Результат оплаты пока неизвестен", "Не запускайте новую оплату. CORE продолжает безопасную проверку статуса."]
      : ["Ожидаем оплату на кассе…", "Следуйте подсказкам на платёжном устройстве."],
    paid: ["✓ Оплата прошла", "Фиксируем продажу в учёте…"],
    fiscalization_pending: fiscal?.status === "unknown"
      ? ["⚠ Оплата прошла, статус чека пока неизвестен", "CORE проверяет чек. Не повторяйте оплату или формирование чека."]
      : [
          "✓ Оплата прошла",
          salesFiscalPendingObservedAt && performance.now() - salesFiscalPendingObservedAt >= 5000
            ? `Провайдер обрабатывает чек ${Math.round((performance.now() - salesFiscalPendingObservedAt) / 1000)} сек. Новая оплата заблокирована.`
            : "Формируем фискальный чек…",
        ],
    completed: fiscal?.external_receipt_confirmed && fiscalVerification === "verified"
      ? ["✓ Оплачено на кассе вручную", "Продажа учтена в CORE. Фискальный чек проверен оператором по внешним реквизитам."]
      : fiscal?.external_receipt_confirmed && fiscalVerification === "missing"
      ? ["⚠ Оплата учтена, фискальный чек отсутствует", "Эквайринговый приход не заменяет фискальный чек. Откройте «Чеки и операции»."]
      : fiscal?.external_receipt_confirmed
      ? ["⚠ Оплата учтена, чек не проверен", "Утверждение о внешнем чеке требует отдельной проверки в «Чеки и операции»."]
      : fiscal?.status === "skipped"
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
  const paymentMethodLabel = payment?.emergency_fallback ? "Оплачено на кассе вручную" : payment?.payment_method === "cash" ? "Наличными" : "Карта / QR";
  const paidLabel = payment?.status === "succeeded" ? `✓ ${paymentMethodLabel}` : paymentMethodLabel;
  const effectivePayments = (sale.payments || []).filter((item) => item.status === "succeeded" && !item.accounting_superseded);
  const paymentSummary = effectivePayments.length > 1
    ? `<div><span>Оплата</span>${effectivePayments.map((item) => `<strong>✓ ${item.payment_method === "cash" ? "Наличными" : "Карта / QR"} · ${formatMoney(item.requested_amount)}</strong>`).join("")}</div>`
    : payment ? `<div><span>Оплата</span><strong>${paidLabel} · ${formatMoney(payment.requested_amount)}</strong><small>${escapeHtml(paymentLabels[payment.status] || payment.status)}</small></div>` : "";
  const fiscalStatusLabel = fiscal?.status === "succeeded"
    ? "✓ Фискализирован"
    : fiscal?.external_receipt_confirmed && fiscalVerification === "verified"
      ? "✓ Проверен оператором"
    : fiscal?.external_receipt_confirmed && fiscalVerification === "missing"
      ? "⚠ Отсутствует"
    : fiscal?.external_receipt_confirmed
      ? "⚠ Сообщён оператором · не проверен"
    : fiscal?.status === "skipped"
      ? "— Не формировался"
      : fiscal?.status;
  const fiscalSummary = fiscal ? `<div><span>Фискальный чек</span><strong>${escapeHtml(fiscalStatusLabel)}</strong>${fiscal.external_receipt_id ? `<small>№ ${escapeHtml(fiscal.external_receipt_id)}</small>` : ""}</div>` : "";
  const emergencyAction = sale.status === "payment_pending"
    && !fallback
    && !fiscal
    && payment?.payment_method === "card"
    && ["pending", "unknown"].includes(payment?.status)
    && (state.user?.is_admin || state.user?.is_superuser)
    ? '<button class="button warning" id="emergency-pos-payment" type="button">Решить инцидент</button>'
    : "";
  const action = sale.status === "draft" && ["canceled", "failed"].includes(payment?.status)
    ? '<div class="checkout-recovery-actions"><button class="button" id="retry-draft-payment" type="button">Повторить оплату</button><button class="button secondary" id="return-to-sale" type="button">Вернуться к продаже</button></div>'
    : sale.status === "payment_failed"
    ? '<button class="button" id="retry-payment" type="button">Повторить оплату</button>'
    : sale.status === "fiscalization_failed"
      ? '<button class="button" id="retry-fiscalization" type="button">Повторить формирование чека</button>'
      : emergencyAction;
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
    const paymentProviderName = context.payment_provider_name || context.provider_name || "провайдера оплаты";
    const fiscalProviderName = context.fiscal_provider_name || context.provider_name || "провайдера чека";
    const fiscalIntegrationName = context.fiscal_integration_name || context.integration_name;
    const providerDisabled = context.fiscalization_available ? "" : "disabled";
    const defaultCard = context.fiscalization_available ? "checked" : "";
    const defaultCashWithoutReceipt = context.fiscalization_available ? "" : "checked";
    dialog.innerHTML = `<form method="dialog"><p class="eyebrow">Подтверждение оплаты</p><h2>Продажа #${sale.sale_number}</h2><div class="checkout-facts"><span>${sale.item_quantity} поз.</span><span>Подытог: ${formatMoney(sale.subtotal_amount)}</span>${Number(sale.discount_amount) > 0 ? `<span>Скидка: −${formatMoney(sale.discount_amount)}</span>` : ""}<strong>К оплате: ${formatMoney(sale.total_amount)}</strong></div><fieldset class="checkout-method-options"><legend>Способ оплаты</legend><label><input type="radio" name="payment_option" value="card" ${defaultCard} ${providerDisabled}> <span><strong>${escapeHtml(acquiringLabel)}</strong><small>Оплата через ${escapeHtml(paymentProviderName)}, чек через ${escapeHtml(fiscalProviderName)}</small></span></label><label><input type="radio" name="payment_option" value="cash_with_receipt" ${providerDisabled}> <span><strong>Наличными + чек</strong><small>Наличные, фискальный чек через ${escapeHtml(fiscalProviderName)}</small></span></label><label><input type="radio" name="payment_option" value="cash_without_receipt" ${defaultCashWithoutReceipt}> <span><strong>Наличными без чека</strong><small>Продажа будет учтена в Core без отправки на кассу</small></span></label></fieldset><div class="cash-amount hidden" id="cash-amount"><span>К получению</span><strong>${formatMoney(sale.total_amount)}</strong></div><div class="checkout-method"><span class="muted small">Касса для фискального чека</span><strong id="checkout-fiscal-provider">${context.fiscalization_available ? escapeHtml(fiscalIntegrationName) : "Не настроена"}</strong><span class="muted small checkout-method-note" id="checkout-method-note"></span></div>${warnings}<div class="sale-actions"><button class="button secondary" value="cancel">Отмена</button><button class="button" id="confirm-checkout" value="default"></button></div></form>`;
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
          ? `Эквайринг не запускается; ${fiscalProviderName} получает только наличный чек`
          : `На устройстве откроется экран ${acquiringLabel.toLowerCase()}, затем сформируется чек`;
      dialog.querySelector("#checkout-fiscal-provider").textContent = option === "cash_without_receipt"
        ? "Не используется"
        : fiscalIntegrationName || "Не настроена";
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
  if (!sale || !["payment_pending", "paid", "fiscalization_pending"].includes(sale.status)) {
    if (sale?.status === "completed" && salesFiscalPendingObservedAt) {
      console.info("Checkout timing stage=T5", {
        saleId: sale.id,
        pendingToRenderedMs: Math.round(performance.now() - salesFiscalPendingObservedAt),
        renderedAt: new Date().toISOString(),
      });
    }
    salesProgressSaleId = null;
    salesProgressStartedAt = 0;
    salesFiscalPendingObservedAt = 0;
    return;
  }
  if (salesProgressSaleId !== sale.id) {
    salesProgressSaleId = sale.id;
    salesProgressStartedAt = performance.now();
    salesFiscalPendingObservedAt = 0;
  }
  if (sale.status === "fiscalization_pending" && !salesFiscalPendingObservedAt) {
    salesFiscalPendingObservedAt = performance.now();
  }
  const elapsed = performance.now() - salesProgressStartedAt;
  const delay = elapsed < 8000 ? 500 : elapsed < 20000 ? 1000 : 2000;
  salesProgressTimer = setTimeout(async () => {
    if (!window.location.hash.startsWith(`#sale/${sale.id}`)) return;
    try {
      const current = await api(`/api/sales/${sale.id}`);
      rememberSale(current);
      renderSalesWorkspace(current);
    } catch (error) {
      showToast(error.message, true);
      salesProgressTimer = setTimeout(() => scheduleCheckoutProgress(sale), 3000);
    }
  }, delay);
}

function renderSaleSwitcher(current) {
  const drafts = state.sales.drafts;
  const active = current?.status === "draft" && current.id === state.sales.activeId ? current : null;
  const deferred = drafts.filter((sale) => sale.id !== active?.id);
  const row = (sale, selected) => `<button class="sale-switch-row ${selected ? "active" : ""}" data-switch-sale="${sale.id}" type="button">
    <span>${selected ? "✓ " : ""}${sale.status === "fiscalization_failed" ? "⚠ " : ["payment_pending", "fiscalization_pending"].includes(sale.status) ? "⏳ " : ""}#${sale.sale_number}</span>
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
    startIntakeRealtime(id);
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
  if (state.session.status !== "draft") return renderClosedIntake();
  const activeItems = state.session.items.filter((item) => !item.abandoned_at);
  const productGroups = groupIntakeItems(activeItems);
  const productCount = productGroups.length;
  const variantCount = activeItems.length;
  root.innerHTML = `
    <div class="shell">
      ${topbar(true)}
      <div class="intake-workspace-status"><p class="eyebrow">Черновик сохраняется по шагам</p><span id="intake-sync-status" class="intake-sync-status ${intakeRealtimeStatus === "connected" ? "connected" : ""}">${intakeRealtimeStatus === "connected" ? "● Синхронизировано" : "● Переподключение…"}</span></div>
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

function renderClosedIntake() {
  const abandoned = state.session.status === "abandoned";
  root.innerHTML = `<div class="shell">
    ${topbar()}
    <section class="card" style="margin-top:36px">
      <p class="eyebrow">Приёмка закрыта</p>
      <h1>${abandoned ? "Приёмка отменена" : "Приёмка проведена"}</h1>
      <p class="muted">Редактирование больше недоступно. Состояние обновлено с сервера.</p>
      <button class="button full" id="closed-intake-home" type="button">На рабочее место</button>
    </section>
  </div>`;
  bindTopbar();
  document.querySelector("#closed-intake-home")?.addEventListener("click", loadHome);
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
      body: JSON.stringify({
        reason: "Удалено оператором из черновика",
        expected_intake_version: state.session.version,
      }),
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
    data.append("expected_intake_version", String(state.session.version));
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
  for (const form of document.querySelectorAll("[data-product-form]")) {
    await persistProductForm(form);
  }
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
  return serializeIntakeWrite(async () => {
    const item = state.session.items.find((value) => value.id === form.dataset.productForm);
    if (!item) return;
    const data = new FormData(form);
    const updated = await api(`/api/intake/sessions/${state.session.id}/items/${item.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        category_id: nullableText(data.get("category_id")),
        product_title: nullableText(data.get("product_title")),
        product_description: nullableText(data.get("product_description")),
        expected_intake_version: state.session.version,
      }),
    });
    state.session.version = updated.intake_version ?? state.session.version;
    state.session.items = state.session.items.map((value) => value.id === updated.id ? updated : value);
    // Refresh only backend-derived hints: never replace unsaved Variant form inputs.
    updateItemRequirements(updated);
  });
}

async function persistItemForm(form) {
  return serializeIntakeWrite(async () => {
    const item = state.session.items.find((value) => value.id === form.dataset.itemForm);
    if (!item) return;
    const payload = buildItemPayload(form, item);
    payload.expected_intake_version = state.session.version;
    const updated = await api(`/api/intake/sessions/${state.session.id}/items/${item.id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    state.session.version = updated.intake_version ?? state.session.version;
    state.session.items = state.session.items.map((value) => value.id === updated.id ? updated : value);
  });
}

async function saveAllItemForms() {
  if (!state.session || state.session.status !== "draft") return;
  const productForms = [...document.querySelectorAll("[data-product-form]")];
  const forms = [...document.querySelectorAll("[data-item-form]")];
  for (const form of productForms) await persistProductForm(form);
  for (const form of forms) await persistItemForm(form);
}

async function saveSupplier(event) {
  const supplierId = event.target.value || null;
  try {
    await saveAllItemForms();
    state.session = await api(`/api/intake/sessions/${state.session.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        supplier_id: supplierId,
        expected_intake_version: state.session.version,
      }),
    });
    renderWorkspace();
  } catch (error) { showToast(error.message, true); }
}

async function completeSession() {
  if (intakeCompletionInFlight) return;
  intakeCompletionInFlight = true;
  const button = document.querySelector("#complete-session");
  if (button) {
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span> Проводим';
  }
  const sessionId = state.session.id;
  try {
    state.session = await api(`/api/intake/sessions/${sessionId}`);
    if (state.session.status === "draft") await saveAllItemForms();
    const expectedVersion = state.session.version;
    state.result = await api(`/api/intake/sessions/${sessionId}/complete?expected_intake_version=${expectedVersion}`, { method: "POST" });
    state.session.status = "completed";
    state.session.version = state.result.intake_version;
    stopIntakeRealtime();
    await loadIntakeAqsi();
    renderResult();
  } catch (error) {
    const recovered = error.status === 409 && await recoverCompletedIntake(sessionId);
    if (!recovered) {
      showToast(error.message, true);
      if (button) {
        button.disabled = false;
        button.textContent = "Провести приёмку";
      }
    }
  } finally {
    intakeCompletionInFlight = false;
  }
}

async function recoverCompletedIntake(sessionId) {
  try {
    const current = await api(`/api/intake/sessions/${sessionId}`);
    if (current.status !== "completed") return false;
    state.session = current;
    state.result = await api(`/api/intake/sessions/${sessionId}/complete`, { method: "POST" });
    state.session.version = state.result.intake_version;
    stopIntakeRealtime();
    await loadIntakeAqsi();
    renderResult();
    return true;
  } catch {
    return false;
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
    collectionId: value.collectionId || "",
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
  if (catalog.collectionId) params.set("collection_id", catalog.collectionId);
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
    [state.operations.products, state.categories, state.suppliers, state.operations.collections] = await Promise.all([
      api(`/api/operations/catalog/products?${params}`),
      api("/api/catalog/categories"),
      api("/api/purchasing/suppliers"),
      api("/api/catalog/collections"),
    ]);
    renderOperationsCatalog(catalog);
  } catch (error) { showToast(error.message, true); }
}

function renderOperationsCatalog(catalog) {
  document.body.classList.remove("catalog-drawer-open");
  const selectedCollection = state.operations.collections.find((collection) => collection.id === catalog.collectionId);
  const rows = state.operations.products.length
    ? state.operations.products.map((product) => renderCatalogProductCard(product, catalog)).join("")
    : selectedCollection
      ? `<div class="empty"><strong>В подборке пока нет товаров.</strong>${selectedCollection.type === "dynamic" ? '<p class="muted small">Ни один вариант сейчас не соответствует условиям подборки.</p>' : ""}</div>`
      : '<div class="empty">Товары не найдены</div>';
  const selectedCategory = state.categories.find((category) => category.id === catalog.categoryId);
  const categoryLabel = selectedCategory?.title || "Все";
  const primaryFilterLabel = selectedCollection
    ? `Подборка: ${selectedCollection.name}`
    : `Категория: ${categoryLabel}`;
  root.innerHTML = `<div class="shell catalog-shell">
    ${topbar(true)}
    <div class="catalog-heading"><div><p class="eyebrow">Каталог</p><h1>Товары</h1></div><div class="catalog-heading-actions"><button class="button" id="catalog-new-product" type="button">＋ Новый товар</button></div></div>
    ${renderCatalogSalesContext(catalog)}
    <div class="catalog-workspace">
      <aside class="catalog-sidebar" aria-label="Навигация и фильтры каталога">
        ${renderCatalogCategoryNavigation(catalog)}
        <button class="button secondary full catalog-category-entry" data-open-category-create type="button">＋ Категория</button>
        <button class="button ghost full category-management-entry" data-open-category-management type="button">Управление категориями</button>
        <div class="divider"></div>
        ${renderCatalogCollectionNavigation(catalog)}
        <button class="button ghost full collection-management-entry" data-open-collection-management type="button">Управление подборками</button>
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
          <button class="button secondary" id="open-category-drawer" type="button">${escapeHtml(primaryFilterLabel)} <span aria-hidden="true">⌄</span></button>
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
      <div class="divider"></div>
      ${renderCatalogCollectionNavigation(catalog)}
      <button class="button ghost full collection-management-entry" data-open-collection-management type="button">Управление подборками</button>
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
  const allSelected = !catalog.categoryId && !catalog.collectionId;
  return `<div class="catalog-category-nav"><h2>Категории</h2><ul><li><button class="category-link ${allSelected ? "active" : ""}" data-category-id="" type="button" aria-pressed="${allSelected}">Все товары</button></li>${branch(null)}</ul></div>`;
}

function renderCatalogCollectionNavigation(catalog) {
  const rows = state.operations.collections.map((collection) => `<li><button class="category-link collection-link ${catalog.collectionId === collection.id ? "active" : ""}" data-collection-id="${collection.id}" type="button" aria-pressed="${catalog.collectionId === collection.id}"><span class="catalog-nav-label">${escapeHtml(collection.name)}</span><span class="catalog-nav-count">${collection.variant_count}</span></button></li>`).join("");
  return `<div class="catalog-category-nav catalog-collection-nav"><h2>Подборки</h2><ul>${rows || '<li><span class="muted small catalog-nav-empty">Активных подборок пока нет</span></li>'}</ul></div>`;
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
    button.addEventListener("click", () => openOperationsCatalog({ ...catalog, categoryId: button.dataset.categoryId, collectionId: "" }));
  });
  document.querySelectorAll("[data-collection-id]").forEach((button) => {
    button.addEventListener("click", () => openOperationsCatalog({ ...catalog, categoryId: "", collectionId: button.dataset.collectionId }));
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
  document.querySelectorAll("[data-open-collection-management]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelector("#catalog-category-dialog")?.close();
      openCollections();
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

function collectionTypeLabel(type) {
  return type === "static" ? "Статическая" : "Динамическая";
}

function collectionOperatorLabel(operator) {
  return { eq: "равно", lt: "меньше", lte: "не больше", gt: "больше", gte: "не меньше" }[operator] || operator;
}

function collectionFieldLabel(field) {
  return { price: "Цена", category: "Категория", stock: "Остаток", is_active: "Активность" }[field] || field;
}

function collectionRuleValueLabel(rule) {
  if (rule.field === "category") {
    return state.categories.find((category) => category.id === rule.value)?.title || "Недоступная категория";
  }
  if (rule.field === "is_active") return String(rule.value) === "true" ? "активен" : "неактивен";
  if (rule.field === "price") return formatMoney(Number(rule.value));
  return formatQuantity(rule.value);
}

function renderCollectionRuleSummary(rule) {
  return `<span class="collection-rule-summary"><strong>${escapeHtml(collectionFieldLabel(rule.field))}</strong><span>${escapeHtml(collectionOperatorLabel(rule.operator))} · ${escapeHtml(collectionRuleValueLabel(rule))}</span></span>`;
}

async function openCollections() {
  try {
    recordRoute("collections");
    logicalParent = () => openOperationsCatalog(state.operations.catalog || {});
    [state.operations.collections, state.categories] = await Promise.all([
      api("/api/catalog/collections?include_inactive=true"),
      api("/api/catalog/categories?status=all"),
    ]);
    renderCollections();
  } catch (error) { showToast(error.message, true); }
}

function renderCollections() {
  const rows = state.operations.collections.length
    ? state.operations.collections.map((collection) => `<article class="collection-row card ${collection.is_active ? "" : "disabled"}">
        <button class="collection-row-main" data-open-collection="${collection.id}" type="button">
          <span class="collection-row-title"><strong>${escapeHtml(collection.name)}</strong>${collection.is_active ? "" : '<span class="chip warn">Отключена</span>'}</span>
          <span class="muted small">${escapeHtml(collectionTypeLabel(collection.type))} · ${collection.variant_count} ${pluralizeRu(collection.variant_count, "вариант", "варианта", "вариантов")}</span>
          ${collection.description ? `<span>${escapeHtml(collection.description)}</span>` : ""}
          <span class="muted small">Обновлена ${formatDate(collection.updated_at)}</span>
        </button>
        <span class="collection-row-actions"><button class="button ghost compact" data-edit-collection="${collection.id}" type="button">Изменить</button><button class="button ${collection.is_active ? "danger" : "secondary"} compact" data-toggle-collection="${collection.id}" type="button">${collection.is_active ? "Отключить" : "Включить"}</button><button class="button ghost compact danger-text" data-delete-collection="${collection.id}" type="button">Удалить</button></span>
      </article>`).join("")
    : '<div class="empty">Коллекций пока нет</div>';
  root.innerHTML = `<div class="shell collection-shell">
    ${topbar(true)}
    <div class="catalog-heading"><div><p class="eyebrow">Каталог · Управление</p><h1>Подборки</h1><p class="muted">Статические и динамические группы вариантов для ежедневной работы в Каталоге.</p></div><button class="button" id="collection-create" type="button">＋ Подборка</button></div>
    <div class="collection-list">${rows}</div>
    <dialog class="catalog-action-dialog" id="catalog-action-dialog"><div id="catalog-action-content"></div></dialog>
  </div>`;
  bindTopbar();
  document.querySelector("#collection-create").addEventListener("click", () => openCollectionEditor());
  document.querySelectorAll("[data-open-collection]").forEach((button) => button.addEventListener("click", () => openCollection(button.dataset.openCollection)));
  document.querySelectorAll("[data-edit-collection]").forEach((button) => button.addEventListener("click", () => {
    const collection = state.operations.collections.find((item) => item.id === button.dataset.editCollection);
    loadCollectionEditor(collection);
  }));
  document.querySelectorAll("[data-toggle-collection]").forEach((button) => button.addEventListener("click", () => toggleCollection(button.dataset.toggleCollection, button)));
  document.querySelectorAll("[data-delete-collection]").forEach((button) => button.addEventListener("click", () => {
    const collection = state.operations.collections.find((item) => item.id === button.dataset.deleteCollection);
    if (collection) openCollectionDeleteConfirmation(collection);
  }));
}

async function loadCollectionEditor(summary) {
  try {
    const collection = await api(`/api/catalog/collections/${summary.id}`);
    openCollectionEditor(collection);
  } catch (error) { showToast(error.message, true); }
}

function collectionRuleOperatorOptions(field, selected = "eq") {
  const operators = ["category", "is_active"].includes(field)
    ? [["eq", "Равно"]]
    : [["eq", "Равно"], ["lt", "Меньше"], ["lte", "Не больше"], ["gt", "Больше"], ["gte", "Не меньше"]];
  return operators.map(([value, label]) => `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`).join("");
}

function collectionRuleValueControl(field, value = "") {
  if (field === "category") {
    const options = state.categories
      .filter((category) => category.is_active && !category.is_archived)
      .map((category) => `<option value="${category.id}" ${String(value) === category.id ? "selected" : ""}>${escapeHtml(category.title)}</option>`)
      .join("");
    return `<select class="collection-rule-value" aria-label="Категория" required><option value="">Выберите категорию</option>${options}</select>`;
  }
  if (field === "is_active") {
    return `<select class="collection-rule-value" aria-label="Активность" required><option value="true" ${String(value) !== "false" ? "selected" : ""}>Активен</option><option value="false" ${String(value) === "false" ? "selected" : ""}>Неактивен</option></select>`;
  }
  const step = field === "price" ? "0.01" : "1";
  return `<input class="collection-rule-value" type="number" min="0" step="${step}" value="${escapeHtml(value)}" inputmode="decimal" aria-label="Значение" required>`;
}

function collectionRuleEditorRow(rule = { field: "price", operator: "lte", value: "" }) {
  return `<div class="collection-rule-row" data-collection-rule-row>
    <select class="collection-rule-field" aria-label="Поле правила">
      <option value="price" ${rule.field === "price" ? "selected" : ""}>Цена</option>
      <option value="category" ${rule.field === "category" ? "selected" : ""}>Категория</option>
      <option value="stock" ${rule.field === "stock" ? "selected" : ""}>Остаток</option>
      <option value="is_active" ${rule.field === "is_active" ? "selected" : ""}>Активность</option>
    </select>
    <select class="collection-rule-operator" aria-label="Оператор">${collectionRuleOperatorOptions(rule.field, rule.operator)}</select>
    <span class="collection-rule-value-wrap">${collectionRuleValueControl(rule.field, rule.value)}</span>
    <button class="button ghost compact" data-remove-collection-rule type="button" aria-label="Удалить правило">Удалить</button>
  </div>`;
}

function bindCollectionRuleRow(row) {
  row.querySelector(".collection-rule-field").addEventListener("change", (event) => {
    const field = event.target.value;
    row.querySelector(".collection-rule-operator").innerHTML = collectionRuleOperatorOptions(field);
    row.querySelector(".collection-rule-value-wrap").innerHTML = collectionRuleValueControl(field);
  });
  row.querySelector("[data-remove-collection-rule]").addEventListener("click", () => row.remove());
}

function addCollectionRuleRow(rule) {
  const container = document.querySelector("#collection-rules");
  const wrapper = document.createElement("div");
  wrapper.innerHTML = collectionRuleEditorRow(rule);
  const row = wrapper.firstElementChild;
  container.append(row);
  bindCollectionRuleRow(row);
}

function setCollectionRuleEditorVisibility(type) {
  const section = document.querySelector("#collection-rules-section");
  if (!section) return;
  section.classList.toggle("hidden", type !== "dynamic");
  if (type === "dynamic" && !section.querySelector("[data-collection-rule-row]")) addCollectionRuleRow();
}

function collectCollectionRules() {
  return [...document.querySelectorAll("[data-collection-rule-row]")].map((row) => ({
    field: row.querySelector(".collection-rule-field").value,
    operator: row.querySelector(".collection-rule-operator").value,
    value: row.querySelector(".collection-rule-value").value,
  }));
}

function openCollectionEditor(collection = null) {
  const editing = Boolean(collection);
  const type = collection?.type || "static";
  const rules = collection?.rules || [];
  const dialog = openCatalogActionDialog(`<form id="collection-editor-form">
    <div class="catalog-dialog-head"><div><p class="eyebrow">${editing ? "Коллекция" : "Новая коллекция"}</p><h2>${editing ? `Изменить «${escapeHtml(collection.name)}»` : "Создать коллекцию"}</h2></div><button class="drawer-close" data-action-cancel type="button" aria-label="Закрыть">×</button></div>
    <div class="field"><label for="collection-name">Название</label><input id="collection-name" name="name" value="${escapeHtml(collection?.name || "")}" maxlength="255" required autofocus></div>
    <div class="field"><label for="collection-description">Описание <span class="muted">(необязательно)</span></label><textarea id="collection-description" name="description">${escapeHtml(collection?.description || "")}</textarea></div>
    <div class="field-row collection-editor-fields"><div class="field"><label for="collection-type">Тип</label><select id="collection-type" name="type" ${editing ? "disabled" : ""}><option value="static" ${type === "static" ? "selected" : ""}>Статическая · ручной состав</option><option value="dynamic" ${type === "dynamic" ? "selected" : ""}>Динамическая · по правилам</option></select></div><div class="field"><label for="collection-sort">Порядок</label><input id="collection-sort" name="sort_order" type="number" step="1" value="${collection?.sort_order ?? 0}"></div></div>
    <label class="catalog-check collection-active-check"><input name="is_active" type="checkbox" ${collection?.is_active === false ? "" : "checked"}><span>Коллекция активна</span></label>
    <section id="collection-rules-section" class="collection-rules-section ${type === "dynamic" ? "" : "hidden"}"><div class="section-heading"><div><strong>Правила</strong><p class="muted small">Все условия применяются одновременно (AND). Состав пересчитывается по текущим данным.</p></div><button class="button secondary compact" id="collection-add-rule" type="button">＋ Условие</button></div><div id="collection-rules">${rules.map(collectionRuleEditorRow).join("")}</div><div class="collection-preview-actions"><button class="button secondary compact" id="collection-preview" type="button">Проверить состав</button><span id="collection-preview-result" class="muted small"></span></div></section>
    <div class="catalog-dialog-actions"><button class="button secondary" data-action-cancel type="button">Отмена</button><button class="button" type="submit">${editing ? "Сохранить" : "Создать"}</button></div>
  </form>`);
  dialog.querySelectorAll("[data-action-cancel]").forEach((button) => button.addEventListener("click", () => dialog.close()));
  dialog.querySelectorAll("[data-collection-rule-row]").forEach(bindCollectionRuleRow);
  dialog.querySelector("#collection-type").addEventListener("change", (event) => setCollectionRuleEditorVisibility(event.target.value));
  dialog.querySelector("#collection-add-rule").addEventListener("click", () => addCollectionRuleRow());
  dialog.querySelector("#collection-preview").addEventListener("click", previewCollectionRules);
  dialog.querySelector("#collection-editor-form").addEventListener("submit", (event) => saveCollection(event, collection));
}

async function previewCollectionRules() {
  const result = document.querySelector("#collection-preview-result");
  const rules = collectCollectionRules();
  if (!rules.length) { result.textContent = "Добавьте хотя бы одно условие"; return; }
  try {
    const preview = await api("/api/catalog/collections/preview", { method: "POST", body: JSON.stringify({ rules }) });
    result.textContent = `Сейчас подходит: ${preview.count} ${pluralizeRu(preview.count, "вариант", "варианта", "вариантов")}`;
  } catch (error) { showToast(error.message, true); }
}

async function saveCollection(event, collection) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button[type="submit"]');
  const data = new FormData(form);
  const type = collection?.type || data.get("type");
  const rules = type === "dynamic" ? collectCollectionRules() : [];
  if (type === "dynamic" && !rules.length) { showToast("Добавьте хотя бы одно правило", true); return; }
  button.disabled = true;
  const metadata = {
    name: String(data.get("name") || "").trim(),
    description: nullableText(data.get("description")),
    sort_order: Number(data.get("sort_order") || 0),
    is_active: data.get("is_active") === "on",
  };
  try {
    if (collection) {
      if (type === "dynamic") await api(`/api/catalog/collections/${collection.id}/rules`, { method: "PUT", body: JSON.stringify({ rules }) });
      await api(`/api/catalog/collections/${collection.id}`, { method: "PATCH", body: JSON.stringify(metadata) });
    } else {
      await api("/api/catalog/collections", { method: "POST", body: JSON.stringify({ ...metadata, type, rules }) });
    }
    document.querySelector("#catalog-action-dialog")?.close();
    if (state.operations.collection?.id === collection?.id) await openCollection(collection.id);
    else await openCollections();
    showToast(collection ? "Коллекция сохранена" : "Коллекция создана");
  } catch (error) { showToast(error.message, true); button.disabled = false; }
}

async function toggleCollection(collectionId, button) {
  const collection = state.operations.collections.find((item) => item.id === collectionId) || state.operations.collection;
  if (!collection) return;
  button.disabled = true;
  try {
    await api(`/api/catalog/collections/${collectionId}`, { method: "PATCH", body: JSON.stringify({ is_active: !collection.is_active }) });
    if (state.operations.collection?.id === collectionId) await openCollection(collectionId);
    else await openCollections();
    showToast(collection.is_active ? "Коллекция отключена" : "Коллекция включена");
  } catch (error) { showToast(error.message, true); button.disabled = false; }
}

function openCollectionDeleteConfirmation(collection) {
  const dialog = openCatalogActionDialog(`<div class="catalog-delete-dialog">
    <div class="catalog-dialog-head"><div><p class="eyebrow">Удаление подборки</p><h2>Удалить коллекцию «${escapeHtml(collection.name)}»?</h2></div><button class="drawer-close" data-action-cancel type="button" aria-label="Закрыть">×</button></div>
    <p>Товары и варианты удалены не будут.</p>
    <p class="muted small">Будут удалены только правила и ручные связи этой коллекции.</p>
    <div class="catalog-dialog-actions"><button class="button secondary" data-action-cancel type="button">Отмена</button><button class="button danger" data-confirm-delete-collection type="button">Удалить коллекцию</button></div>
  </div>`);
  dialog.querySelectorAll("[data-action-cancel]").forEach((button) => button.addEventListener("click", () => dialog.close()));
  dialog.querySelector("[data-confirm-delete-collection]").addEventListener("click", (event) => deleteCollection(collection.id, event.currentTarget));
}

async function deleteCollection(collectionId, button) {
  button.disabled = true;
  try {
    await api(`/api/catalog/collections/${collectionId}`, { method: "DELETE" });
    document.querySelector("#catalog-action-dialog")?.close();
    state.operations.collection = null;
    await openCollections();
    showToast("Коллекция удалена");
  } catch (error) { showToast(error.message, true); button.disabled = false; }
}

function renderCollectionVariantRow(variant, removable = false) {
  return `<article class="collection-variant-row"><span><strong>${escapeHtml(variant.product_name)}${meaningfulVariantSuffix(variant.variant_name)}</strong><small class="muted">${escapeHtml(variant.sku)} · ${variant.current_retail_price === null ? "без цены" : formatMoney(variant.current_retail_price)} · ${formatQuantity(variant.stock)}</small></span>${removable ? `<button class="button ghost compact danger-text" data-remove-collection-member="${variant.id}" type="button">Убрать</button>` : ""}</article>`;
}

async function openCollection(collectionId, returnProductId = null) {
  try {
    recordRoute("collection", { collectionId });
    logicalParent = returnProductId ? () => openOperationsProduct(returnProductId) : openCollections;
    [state.operations.collection, state.operations.collectionVariants, state.categories] = await Promise.all([
      api(`/api/catalog/collections/${collectionId}`),
      api(`/api/catalog/collections/${collectionId}/variants?limit=100`),
      api("/api/catalog/categories?status=all"),
    ]);
    state.operations.collectionSearch = [];
    renderCollectionDetail();
  } catch (error) { showToast(error.message, true); }
}

function renderCollectionDetail() {
  const collection = state.operations.collection;
  const variants = state.operations.collectionVariants.length
    ? state.operations.collectionVariants.map((variant) => renderCollectionVariantRow(variant, collection.type === "static")).join("")
    : '<div class="empty">Подходящих вариантов нет</div>';
  const rules = collection.type === "dynamic"
    ? `<section class="card collection-rule-card"><div class="section-heading"><div><h2>Правила</h2><p class="muted small">Все условия применяются одновременно. Состав не хранится и пересчитывается по текущим цене, категории, остатку и активности.</p></div></div><div class="collection-rule-summaries">${collection.rules.map(renderCollectionRuleSummary).join("")}</div></section>`
    : "";
  const staticAdd = collection.type === "static" && collection.is_active ? `<section class="card collection-member-search"><div class="section-heading"><div><h2>Добавить варианты</h2><p class="muted small">Найдите вариант по товару, названию, SKU или штрихкоду.</p></div></div><form class="search-row" id="collection-variant-search"><input name="query" placeholder="Название, SKU или штрихкод" autocomplete="off" required><button class="button secondary" type="submit">Найти</button></form><div id="collection-search-results"></div></section>` : "";
  root.innerHTML = `<div class="shell collection-shell">
    ${topbar(true)}
    <div class="catalog-heading"><div><p class="eyebrow">Каталог · Подборки</p><h1>${escapeHtml(collection.name)} ${collection.is_active ? "" : '<span class="chip warn">Отключена</span>'}</h1><p class="muted">${escapeHtml(collection.description || collectionTypeLabel(collection.type))}</p></div><div class="catalog-heading-actions"><button class="button secondary" id="collection-edit" type="button">Изменить</button><button class="button ${collection.is_active ? "danger" : "secondary"}" id="collection-toggle" type="button">${collection.is_active ? "Отключить" : "Включить"}</button><button class="button ghost danger-text" id="collection-delete" type="button">Удалить коллекцию</button></div></div>
    <div class="collection-facts"><span><small>Тип</small><strong>${escapeHtml(collectionTypeLabel(collection.type))}</strong></span><span><small>Состав</small><strong>${collection.variant_count} ${pluralizeRu(collection.variant_count, "вариант", "варианта", "вариантов")}</strong></span><span><small>Обновлена</small><strong>${formatDate(collection.updated_at)}</strong></span></div>
    ${rules}${staticAdd}
    <div class="section-heading"><h2>${collection.type === "static" ? "Состав" : "Текущий результат"}</h2><span class="muted small">${collection.variant_count}</span></div>
    <div class="collection-variant-list">${variants}</div>
    <dialog class="catalog-action-dialog" id="catalog-action-dialog"><div id="catalog-action-content"></div></dialog>
  </div>`;
  bindTopbar();
  document.querySelector("#collection-edit").addEventListener("click", () => openCollectionEditor(collection));
  document.querySelector("#collection-toggle").addEventListener("click", (event) => toggleCollection(collection.id, event.currentTarget));
  document.querySelector("#collection-delete").addEventListener("click", () => openCollectionDeleteConfirmation(collection));
  document.querySelector("#collection-variant-search")?.addEventListener("submit", searchCollectionVariants);
  document.querySelectorAll("[data-remove-collection-member]").forEach((button) => button.addEventListener("click", () => removeCollectionMember(button.dataset.removeCollectionMember, button)));
}

async function searchCollectionVariants(event) {
  event.preventDefault();
  const query = String(new FormData(event.currentTarget).get("query") || "").trim();
  if (!query) return;
  try {
    state.operations.collectionSearch = await api(`/api/catalog/collections/variant-search?query=${encodeURIComponent(query)}&limit=12`);
    const existing = new Set(state.operations.collectionVariants.map((variant) => variant.id));
    const html = state.operations.collectionSearch.length
      ? `${state.operations.collectionSearch.map((variant) => `<label class="collection-variant-row collection-search-option"><span><strong>${escapeHtml(variant.product_name)}${meaningfulVariantSuffix(variant.variant_name)}</strong><small class="muted">${escapeHtml(variant.sku)} · ${variant.current_retail_price === null ? "без цены" : formatMoney(variant.current_retail_price)} · ${formatQuantity(variant.stock)}</small></span><input type="checkbox" data-collection-member-choice value="${variant.id}" ${existing.has(variant.id) ? "checked disabled" : ""} aria-label="Добавить ${escapeHtml(variant.sku)}"></label>`).join("")}<button class="button secondary collection-add-selected" id="collection-add-selected" type="button">Добавить выбранные</button>`
      : '<div class="empty">Варианты не найдены</div>';
    document.querySelector("#collection-search-results").innerHTML = html;
    document.querySelector("#collection-add-selected")?.addEventListener("click", (event) => {
      const ids = [...document.querySelectorAll("[data-collection-member-choice]:checked:not(:disabled)")].map((input) => input.value);
      addCollectionMembers(ids, event.currentTarget);
    });
  } catch (error) { showToast(error.message, true); }
}

async function addCollectionMembers(variantIds, button) {
  if (!variantIds.length) { showToast("Выберите хотя бы один вариант", true); return; }
  button.disabled = true;
  try {
    await api(`/api/catalog/collections/${state.operations.collection.id}/memberships`, { method: "POST", body: JSON.stringify({ variant_ids: variantIds }) });
    await openCollection(state.operations.collection.id);
    showToast(`${variantIds.length} ${pluralizeRu(variantIds.length, "вариант добавлен", "варианта добавлены", "вариантов добавлены")} в коллекцию`);
  } catch (error) { showToast(error.message, true); button.disabled = false; }
}

async function removeCollectionMember(variantId, button) {
  button.disabled = true;
  try {
    await api(`/api/catalog/collections/${state.operations.collection.id}/memberships/${variantId}`, { method: "DELETE" });
    await openCollection(state.operations.collection.id);
    showToast("Вариант убран из коллекции");
  } catch (error) { showToast(error.message, true); button.disabled = false; }
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
    await Promise.all([
      loadAqsiStates(state.operations.product.variants.filter((variant) => !variant.is_archived)),
      loadRentalTariffs(state.operations.product.variants),
      loadVariantCollectionStates(state.operations.product.variants),
    ]);
    renderOperationsProduct();
  } catch (error) { showToast(error.message, true); }
}

async function loadRentalTariffs(variants) {
  state.operations.tariffs = new Map();
  const rentable = variants.filter((variant) => !variant.is_archived && variant.rental_asset_count > 0);
  const entries = await Promise.all(rentable.map(async (variant) => [
    variant.id,
    await api(`/api/rental/variants/${variant.id}/tariffs?view=management`),
  ]));
  entries.forEach(([variantId, tariffs]) => state.operations.tariffs.set(variantId, tariffs));
}

async function loadVariantCollectionStates(variants) {
  state.operations.variantCollections = new Map();
  const operational = variants.filter((variant) => !variant.is_archived);
  const entries = await Promise.all(operational.map(async (variant) => [
    variant.id,
    await api(`/api/catalog/collections/variants/${variant.id}`),
  ]));
  entries.forEach(([variantId, collections]) => state.operations.variantCollections.set(variantId, collections));
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

function renderVariantCollections(variant, readOnly = false) {
  const collections = state.operations.variantCollections.get(variant.id);
  if (!collections) return "";
  const staticOptions = collections.static_collections.map((collection) => `<label class="catalog-check"><input type="checkbox" name="collection_id" value="${collection.id}" ${collection.is_member ? "checked" : ""} ${readOnly ? "disabled" : ""}><span>${escapeHtml(collection.name)}</span></label>`).join("");
  const dynamicMatches = collections.dynamic_collections.map((collection) => `<button class="chip collection-match-chip" data-open-variant-collection="${collection.id}" type="button" title="Состав вычислен по правилам">${escapeHtml(collection.name)} · по правилам</button>`).join("");
  if (!staticOptions && !dynamicMatches) return `<section class="variant-collections"><div class="variant-section-heading"><strong>Коллекции</strong></div><p class="muted small">Вариант пока не входит в активные коллекции.</p></section>`;
  return `<section class="variant-collections"><div class="variant-section-heading"><strong>Коллекции</strong>${staticOptions && !readOnly ? `<button class="link-button" type="submit" form="variant-collections-${variant.id}">Сохранить</button>` : ""}</div>
    ${staticOptions ? `<form class="variant-static-collections" id="variant-collections-${variant.id}" data-variant-collections-form="${variant.id}">${staticOptions}</form>` : ""}
    ${dynamicMatches ? `<div class="variant-dynamic-collections"><span class="muted small">По правилам</span><span class="chips">${dynamicMatches}</span></div>` : ""}
  </section>`;
}

async function saveVariantCollections(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const variantId = form.dataset.variantCollectionsForm;
  const button = document.querySelector(`[form="variant-collections-${variantId}"]`);
  if (button) button.disabled = true;
  try {
    const collectionIds = new FormData(form).getAll("collection_id");
    const result = await api(`/api/catalog/collections/variants/${variantId}`, {
      method: "PUT",
      body: JSON.stringify({ collection_ids: collectionIds }),
    });
    state.operations.variantCollections.set(variantId, result);
    await openOperationsProduct(state.operations.product.id);
    showToast("Коллекции варианта сохранены");
  } catch (error) {
    showToast(error.message, true);
    if (button?.isConnected) button.disabled = false;
  }
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
    ${readOnly ? "" : `<span class="variant-info-action"><button class="link-button" data-set-rental-prices="${variant.id}" type="button">Базовые условия</button>${Number(variant.ordinary_quantity) > 0 ? `<button class="link-button" data-allocate-rental="${variant.id}" type="button">Выделить ещё</button>` : ""}</span>`}
  </section>${renderVariantTariffs(variant, readOnly)}`;
}

function tariffPeriod(tariff) {
  if (tariff.type === "slot") return `${String(tariff.slot_start).slice(0, 5)}–${String(tariff.slot_end).slice(0, 5)}`;
  const minutes = Number(tariff.duration_minutes);
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return [hours ? `${hours} ч` : "", rest ? `${rest} мин` : ""].filter(Boolean).join(" ");
}

function localToday() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

function tariffValidity(tariff) {
  const today = localToday();
  if (tariff.valid_from > today) return `<span class="chip">С ${escapeHtml(tariff.valid_from)}</span>`;
  if (tariff.valid_to && tariff.valid_to < today) return '<span class="chip warn">Срок завершён</span>';
  return '<span class="chip good">Действует</span>';
}

function renderVariantTariffs(variant, readOnly = false) {
  const tariffs = state.operations.tariffs.get(variant.id) || [];
  const cards = tariffs.map((tariff) => `<article class="tariff-card">
    <div class="tariff-card-heading"><span><strong>${escapeHtml(tariff.name)}</strong><small>${escapeHtml(tariffPeriod(tariff))}</small></span><strong>${formatMoney(tariff.price)}</strong></div>
    <div class="tariff-facts"><span>Льготный период: ${tariff.grace_minutes} мин</span><span>Залог: ${tariff.deposit_amount === null ? "не указан" : formatMoney(tariff.deposit_amount)}</span><span>${tariff.requires_agreement ? "Договор обязателен" : "Без обязательного договора"}</span></div>
    <div class="tariff-card-actions">${tariffValidity(tariff)}<span class="tariff-action-buttons"><button class="link-button" data-tariff-history="${tariff.id}" type="button">История</button>${readOnly ? "" : `<button class="link-button" data-edit-tariff="${tariff.id}" data-variant-id="${variant.id}" type="button">Изменить</button><button class="link-button danger-text" data-archive-tariff="${tariff.id}" type="button">Архивировать</button>`}</span></div>
  </article>`).join("");
  return `<section class="tariff-section">
    <div class="variant-section-heading"><strong>Тарифы</strong>${readOnly ? "" : `<button class="link-button tariff-add-button" data-add-tariff="${variant.id}" type="button">+ Добавить тариф</button>`}</div>
    ${cards ? `<div class="tariff-list">${cards}</div>` : '<p class="muted small tariff-empty">Тарифы ещё не настроены.</p>'}
  </section>`;
}

function renderOperationsVariant(variant, product, canDeleteCatalog, position = 0, variantCount = 1) {
  const readOnly = product.is_archived || variant.is_archived;
  const orderActions = !product.is_archived && variantCount > 1
    ? `<span class="variant-order-actions" aria-label="Порядок варианта ${escapeHtml(visibleVariantTitle(variant.title, "Единственный вариант"))}"><span class="muted small">${position + 1} из ${variantCount}</span><button class="button ghost compact" data-move-variant="${variant.id}" data-move-direction="-1" type="button" ${position === 0 ? "disabled" : ""}>↑ Выше</button><button class="button ghost compact" data-move-variant="${variant.id}" data-move-direction="1" type="button" ${position === variantCount - 1 ? "disabled" : ""}>↓ Ниже</button></span>`
    : "";
  return `<article class="variant-commercial-card card ${variant.is_archived ? "archived" : ""}">
    ${orderActions}
    ${renderCatalogMedia("catalog_variant", variant.id, product, readOnly)}
    <span class="variant-commercial-main">
      <span class="variant-identity"><span class="variant-info-section"><span class="variant-info-content"><span class="muted small">Название варианта</span><strong>${escapeHtml(visibleVariantTitle(variant.title, "Единственный вариант"))}</strong></span>${readOnly ? "" : `<span class="variant-info-action"><button class="link-button" data-rename-variant="${variant.id}" type="button">Изменить</button></span>`}</span>${variant.is_archived ? '<span class="chip archive-chip">АРХИВНЫЙ ВАРИАНТ</span>' : ""}<span class="muted small">SKU · ${escapeHtml(variant.sku)}</span></span>
      ${renderVariantCharacteristics(variant, readOnly)}
      ${renderVariantCollections(variant, readOnly)}
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
    ? product.variants.map((variant, index) => renderOperationsVariant(variant, product, canDeleteCatalog, index, product.variants.length)).join("")
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
  document.querySelectorAll("[data-variant-collections-form]").forEach((form) => form.addEventListener("submit", saveVariantCollections));
  document.querySelectorAll("[data-open-variant-collection]").forEach((button) => button.addEventListener("click", () => openCollection(button.dataset.openVariantCollection, product.id)));
  document.querySelectorAll("[data-replace-barcode]").forEach((button) => button.addEventListener("click", () => replaceVariantBarcode(button.dataset.replaceBarcode)));
  document.querySelectorAll("[data-delete-external-barcode]").forEach((button) => button.addEventListener("click", () => deleteExternalBarcode(button.dataset.deleteExternalBarcode)));
  document.querySelectorAll("[data-set-sale-price]").forEach((button) => button.addEventListener("click", () => editCatalogSalePrice(button.dataset.setSalePrice)));
  document.querySelectorAll("[data-set-rental-prices]").forEach((button) => button.addEventListener("click", () => editCatalogRentalPrices(button.dataset.setRentalPrices)));
  document.querySelectorAll("[data-add-tariff]").forEach((button) => button.addEventListener("click", () => openRentalTariffEditor(button.dataset.addTariff)));
  document.querySelectorAll("[data-edit-tariff]").forEach((button) => button.addEventListener("click", () => openRentalTariffEditor(button.dataset.variantId, button.dataset.editTariff)));
  document.querySelectorAll("[data-tariff-history]").forEach((button) => button.addEventListener("click", () => openRentalTariffHistory(button.dataset.tariffHistory)));
  document.querySelectorAll("[data-archive-tariff]").forEach((button) => button.addEventListener("click", () => archiveRentalTariff(button.dataset.archiveTariff)));
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
  document.querySelectorAll("[data-move-variant]").forEach((button) => button.addEventListener("click", () => moveCatalogVariant(button.dataset.moveVariant, Number(button.dataset.moveDirection), button)));
  document.querySelectorAll("[data-restore-product]").forEach((button) => button.addEventListener("click", () => openCatalogRestoreConfirmation(button.dataset.restoreProduct, button.dataset.restoreTitle, button.dataset.restoreContext)));
  document.querySelectorAll("[data-restore-variant]").forEach((button) => button.addEventListener("click", () => executeCatalogRestore("variant", button.dataset.restoreVariant, button, button.dataset.restoreContext)));
  document.querySelectorAll("[data-classify-test-product]").forEach((button) => button.addEventListener("click", () => openCatalogTestDataPreflight(button.dataset.classifyTestProduct, "classify")));
  document.querySelectorAll("[data-purge-test-product]").forEach((button) => button.addEventListener("click", () => openCatalogTestDataPreflight(button.dataset.purgeTestProduct, "purge")));
}

async function moveCatalogVariant(variantId, direction, button) {
  const product = state.operations.product;
  const variantIds = product.variants.map((variant) => variant.id);
  const currentIndex = variantIds.indexOf(variantId);
  const targetIndex = currentIndex + direction;
  if (currentIndex < 0 || targetIndex < 0 || targetIndex >= variantIds.length) return;
  [variantIds[currentIndex], variantIds[targetIndex]] = [variantIds[targetIndex], variantIds[currentIndex]];
  button.disabled = true;
  try {
    await api(`/api/catalog/products/${product.id}/variant-order`, {
      method: "PUT",
      body: JSON.stringify({ variant_ids: variantIds }),
    });
    await openOperationsProduct(product.id);
    showToast("Порядок вариантов сохранён");
  } catch (error) {
    showToast(error.message, true);
    if (button.isConnected) button.disabled = false;
  }
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

function rentalTariffById(variantId, tariffId) {
  return (state.operations.tariffs.get(variantId) || []).find((tariff) => tariff.id === tariffId) || null;
}

function tariffFormTypeFields(type, tariff = null) {
  const duration = type === "duration";
  const minutes = Number(tariff?.duration_minutes || 0);
  return `<div class="tariff-type-fields${duration ? "" : " hidden"}" data-duration-fields>
      <div class="field-row">
        <div class="field"><label for="tariff-duration-hours">Часы</label><input id="tariff-duration-hours" name="duration_hours" type="number" min="0" max="720" step="1" inputmode="numeric" value="${duration ? Math.floor(minutes / 60) : 0}"></div>
        <div class="field"><label for="tariff-duration-minutes">Минуты</label><input id="tariff-duration-minutes" name="duration_minutes_part" type="number" min="0" max="59" step="1" inputmode="numeric" value="${duration ? minutes % 60 : 0}"></div>
      </div>
    </div>
    <div class="tariff-type-fields${duration ? " hidden" : ""}" data-slot-fields>
      <div class="field-row tariff-time-row">
        <div class="field"><label for="tariff-slot-start">Начало</label><input id="tariff-slot-start" name="slot_start" type="time" data-tariff-required value="${escapeHtml(String(tariff?.slot_start || "09:00").slice(0, 5))}"></div>
        <div class="field"><label for="tariff-slot-end">Окончание</label><input id="tariff-slot-end" name="slot_end" type="time" data-tariff-required value="${escapeHtml(String(tariff?.slot_end || "14:00").slice(0, 5))}"></div>
      </div>
      <p class="muted small">Слот должен начинаться и заканчиваться в один календарный день.</p>
    </div>`;
}

function syncRentalTariffTypeFields(form, selectedType = null) {
  const type = selectedType || form.querySelector('[name="type"]:checked').value;
  [
    [form.querySelector("[data-duration-fields]"), type === "duration"],
    [form.querySelector("[data-slot-fields]"), type === "slot"],
  ].forEach(([container, active]) => {
    container.classList.toggle("hidden", !active);
    container.querySelectorAll("input, select, textarea").forEach((control) => {
      control.disabled = !active;
      control.required = active && control.hasAttribute("data-tariff-required");
    });
  });
}

function openRentalTariffEditor(variantId, tariffId = null) {
  const variant = state.operations.product.variants.find((item) => item.id === variantId);
  const tariff = tariffId ? rentalTariffById(variantId, tariffId) : null;
  if (!variant || (tariffId && !tariff)) return;
  const editing = Boolean(tariff);
  const type = tariff?.type || "duration";
  const recommendedDeposit = tariff?.deposit_amount ?? variant.current_recommended_deposit ?? "";
  const dialog = openCatalogActionDialog(`<form id="rental-tariff-form" class="tariff-form">
    <div class="catalog-dialog-head"><div><p class="eyebrow">${escapeHtml(visibleVariantTitle(variant.title, variant.sku))}</p><h2>${editing ? "Изменить тариф" : "Добавить тариф"}</h2></div><button class="drawer-close" data-action-cancel type="button" aria-label="Закрыть">×</button></div>
    ${editing ? '<p class="tariff-version-note">Изменение сохранит прежние условия и создаст новую версию тарифа.</p>' : ""}
    <div class="field"><label for="tariff-name">Название</label><input id="tariff-name" name="name" maxlength="120" value="${escapeHtml(tariff?.name || "")}" required autofocus></div>
    <fieldset class="tariff-type-options"><legend>Тип</legend>
      <label><input name="type" type="radio" value="duration" ${type === "duration" ? "checked" : ""}><span><strong>По длительности</strong><small>Период от фактического времени выдачи</small></span></label>
      <label><input name="type" type="radio" value="slot" ${type === "slot" ? "checked" : ""}><span><strong>Временной слот</strong><small>Фиксированные начало и окончание</small></span></label>
    </fieldset>
    <div data-tariff-type-container>${tariffFormTypeFields(type, tariff)}</div>
    <div class="field-row">
      <div class="field"><label for="tariff-price">Цена, ₽</label><input id="tariff-price" name="price" type="number" min="0" step="0.01" inputmode="decimal" value="${escapeHtml(tariff?.price ?? "")}" required></div>
      <div class="field"><label for="tariff-grace">Льготный период, мин</label><input id="tariff-grace" name="grace_minutes" type="number" min="0" max="10080" step="1" inputmode="numeric" value="${tariff?.grace_minutes ?? 30}" required></div>
    </div>
    <div class="field"><label for="tariff-deposit">Обеспечительный платёж, ₽ <span class="muted">(необязательно)</span></label><input id="tariff-deposit" name="deposit_amount" type="number" min="0" step="0.01" inputmode="decimal" value="${escapeHtml(recommendedDeposit)}"><small class="muted">Если оставить пустым, Core скопирует текущий рекомендуемый залог Variant в эту версию.</small></div>
    <label class="tariff-agreement"><input name="requires_agreement" type="checkbox" ${tariff?.requires_agreement ? "checked" : ""}><span><strong>Договор обязателен</strong><small>Без сформированного договора оборудование нельзя будет выдать.</small></span></label>
    <div class="field-row">
      <div class="field"><label for="tariff-valid-from">Действует с</label><input id="tariff-valid-from" name="valid_from" type="date" value="${escapeHtml(tariff?.valid_from || localToday())}" required></div>
      <div class="field"><label for="tariff-valid-to">Действует до <span class="muted">(необязательно)</span></label><input id="tariff-valid-to" name="valid_to" type="date" value="${escapeHtml(tariff?.valid_to || "")}"></div>
    </div>
    <div class="catalog-dialog-actions"><button class="button secondary" data-action-cancel type="button">Отмена</button><button class="button" type="submit">${editing ? "Сохранить новую версию" : "Создать тариф"}</button></div>
  </form>`);
  const form = dialog.querySelector("#rental-tariff-form");
  syncRentalTariffTypeFields(form, type);
  form.querySelectorAll('[name="type"]').forEach((input) => input.addEventListener("change", () => syncRentalTariffTypeFields(form)));
  dialog.querySelectorAll("[data-action-cancel]").forEach((button) => button.addEventListener("click", () => dialog.close()));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    const selectedType = String(data.get("type"));
    const durationMinutes = Number(data.get("duration_hours") || 0) * 60 + Number(data.get("duration_minutes_part") || 0);
    if (selectedType === "duration" && durationMinutes <= 0) {
      showToast("Укажите положительную длительность тарифа", true);
      return;
    }
    const payload = {
      name: String(data.get("name") || "").trim(),
      type: selectedType,
      price: String(data.get("price")),
      duration_minutes: selectedType === "duration" ? durationMinutes : null,
      slot_start: selectedType === "slot" ? String(data.get("slot_start")) : null,
      slot_end: selectedType === "slot" ? String(data.get("slot_end")) : null,
      grace_minutes: Number(data.get("grace_minutes")),
      deposit_amount: data.get("deposit_amount") === "" ? null : String(data.get("deposit_amount")),
      requires_agreement: data.get("requires_agreement") === "on",
      valid_from: String(data.get("valid_from")),
      valid_to: data.get("valid_to") === "" ? null : String(data.get("valid_to")),
    };
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      await api(editing ? `/api/rental/tariffs/${tariff.id}` : `/api/rental/variants/${variantId}/tariffs`, { method: editing ? "PUT" : "POST", body: JSON.stringify(payload) });
      dialog.close();
      await openOperationsProduct(state.operations.product.id);
      showToast(editing ? "Новая версия тарифа сохранена" : "Тариф создан");
    } catch (error) { showToast(error.message, true); button.disabled = false; }
  });
}

async function openRentalTariffHistory(tariffId) {
  try {
    const versions = await api(`/api/rental/tariffs/${tariffId}/history`);
    const rows = versions.map((tariff, index) => `<article class="tariff-history-row ${index === 0 ? "current" : ""}">
      <div><strong>v${tariff.version} · ${formatMoney(tariff.price)}</strong><span>${escapeHtml(tariffPeriod(tariff))}</span></div>
      <div><span>${escapeHtml(tariff.valid_from)}${tariff.valid_to ? ` — ${escapeHtml(tariff.valid_to)}` : ""}</span><span>${tariff.status === "superseded" ? "Предыдущая версия" : tariff.status === "archived" ? "Архив" : "Активная версия"}</span></div>
    </article>`).join("");
    const dialog = openCatalogActionDialog(`<div class="tariff-history-dialog">
      <div class="catalog-dialog-head"><div><p class="eyebrow">История условий</p><h2>${escapeHtml(versions[0]?.name || "Тариф")}</h2></div><button class="drawer-close" data-action-cancel type="button" aria-label="Закрыть">×</button></div>
      <div class="tariff-history-list">${rows}</div>
      <div class="catalog-dialog-actions"><button class="button secondary" data-action-cancel type="button">Закрыть</button></div>
    </div>`);
    dialog.querySelectorAll("[data-action-cancel]").forEach((button) => button.addEventListener("click", () => dialog.close()));
  } catch (error) { showToast(error.message, true); }
}

async function archiveRentalTariff(tariffId) {
  if (!window.confirm("Архивировать тариф? Он перестанет быть доступен для новых аренд, а история сохранится.")) return;
  try {
    await api(`/api/rental/tariffs/${tariffId}/archive`, { method: "POST" });
    await openOperationsProduct(state.operations.product.id);
    showToast("Тариф архивирован");
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
  const seals = asset.seals || [];
  const components = (asset.components || []).filter((component) => component.is_active);
  const sealRows = seals.length ? seals.map((seal) => `
    <article class="session-row asset-seal-row">
      <span><strong>${escapeHtml(seal.location)} — ${escapeHtml(seal.seal_number)}</strong><br><span class="muted small">${escapeHtml(sealStatusLabel(seal.status))} · установлена ${formatDate(seal.installed_at)}${seal.removed_at ? ` · снята ${formatDate(seal.removed_at)}` : ""}</span>${seal.note ? `<br><span class="muted small">${escapeHtml(seal.note)}</span>` : ""}</span>
      ${seal.status === "active" ? `<span class="inline-actions"><button class="button ghost compact" data-seal-status="removed" data-seal-id="${seal.id}">Снять</button><button class="button ghost compact" data-seal-status="broken" data-seal-id="${seal.id}">Отметить повреждение</button></span>` : ""}
    </article>`).join("") : '<div class="empty">Контрольные пломбы не установлены</div>';
  const componentRows = components.map((component, index) => `
    <article class="card asset-component-row" data-component-id="${component.id}">
      <form data-component-form="${component.id}">
        <div class="field-row"><div class="field"><label>Название</label><input name="name" maxlength="200" value="${escapeHtml(component.name)}" required></div><div class="field"><label>Примечание</label><input name="note" maxlength="2000" value="${escapeHtml(component.note || "")}"></div></div>
        <div class="inline-actions"><button class="button secondary compact" type="submit">Сохранить</button><button class="button ghost compact" type="button" data-component-move="up" data-component-id="${component.id}" ${index === 0 ? "disabled" : ""}>↑</button><button class="button ghost compact" type="button" data-component-move="down" data-component-id="${component.id}" ${index === components.length - 1 ? "disabled" : ""}>↓</button><button class="button ghost compact" type="button" data-component-remove="${component.id}">Убрать</button></div>
      </form>
    </article>`).join("");
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
    <div class="section-heading"><h2>Идентификация</h2><span class="muted small">Необязательно для выдачи</span></div>
    <form class="card" id="asset-identification-form">
      <div class="field"><label>Серийный номер</label><input name="serial_number" maxlength="120" value="${escapeHtml(asset.serial_number || "")}" placeholder="Оставьте пустым, если номера нет"></div>
      <button class="button secondary" type="submit">Сохранить серийный номер</button>
    </form>
    <div class="section-heading"><h2>Контрольные пломбы</h2></div>
    <div class="session-list">${sealRows}</div>
    <form class="card" id="asset-seal-form">
      <h3>Добавить пломбу</h3>
      <div class="field-row"><div class="field"><label>Место установки</label><input name="location" maxlength="160" required placeholder="Например, Корпус"></div><div class="field"><label>Номер пломбы</label><input name="seal_number" maxlength="120" required></div></div>
      <div class="field"><label>Комментарий</label><textarea name="note" maxlength="2000"></textarea></div>
      <button class="button secondary full" type="submit">Добавить пломбу</button>
    </form>
    ${components.length ? `<div class="section-heading"><h2>Комплектность</h2><span class="muted small">Не отдельные товары или складские остатки</span></div><div class="asset-component-list">${componentRows}</div>` : ""}
    <details class="card asset-component-add"><summary>+ Добавить элемент комплектности</summary>
      <form id="asset-component-form">
        <div class="field-row"><div class="field"><label>Название</label><input name="name" maxlength="200" required placeholder="Например, Шланг"></div><div class="field"><label>Примечание</label><input name="note" maxlength="2000" placeholder="Необязательно"></div></div>
        <button class="button secondary full" type="submit">Добавить элемент</button>
      </form>
    </details>
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
  document.querySelector("#asset-identification-form").addEventListener("submit", updateAssetIdentification);
  document.querySelector("#asset-seal-form").addEventListener("submit", addAssetSeal);
  document.querySelectorAll("[data-seal-status]").forEach((button) => button.addEventListener("click", updateAssetSealStatus));
  document.querySelector("#asset-component-form").addEventListener("submit", addAssetComponent);
  document.querySelectorAll("[data-component-form]").forEach((form) => form.addEventListener("submit", updateAssetComponent));
  document.querySelectorAll("[data-component-remove]").forEach((button) => button.addEventListener("click", deactivateAssetComponent));
  document.querySelectorAll("[data-component-move]").forEach((button) => button.addEventListener("click", moveAssetComponent));
  document.querySelectorAll("[data-passport-order]").forEach((button) => button.addEventListener("click", () => openRentalReturnOrder(button.dataset.passportOrder)));
  document.querySelectorAll("[data-passport-customer]").forEach((button) => button.addEventListener("click", () => selectRentalCustomer(button.dataset.passportCustomer)));
  document.querySelector("#asset-maintenance-form").addEventListener("submit", addAssetMaintenance);
  document.querySelector("#asset-damage-form").addEventListener("submit", addAssetDamage);
  document.querySelector("#asset-photo-form").addEventListener("submit", addAssetConditionPhoto);
}

function sealStatusLabel(status) {
  return { active: "Активна", removed: "Снята", broken: "Повреждена" }[status] || status;
}

async function updateAssetIdentification(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  try {
    state.operations.asset = await api(`/api/operations/rental/assets/${state.operations.asset.id}/identification`, {
      method: "PATCH",
      body: JSON.stringify({ serial_number: nullableText(data.get("serial_number")) }),
    });
    renderOperationsAsset();
    showToast("Серийный номер сохранён.");
  } catch (error) { showToast(error.message, true); }
}

async function addAssetSeal(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  try {
    await api(`/api/operations/rental/assets/${state.operations.asset.id}/seals`, {
      method: "POST",
      body: JSON.stringify({
        location: String(data.get("location") || "").trim(),
        seal_number: String(data.get("seal_number") || "").trim(),
        note: nullableText(data.get("note")),
      }),
    });
    await openOperationsAsset(state.operations.asset.id);
    showToast("Пломба добавлена. Предыдущая пломба в этом месте сохранена в истории.");
  } catch (error) { showToast(error.message, true); }
}

async function updateAssetSealStatus(event) {
  const button = event.currentTarget;
  const status = button.dataset.sealStatus;
  const note = window.prompt(status === "broken" ? "Опишите повреждение пломбы:" : "Комментарий к снятию пломбы (необязательно):", "");
  if (note === null) return;
  if (status === "broken" && !note.trim()) {
    showToast("Для повреждённой пломбы нужен комментарий.", true);
    return;
  }
  try {
    await api(`/api/operations/rental/assets/${state.operations.asset.id}/seals/${button.dataset.sealId}`, {
      method: "PATCH",
      body: JSON.stringify({ status, note: nullableText(note) }),
    });
    await openOperationsAsset(state.operations.asset.id);
    showToast(status === "broken" ? "Повреждение пломбы сохранено." : "Пломба снята.");
  } catch (error) { showToast(error.message, true); }
}

async function addAssetComponent(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  try {
    await api(`/api/operations/rental/assets/${state.operations.asset.id}/components`, {
      method: "POST",
      body: JSON.stringify({
        name: String(data.get("name") || "").trim(),
        note: nullableText(data.get("note")),
      }),
    });
    await openOperationsAsset(state.operations.asset.id);
    showToast("Элемент комплектности добавлен.");
  } catch (error) { showToast(error.message, true); }
}

async function updateAssetComponent(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  const componentId = event.currentTarget.dataset.componentForm;
  try {
    await api(`/api/operations/rental/assets/${state.operations.asset.id}/components/${componentId}`, {
      method: "PATCH",
      body: JSON.stringify({
        name: String(data.get("name") || "").trim(),
        note: nullableText(data.get("note")),
      }),
    });
    await openOperationsAsset(state.operations.asset.id);
    showToast("Комплектность обновлена. Старые выдачи не изменены.");
  } catch (error) { showToast(error.message, true); }
}

async function deactivateAssetComponent(event) {
  const componentId = event.currentTarget.dataset.componentRemove;
  if (!window.confirm("Убрать элемент из будущих выдач? Старые снимки сохранятся.")) return;
  try {
    await api(`/api/operations/rental/assets/${state.operations.asset.id}/components/${componentId}`, { method: "DELETE" });
    await openOperationsAsset(state.operations.asset.id);
    showToast("Элемент убран из будущих выдач.");
  } catch (error) { showToast(error.message, true); }
}

async function moveAssetComponent(event) {
  const active = (state.operations.asset.components || []).filter((component) => component.is_active);
  const index = active.findIndex((component) => component.id === event.currentTarget.dataset.componentId);
  const target = event.currentTarget.dataset.componentMove === "up" ? index - 1 : index + 1;
  if (index < 0 || target < 0 || target >= active.length) return;
  [active[index], active[target]] = [active[target], active[index]];
  try {
    await api(`/api/operations/rental/assets/${state.operations.asset.id}/components/order`, {
      method: "PUT",
      body: JSON.stringify({ component_ids: active.map((component) => component.id) }),
    });
    await openOperationsAsset(state.operations.asset.id);
  } catch (error) { showToast(error.message, true); }
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
    const [active, ready, closed, cancelled] = await Promise.all([
      api("/rental/orders?order_status=issued"),
      api("/rental/orders?order_status=ready"),
      api("/rental/orders?order_status=closed"),
      api("/rental/orders?order_status=cancelled"),
    ]);
    state.rental.activeOrders = active;
    state.rental.readyOrders = ready;
    state.rental.historyOrders = [...closed, ...cancelled].sort((a, b) => String(b.closed_at || "").localeCompare(String(a.closed_at || "")));
    state.rental.activeOrders.sort((left, right) => rentalOrderUrgency(right) - rentalOrderUrgency(left));
    state.rental.workspaceView = state.rental.workspaceView || "current";
    renderRentalHub();
  } catch (error) { showToast(error.message, true); }
}

function renderRentalHub() {
  if (state.rental.timerHandle) window.clearInterval(state.rental.timerHandle);
  const view = state.rental.workspaceView;
  const content = view === "ready"
    ? renderRentalWorkspaceSection("Ожидают выдачи", state.rental.readyOrders, renderReadyRentalCard)
    : view === "history"
      ? renderRentalWorkspaceSection("История", state.rental.historyOrders, renderHistoryRentalCard)
      : `${renderRentalWorkspaceSection("Активные", state.rental.activeOrders, renderActiveRentalCard)}${renderRentalWorkspaceSection("Ожидают выдачи", state.rental.readyOrders, renderReadyRentalCard)}`;
  root.innerHTML = `<div class="shell">
    ${topbar(true)}
    <div class="workspace-title"><div><p class="eyebrow">Операции</p><h1>Аренда</h1></div><button class="button" id="hub-new">＋ Новая аренда</button></div>
    <div class="rental-workspace-tabs" role="tablist">
      <button class="${view === "current" ? "active" : ""}" data-rental-view="current">Активные <span>${state.rental.activeOrders.length}</span></button>
      <button class="${view === "ready" ? "active" : ""}" data-rental-view="ready">Ожидают выдачи <span>${state.rental.readyOrders.length}</span></button>
      <button class="${view === "history" ? "active" : ""}" data-rental-view="history">История</button>
    </div>
    <div class="rental-workspace-list">${content}</div>
  </div>`;
  bindTopbar();
  document.querySelector("#hub-new").addEventListener("click", openRentalHome);
  document.querySelectorAll("[data-rental-view]").forEach((button) => button.addEventListener("click", () => {
    state.rental.workspaceView = button.dataset.rentalView;
    renderRentalHub();
  }));
  document.querySelectorAll("[data-open-ready-rental]").forEach((button) => button.addEventListener("click", () => openRentalDraft(button.dataset.openReadyRental)));
  document.querySelectorAll("[data-issue-rental]").forEach((button) => button.addEventListener("click", () => openRentalIssueConfirmation(button.dataset.issueRental)));
  document.querySelectorAll("[data-open-active-rental]").forEach((button) => button.addEventListener("click", () => openRentalOperationalOrder(button.dataset.openActiveRental)));
  document.querySelectorAll("[data-return-rental]").forEach((button) => button.addEventListener("click", () => openRentalReturnOrder(button.dataset.returnRental)));
  document.querySelectorAll("[data-return-item]").forEach((button) => button.addEventListener("click", () => openRentalReturnOrder(button.dataset.returnOrder, button.dataset.returnItem)));
  document.querySelectorAll("[data-open-history-rental]").forEach((button) => button.addEventListener("click", () => openRentalReturnOrder(button.dataset.openHistoryRental)));
  updateRentalTimers();
  state.rental.timerHandle = window.setInterval(updateRentalTimers, 1000);
}

function renderRentalWorkspaceSection(title, orders, renderer) {
  const cards = orders.length ? orders.map((order) => renderer(order)).join("") : `<div class="empty">${title === "Активные" ? "Активных аренд нет" : title === "Ожидают выдачи" ? "Нет заказов, готовых к выдаче" : "История пока пуста"}</div>`;
  return `<section class="rental-workspace-section"><div class="section-heading"><h2>${title}</h2><span class="chip">${orders.length}</span></div><div class="rental-order-grid">${cards}</div></section>`;
}

function renderReadyRentalCard(order) {
  const items = order.items.map((item) => `<div class="workspace-rental-item"><span><strong>${escapeHtml(item.title_snapshot)}</strong><small>${escapeHtml(item.asset_number_snapshot)}</small></span><span>${escapeHtml(item.tariff_snapshot?.tariff_name || "Тариф")} · ${formatMoney(Number(item.agreed_price))}</span></div>`).join("");
  return `<article class="rental-operation-card ready">
    <div class="section-heading"><span class="chip good">Готово к выдаче</span><span class="muted small">${escapeHtml(order.order_number)}</span></div>
    <h3>${escapeHtml(order.customer_name_snapshot)}</h3><p class="muted small">${order.items.length} поз.</p>
    <div class="workspace-rental-items">${items}</div>
    <div class="rental-order-totals"><span>Аренда <strong>${formatMoney(Number(order.rental_total))}</strong></span><span>Залог <strong>${formatMoney(Number(order.recommended_deposit_total))}</strong></span></div>
    ${order.requires_agreement ? '<p class="agreement-warning">Договор обязателен</p>' : ""}
    <div class="rental-card-actions"><button class="button secondary" data-open-ready-rental="${order.id}">Открыть</button><button class="button" data-issue-rental="${order.id}">Выдать</button></div>
  </article>`;
}

function renderActiveRentalCard(order, showOpen = true) {
  const issuedItems = order.items.filter((item) => item.status === "issued");
  const overdueCount = issuedItems.filter((item) => item.due_at && Date.now() >= new Date(item.due_at).getTime()).length;
  const items = order.items.map((item) => {
    if (item.status !== "issued") return `<div class="workspace-rental-item completed"><span><strong>${escapeHtml(item.title_snapshot)}</strong><small>${escapeHtml(item.asset_number_snapshot)}</small></span><span>Возвращено</span></div>`;
    const grace = Number(item.tariff_snapshot?.grace_minutes || 0);
    const dueText = item.due_at
      ? `<small>${escapeHtml(item.asset_number_snapshot)} · вернуть до ${formatDate(item.due_at)}</small>`
      : `<small>${escapeHtml(item.asset_number_snapshot)} · срок не указан</small>`;
    const timer = item.due_at
      ? `<strong class="rental-timer" data-rental-due="${escapeHtml(item.due_at)}" data-rental-grace="${grace}"></strong>`
      : '<strong class="rental-timer muted">—</strong>';
    return `<div class="workspace-rental-item active"><span><strong>${escapeHtml(item.title_snapshot)}</strong>${dueText}${grace ? `<small>Льготный период: ${grace} мин</small>` : ""}</span>${timer}<button class="button ghost compact rental-item-return-action" data-return-order="${order.id}" data-return-item="${item.id}">Оформить возврат</button></div>`;
  }).join("");
  return `<article class="rental-operation-card active-rental">
    <div class="section-heading"><span class="chip">Активна</span><span class="muted small">${escapeHtml(order.order_number)}</span></div>
    <h3>${escapeHtml(order.customer_name_snapshot)}</h3><p class="muted small">${order.items.length} поз.${overdueCount ? ` · ${overdueCount} просрочено` : ""}</p>
    <div class="workspace-rental-items">${items}</div>
    <div class="rental-card-actions">${showOpen ? `<button class="button secondary" data-open-active-rental="${order.id}">Открыть</button>` : ""}<button class="button" data-return-rental="${order.id}">Принять возврат</button></div>
  </article>`;
}

function renderHistoryRentalCard(order) {
  const returned = order.items.filter((item) => item.returned_at);
  const items = returned.map((item) => `${escapeHtml(item.title_snapshot)} · ${item.returned_at ? formatDate(item.returned_at) : "—"}`).join("<br>");
  return `<article class="rental-operation-card history"><div class="section-heading"><span class="chip">${order.status === "closed" ? "Завершена" : "Отменена"}</span><span class="muted small">${escapeHtml(order.order_number)}</span></div><h3>${escapeHtml(order.customer_name_snapshot)}</h3><p class="muted small">${items || `${order.items.length} поз. · без выдачи`}</p>${order.status === "closed" ? `<div class="rental-order-totals"><span>Аренда <strong>${formatMoney(Number(order.rental_total))}</strong></span><span>Просрочка <strong>${formatMoney(Number(order.overdue_charge_total || 0))}</strong></span><span>Повреждения <strong>${formatMoney(Number(order.damage_charge_total || 0))}</strong></span></div><button class="button secondary" data-open-history-rental="${order.id}">Открыть расчёт</button>` : ""}</article>`;
}

function updateRentalTimers() {
  const now = Date.now();
  document.querySelectorAll("[data-rental-due]").forEach((element) => {
    const due = new Date(element.dataset.rentalDue).getTime();
    if (!Number.isFinite(due)) return;
    const overdue = now >= due;
    const graceMs = Number(element.dataset.rentalGrace || 0) * 60000;
    const overdueMs = now - due;
    element.textContent = overdue
      ? overdueMs <= graceMs
        ? `Просрочка ${formatHumanDuration(overdueMs)} · льготный период`
        : `Просрочка ${formatHumanDuration(overdueMs)}`
      : `Осталось ${formatHumanDuration(due - now)}`;
    element.classList.toggle("overdue", overdue);
  });
}

function formatHumanDuration(milliseconds) {
  const totalMinutes = Math.max(0, Math.ceil(milliseconds / 60000));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours ? `${hours} ч ${minutes} мин` : `${minutes} мин`;
}

function rentalOrderUrgency(order) {
  const ranks = { overdue_chargeable: 4, overdue_grace: 3, on_time: 1 };
  return Math.max(0, ...order.items.filter((item) => item.status === "issued").map((item) => {
    if (item.current_overdue_state === "on_time" && item.due_at && new Date(item.due_at).getTime() - Date.now() <= 30 * 60000) return 2;
    return ranks[item.current_overdue_state] || 0;
  }));
}

function formatTimerDuration(milliseconds) {
  const total = Math.floor(milliseconds / 1000);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return [hours, minutes, seconds].map((value) => String(value).padStart(2, "0")).join(":");
}

async function openRentalHome() {
  try {
    logicalParent = () => openRentalHub();
    const [drafts, ready] = await Promise.all([
      api("/rental/orders?order_status=draft"),
      api("/rental/orders?order_status=ready"),
    ]);
    state.rental = {
      customers: [],
      customer: null,
      drafts: [...ready, ...drafts],
      order: null,
      assets: [],
      tariffs: [],
      selectedAssetId: null,
      selectedTariffId: null,
      editingItemId: null,
      itemEditorOpen: false,
      returnAssets: new Map(),
      readyOrders: [],
      activeOrders: [],
      historyOrders: [],
      workspaceView: "current",
      issuePreview: null,
      agreement: null,
      agreementPreview: null,
      returnPreview: null,
      returnItemIds: [],
      timerHandle: null,
    };
    renderRentalHome();
  } catch (error) { showToast(error.message, true); }
}

function renderRentalHome() {
  const draftRows = state.rental.drafts.length
    ? state.rental.drafts.map((order) => `
      <button class="session-row" data-rental-draft="${order.id}">
        <span><strong>${escapeHtml(order.order_number)}</strong>${order.status === "ready" ? '<span class="chip good inline-chip">READY</span>' : ''}<br><span class="muted small">${escapeHtml(order.customer_name_snapshot)}${order.status === "ready" ? ` · вернуть ${formatShortDate(order.planned_return_at)}` : " · не завершён"}</span></span>
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
  root.innerHTML = `<div class="shell">
    ${topbar(true)}
    <p class="eyebrow">Новая аренда · шаг 1</p>
    <section class="card customer-card">
      <span class="chip good">${escapeHtml(customer.customer_number)}</span>
      <h1>${escapeHtml(customer.full_name)}</h1>
      <p><a href="tel:${escapeHtml(customer.phone)}">${escapeHtml(customer.phone)}</a>${customer.email ? ` · ${escapeHtml(customer.email)}` : ""}</p>
      ${customer.note ? `<p class="muted">${escapeHtml(customer.note)}</p>` : ""}
      <button class="button full" id="rental-create-draft">Продолжить к оборудованию</button>
    </section>
    ${history ? renderCustomerRentalHistory(history) : ""}
  </div>`;
  bindTopbar();
  document.querySelector("#rental-create-draft").addEventListener("click", createRentalDraft);
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

async function createRentalDraft() {
  try {
    state.rental.order = await api("/rental/orders", {
      method: "POST",
      body: JSON.stringify({
        customer_id: state.rental.customer.id,
      }),
    });
    recordRoute("order", { orderId: state.rental.order.id });
    await loadDefaultRentalAssets();
    state.rental.itemEditorOpen = true;
    state.rental.editingItemId = null;
    renderRentalDraft();
  } catch (error) { showToast(error.message, true); }
}

async function openRentalOrder(orderId) {
  const order = await api(`/rental/orders/${orderId}`);
  if (["draft", "ready"].includes(order.status)) {
    await openRentalDraft(orderId, order);
  } else if (order.status === "issued") {
    await openRentalOperationalOrder(orderId, order);
  } else {
    await openRentalReturnOrder(orderId);
  }
}

async function openRentalDraft(orderId, loadedOrder = null) {
  try {
    recordRoute("order", { orderId });
    logicalParent = () => selectRentalCustomer(state.rental.order?.customer_id);
    state.rental.order = loadedOrder || await api(`/rental/orders/${orderId}`);
    state.rental.agreement = state.rental.order.status === "ready"
      ? await api(`/rental/orders/${orderId}/agreement`)
      : null;
    state.rental.customer = await api(`/api/customers/${state.rental.order.customer_id}`);
    await loadDefaultRentalAssets();
    state.rental.itemEditorOpen = state.rental.order.items.length === 0 && !state.rental.agreement;
    state.rental.editingItemId = null;
    state.rental.selectedAssetId = null;
    state.rental.selectedTariffId = null;
    state.rental.depositEditing = false;
    state.rental.tariffs = [];
    renderRentalDraft();
  } catch (error) { showToast(error.message, true); }
}

function renderRentalDraft() {
  const order = state.rental.order;
  const agreementTermsLocked = Boolean(state.rental.agreement);
  const editingItem = order.items.find((item) => item.id === state.rental.editingItemId) || null;
  const selectedAsset = state.rental.assets.find((asset) => asset.id === state.rental.selectedAssetId);
  const selectedTariff = state.rental.tariffs.find((tariff) => tariff.id === state.rental.selectedTariffId);
  root.innerHTML = `<div class="shell">
    ${topbar(true)}
    <p class="eyebrow">${order.status === "ready" ? "READY" : "Черновик"} · ${escapeHtml(order.order_number)}</p>
    <h1>Новая аренда</h1>
    <section class="card"><span class="muted small">1. Клиент</span><h2>${escapeHtml(order.customer_name_snapshot)}</h2><p>${escapeHtml(order.customer_phone_snapshot)}</p></section>
    <section class="card rental-items-card">
      <div class="section-heading"><div><span class="muted small">2. Позиции аренды</span><h2>Оборудование</h2></div><span class="chip ${order.status === "ready" ? "good" : "warn"}">${order.items.length} поз.</span></div>
      <div class="rental-item-list">${renderRentalDraftItems(order, agreementTermsLocked)}</div>
      ${agreementTermsLocked ? '<div class="notice"><strong>Условия зафиксированы</strong><br>После создания договора позиции, тарифы, сроки и суммы нельзя изменить.</div>' : `<button class="button full" id="rental-add-item" ${state.rental.itemEditorOpen && !editingItem ? "disabled" : ""}>＋ Добавить оборудование</button>`}
    </section>
    ${state.rental.itemEditorOpen ? `<section class="card rental-item-editor">
      <div class="section-heading"><div><span class="muted small">${editingItem ? "Редактирование позиции" : "Новая позиция"}</span>
      <h2>${selectedAsset ? `${escapeHtml(selectedAsset.product_title)} · ${escapeHtml(selectedAsset.variant_title)}` : "Выберите предмет аренды"}</h2>
      </div>${order.items.length ? '<button class="button secondary compact" type="button" id="rental-cancel-item">Закрыть</button>' : ""}</div>
      <p class="muted small">Отсканируйте RENT-номер или найдите по товару, варианту либо SKU.</p>
      <form class="search-row" id="rental-asset-search-form">
        <input name="query" autocomplete="off" placeholder="RENT-000001 или название">
        <button class="button" type="submit">Найти</button>
      </form>
      <div id="rental-asset-results">${renderRentalAssetResults()}</div>
    </section>
    ${selectedAsset ? `<section class="card"><span class="muted small">3. Тариф позиции</span><h2>Выберите тариф</h2><div class="tariff-choice-list">${renderRentalTariffChoices()}</div></section>` : ""}
    ${selectedTariff ? renderRentalTimingForm(selectedTariff, editingItem) : ""}` : ""}
    ${order.items.length ? renderRentalReadySummary(order) : ""}
  </div>`;
  bindTopbar();
  document.querySelector("#rental-add-item")?.addEventListener("click", beginRentalItemAdd);
  document.querySelector("#rental-cancel-item")?.addEventListener("click", closeRentalItemEditor);
  document.querySelector("#rental-asset-search-form")?.addEventListener("submit", searchRentalAssets);
  bindRentalAssetResults();
  document.querySelectorAll("[data-edit-rental-item]").forEach((button) => button.addEventListener("click", () => beginRentalItemEdit(button.dataset.editRentalItem)));
  document.querySelectorAll("[data-delete-rental-item]").forEach((button) => button.addEventListener("click", () => deleteRentalItem(button.dataset.deleteRentalItem)));
  document.querySelectorAll("[data-rental-tariff]").forEach((input) => {
    input.addEventListener("change", () => {
      try {
        state.rental.selectedTariffId = input.value;
        renderRentalDraft();
      } catch (error) {
        showToast(error.message || "Не удалось открыть условия тарифа", true);
      }
    });
  });
  document.querySelector("#rental-tariff-form")?.addEventListener("submit", saveRentalTariffDraft);
  document.querySelector("#rental-deposit-form")?.addEventListener("submit", saveRentalOrderDeposit);
  document.querySelector("#rental-edit-deposit")?.addEventListener("click", () => {
    state.rental.depositEditing = true;
    renderRentalDraft();
  });
  document.querySelector("#rental-ready-issue")?.addEventListener("click", () => openRentalIssueConfirmation(order.id));
  document.querySelector("#rental-generate-agreement")?.addEventListener("click", () => openRentalAgreementPreview(order.id));
  document.querySelector("[data-open-agreement]")?.addEventListener("click", () => openAuthenticatedFile(`/rental/agreements/${state.rental.agreement.id}/pdf`));
  document.querySelector("[data-print-agreement]")?.addEventListener("click", () => openAuthenticatedFile(`/rental/agreements/${state.rental.agreement.id}/pdf`, true));
  document.querySelectorAll('[name="timing_source"]').forEach((input) => {
    input.addEventListener("change", toggleRentalTimingFields);
  });
  toggleRentalTimingFields();
}

function renderRentalDraftItems(order, termsLocked = false) {
  if (!order.items.length) return '<div class="empty">Добавьте хотя бы одно оборудование</div>';
  return order.items.map((item) => {
    const snapshot = item.tariff_snapshot;
    return `<article class="rental-item-card">
      <div><strong>${escapeHtml(item.title_snapshot)} · ${escapeHtml(item.asset_number_snapshot)}</strong>
      <p class="muted small">${snapshot ? `${escapeHtml(snapshot.tariff_name)} · ${renderRentalItemTiming(item)}` : "Условия не заполнены"}</p></div>
      <div class="rental-item-price"><strong>${formatMoney(Number(item.agreed_price))}</strong><span class="muted small">залог ${formatMoney(Number(item.recommended_deposit || 0))}</span></div>
      ${termsLocked ? "" : `<div class="rental-item-actions"><button class="button secondary compact" data-edit-rental-item="${item.id}">Изменить</button><button class="button danger compact" data-delete-rental-item="${item.id}">Удалить</button></div>`}
    </article>`;
  }).join("");
}

function renderRentalItemTiming(item) {
  const snapshot = item.tariff_snapshot;
  if (snapshot?.tariff_type === "duration" && item.timing_source === "tariff") {
    if (item.issued_at && item.due_at) return `выдано ${formatDate(item.issued_at)} · вернуть ${formatDate(item.due_at)}`;
    return `дата выдачи ${formatRentalDate(item.planned_rental_date)} · срок начнётся при выдаче`;
  }
  return `${formatShortDate(item.planned_start_at)} — ${formatShortDate(item.due_at)}`;
}

function beginRentalItemAdd() {
  state.rental.itemEditorOpen = true;
  state.rental.editingItemId = null;
  state.rental.selectedAssetId = null;
  state.rental.selectedTariffId = null;
  state.rental.tariffs = [];
  renderRentalDraft();
}

async function beginRentalItemEdit(itemId) {
  const item = state.rental.order.items.find((candidate) => candidate.id === itemId);
  if (!item) return;
  state.rental.itemEditorOpen = true;
  state.rental.editingItemId = item.id;
  state.rental.selectedAssetId = item.rental_asset_id;
  state.rental.selectedTariffId = item.rental_tariff_id;
  await loadRentalTariffsForAsset(item.rental_asset_id);
  renderRentalDraft();
}

function closeRentalItemEditor() {
  state.rental.itemEditorOpen = false;
  state.rental.editingItemId = null;
  state.rental.selectedAssetId = null;
  state.rental.selectedTariffId = null;
  state.rental.tariffs = [];
  renderRentalDraft();
}

function renderRentalTariffChoices() {
  if (!state.rental.tariffs.length) return '<div class="empty">Для этого Variant нет действующих тарифов</div>';
  return state.rental.tariffs.map((tariff) => `<label class="tariff-choice">
    <input type="radio" name="tariff_choice" value="${tariff.id}" data-rental-tariff ${tariff.id === state.rental.selectedTariffId ? "checked" : ""}>
    <span><strong>${escapeHtml(tariff.name)}</strong><span>${escapeHtml(tariff.tariff_terms).replaceAll("\n", "<br>")}</span><small>${tariff.requires_agreement ? "Договор обязателен" : "Договор не обязателен"}</small></span>
  </label>`).join("");
}

function renderRentalTimingForm(tariff, item = null) {
  const start = item?.planned_start_at ? new Date(item.planned_start_at) : new Date();
  const rentalDate = item?.planned_rental_date || localToday();
  const manual = item?.timing_source === "manual";
  const tariffFields = tariff.type === "slot"
    ? `<div class="field" data-tariff-timing><label>Дата аренды</label><input name="rental_date" type="date" value="${rentalDate}" required></div>
       <p class="muted small" data-tariff-timing>Выдача ${escapeHtml(tariff.slot_start.slice(0, 5))} · вернуть до ${escapeHtml(tariff.slot_end.slice(0, 5))}. Точное время рассчитает Core.</p>`
    : `<div class="field" data-tariff-timing><label>Дата выдачи</label><input name="rental_date" type="date" value="${rentalDate}" required></div>
       <p class="muted small" data-tariff-timing><strong>${escapeHtml(tariffPeriod(tariff))} · ${formatMoney(Number(tariff.price))}</strong><br>Срок начнётся в момент фактической выдачи.</p>`;
  return `<form class="card" id="rental-tariff-form">
    <span class="muted small">4. Дата / начало</span>
    <h2>${escapeHtml(tariff.name)}</h2>
    <label class="check"><input type="radio" name="timing_source" value="tariff" ${manual ? "" : "checked"}> По тарифу</label>
    ${tariffFields}
    <label class="check"><input type="radio" name="timing_source" value="manual" ${manual ? "checked" : ""}> Другой срок</label>
    <div class="field-row" data-manual-timing>
      <div class="field"><label>Начало</label><input name="manual_start_at" type="datetime-local" value="${toLocalInput(start)}"></div>
      <div class="field"><label>Возврат</label><input name="manual_return_at" type="datetime-local" value="${toLocalInput(item?.due_at ? new Date(item.due_at) : new Date(start.getTime() + 60 * 60 * 1000))}"></div>
    </div>
    <details><summary>Согласованная цена</summary><div class="field"><label>Цена, ₽ <span class="muted">(если отличается от тарифа)</span></label><input name="agreed_price" type="number" min="0" step="0.01" inputmode="decimal" placeholder="${escapeHtml(tariff.price)}"></div></details>
    <button class="button full" type="submit">${item ? "Сохранить позицию" : "Добавить позицию"}</button>
  </form>`;
}

function toggleRentalTimingFields() {
  const form = document.querySelector("#rental-tariff-form");
  if (!form) return;
  const manual = form.querySelector('[name="timing_source"]:checked')?.value === "manual";
  form.querySelectorAll("[data-tariff-timing]").forEach((element) => element.classList.toggle("hidden", manual));
  form.querySelector("[data-manual-timing]")?.classList.toggle("hidden", !manual);
}

function renderRentalReadySummary(order) {
  const agreement = state.rental.agreement;
  const depositReceived = order.deposit_received ?? order.deposit_amount ?? null;
  const depositConfirmed = order.deposit_received_confirmed === true
    || (order.deposit_received_confirmed === undefined && depositReceived !== null);
  const suggestedDeposit = depositConfirmed
    ? depositReceived
    : order.required_deposit_total ?? order.recommended_deposit_total;
  const depositControl = depositConfirmed && !state.rental.depositEditing
    ? `<div class="rental-deposit-confirmed"><span><span class="muted small">Фактически получено</span><strong>${formatMoney(Number(depositReceived))}</strong></span><button class="button secondary compact" type="button" id="rental-edit-deposit">Изменить</button></div>`
    : `<form id="rental-deposit-form" class="rental-deposit-form"><div class="field"><label>Фактически получено, ₽</label><input name="deposit_received" type="number" min="0" step="0.01" inputmode="decimal" value="${escapeHtml(suggestedDeposit)}" required><small class="muted">По умолчанию предложена рекомендованная сумма. Измените её, если фактически получена другая сумма.</small></div><button class="button secondary compact" type="submit">Сохранить залог</button></form>`;
  const readyAction = order.status !== "ready"
    ? '<p class="muted small rental-ready-message">Заполните условия всех позиций, чтобы подготовить аренду к выдаче.</p>'
    : order.requires_agreement && !agreement
      ? '<p class="muted small rental-ready-message">Аренда подготовлена. Перед выдачей сформируйте договор.</p><button class="button full" type="button" id="rental-generate-agreement">Сформировать договор</button><button class="button full" type="button" disabled>Выдать оборудование</button>'
      : agreement
        ? `<div class="rental-agreement-summary"><span><strong>Договор № ${escapeHtml(agreement.agreement_number)}</strong><small>Создан ${formatDate(agreement.generated_at)} · шаблон v${agreement.template_version}</small><details><summary>Технические данные</summary><code>SHA-256 ${escapeHtml(agreement.pdf_sha256)}</code><small>Hash проверяет точные байты PDF, но не является электронной подписью или внешней меткой времени.</small></details></span><div class="inline-actions"><button class="button ghost compact" type="button" data-open-agreement>Открыть</button><button class="button secondary compact" type="button" data-print-agreement>Печать</button></div></div><button class="button full rental-ready-issue" type="button" id="rental-ready-issue">Выдать оборудование</button>`
      : '<p class="muted small rental-ready-message">Аренда подготовлена и готова к выдаче.</p><button class="button full rental-ready-issue" type="button" id="rental-ready-issue">Выдать оборудование</button>';
  return `<section class="checkout-summary rental-ready-summary">
    <span class="chip ${order.status === "ready" ? "good" : "warn"}">${order.status === "ready" ? "READY" : "DRAFT"}</span><h2>Итоговые условия заказа</h2>
    <div><span class="muted small">Позиций</span><strong>${order.items.length}</strong></div>
    <div><span class="muted small">Стоимость</span><strong>${formatMoney(Number(order.rental_total))}</strong></div>
    <div><span class="muted small">Рекомендуемый залог</span><strong>${formatMoney(Number(order.recommended_deposit_total))}</strong></div>
    <div><span class="muted small">Договор</span><strong>${order.requires_agreement ? "требуется" : "не требуется"}</strong></div>
    ${depositControl}
    ${readyAction}
  </section>`;
}

async function saveRentalOrderDeposit(event) {
  event.preventDefault();
  const amount = String(new FormData(event.currentTarget).get("deposit_received"));
  try {
    state.rental.order = await api(`/rental/orders/${state.rental.order.id}/deposit`, {
      method: "PATCH",
      body: JSON.stringify({ deposit_received: amount }),
    });
    state.rental.depositEditing = false;
    renderRentalDraft();
    showToast("Фактически полученный залог сохранён");
  } catch (error) { showToast(error.message, true); }
}

async function openRentalAgreementPreview(orderId) {
  try {
    state.rental.agreementPreview = await api(`/rental/orders/${orderId}/agreement-preview`);
    logicalParent = () => openRentalDraft(orderId);
    const preview = state.rental.agreementPreview;
    root.innerHTML = `<div class="shell agreement-generation-shell">
      ${topbar(true)}
      <p class="eyebrow">Предпросмотр · шаблон v${preview.template_version}</p><h1>Договор проката</h1>
      <div class="notice"><strong>Это предпросмотр</strong><br>Номер и неизменяемый PDF появятся только после создания. Паспортные поля заполняются от руки.</div>
      <div class="agreement-preview-shell">${preview.rendered_html}</div>
      <div class="rental-card-actions"><button class="button secondary" type="button" id="cancel-agreement-generation">Назад</button><button class="button" type="button" id="create-rental-agreement">Создать договор</button></div>
    </div>`;
    bindTopbar();
    document.querySelector("#cancel-agreement-generation").addEventListener("click", () => openRentalDraft(orderId));
    document.querySelector("#create-rental-agreement").addEventListener("click", () => createRentalAgreement(orderId));
  } catch (error) { showToast(error.message, true); }
}

async function createRentalAgreement(orderId) {
  const button = document.querySelector("#create-rental-agreement");
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span> Создаём PDF';
  try {
    state.rental.agreement = await api(`/rental/orders/${orderId}/agreements`, { method: "POST" });
    showToast(`Договор ${state.rental.agreement.agreement_number} создан`);
    await openRentalDraft(orderId);
  } catch (error) {
    showToast(error.message, true);
    button.disabled = false;
    button.textContent = "Создать договор";
  }
}

async function saveRentalTariffDraft(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  const timingSource = String(data.get("timing_source"));
  const payload = {
    rental_asset_id: state.rental.selectedAssetId,
    tariff_id: state.rental.selectedTariffId,
    timing_source: timingSource,
    agreed_price: nullableText(data.get("agreed_price")),
  };
  if (timingSource === "manual") {
    payload.planned_start_at = new Date(String(data.get("manual_start_at"))).toISOString();
    payload.planned_return_at = new Date(String(data.get("manual_return_at"))).toISOString();
  } else {
    payload.rental_date = String(data.get("rental_date"));
  }
  try {
    const itemPath = state.rental.editingItemId ? `/tariff-items/${state.rental.editingItemId}` : "/tariff-items";
    state.rental.order = await api(`/rental/orders/${state.rental.order.id}${itemPath}`, {
      method: state.rental.editingItemId ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    state.rental.itemEditorOpen = false;
    state.rental.editingItemId = null;
    state.rental.selectedAssetId = null;
    state.rental.selectedTariffId = null;
    state.rental.tariffs = [];
    renderRentalDraft();
    showToast("Позиция сохранена");
  } catch (error) { showToast(error.message, true); }
}

async function deleteRentalItem(itemId) {
  if (!window.confirm("Удалить эту позицию из заказа?")) return;
  try {
    await api(`/rental/orders/${state.rental.order.id}/items/${itemId}`, { method: "DELETE" });
    state.rental.order = await api(`/rental/orders/${state.rental.order.id}`);
    if (!state.rental.order.items.length) state.rental.itemEditorOpen = true;
    renderRentalDraft();
    showToast("Позиция удалена");
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
    const selected = state.rental.selectedAssetId === asset.id;
    const available = asset.availability === "available";
    const usedBySibling = state.rental.order?.items.some((item) => item.rental_asset_id === asset.id && item.id !== state.rental.editingItemId);
    return `<article class="asset-result">
      <div><strong>${escapeHtml(asset.product_title)} · ${escapeHtml(asset.variant_title)}</strong>
      <div class="muted small">${escapeHtml(asset.asset_number)} · ${escapeHtml(conditionLabel(asset.condition))} · ${escapeHtml(availabilityLabel(asset.availability))}</div></div>
      <button class="button ${selected ? "secondary" : ""} compact" data-select-rental-asset="${asset.id}" ${available && !usedBySibling ? "" : "disabled"}>${usedBySibling ? "Уже в заказе" : selected ? "Выбрано" : "Выбрать"}</button>
    </article>`;
  }).join("");
}

function bindRentalAssetResults() {
  document.querySelectorAll("[data-select-rental-asset]").forEach((button) => {
    button.addEventListener("click", () => selectRentalAsset(button.dataset.selectRentalAsset));
  });
}

async function selectRentalAsset(assetId) {
  try {
    state.rental.selectedAssetId = assetId;
    state.rental.selectedTariffId = null;
    await loadRentalTariffsForAsset(assetId);
    renderRentalDraft();
  } catch (error) { showToast(error.message, true); }
}

async function loadRentalTariffsForAsset(assetId) {
  const asset = state.rental.assets.find((candidate) => candidate.id === assetId);
  state.rental.tariffs = asset
    ? await api(`/api/rental/variants/${asset.variant_id}/tariffs?view=selectable`)
    : [];
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

async function openRentalIssueConfirmation(orderId) {
  try {
    [state.rental.order, state.rental.issuePreview] = await Promise.all([
      api(`/rental/orders/${orderId}`),
      api(`/rental/orders/${orderId}/issue-preview`),
    ]);
    logicalParent = () => openRentalHub();
    renderRentalIssueConfirmation();
  } catch (error) { showToast(error.message, true); }
}

function renderRentalIssueConfirmation() {
  const order = state.rental.order;
  const preview = state.rental.issuePreview;
  const items = preview.items.map((item) => `<article class="issue-preview-item" data-issue-preview-item="${item.id}" data-tariff-type="${item.tariff_type}" data-duration-minutes="${order.items.find((candidate) => candidate.id === item.id)?.tariff_snapshot?.duration_minutes || ""}" data-fixed-due="${item.tariff_type === "slot" ? item.due_at || "" : ""}">
    <strong>${escapeHtml(item.title_snapshot)} · ${escapeHtml(item.asset_number_snapshot)}</strong>
    <span class="muted small">Тариф: ${escapeHtml(item.tariff_name)}${item.tariff_terms ? ` · ${escapeHtml(item.tariff_terms)}` : ""}</span>
    <div><span class="muted small">Фактическая выдача</span><strong data-issue-at-label>Сейчас, ${formatDate(preview.server_now)}</strong></div>
    <div><span class="muted small">Вернуть до</span><strong data-issue-due-label>${item.due_at ? formatDate(item.due_at) : "будет рассчитано при выдаче"}</strong></div>
  </article>`).join("");
  root.innerHTML = `<div class="shell narrow-shell">
    ${topbar(true)}
    <p class="eyebrow">Подтверждение</p><h1>Выдать аренду</h1>
    <section class="card"><span class="muted small">Клиент</span><h2>${escapeHtml(order.customer_name_snapshot)}</h2><p>${escapeHtml(order.order_number)} · ${order.items.length} поз.</p></section>
    <div class="issue-preview-list">${items}</div>
    ${preview.blocked_reason ? `<div class="notice danger"><strong>Выдача заблокирована</strong><br>${escapeHtml(preview.blocked_reason)}</div>` : ""}
    ${preview.requires_agreement && !preview.blocked_reason ? '<div class="notice"><strong>Договор сформирован</strong><br>Выдача разрешена.</div>' : ""}
    <form class="card issue-confirm-form" id="rental-issue-form">
      <label class="check"><input type="checkbox" id="manual-issue-toggle"> Указать время выдачи вручную</label>
      <div class="field hidden" id="manual-issue-field"><label>Фактическое время выдачи</label><input name="issued_at" type="datetime-local" value="${toLocalInput(new Date(preview.server_now))}"><p class="muted small">Допустима корректировка не более чем на 24 часа назад.</p></div>
      <div class="rental-card-actions"><button class="button secondary" type="button" id="cancel-rental-issue">Отмена</button><button class="button" type="submit" id="confirm-rental-issue" ${preview.can_issue ? "" : "disabled"}>Выдать оборудование</button></div>
    </form>
  </div>`;
  bindTopbar();
  document.querySelector("#cancel-rental-issue").addEventListener("click", openRentalHub);
  document.querySelector("#manual-issue-toggle").addEventListener("change", toggleManualIssueTime);
  document.querySelector('[name="issued_at"]').addEventListener("input", updateIssuePreviewTimes);
  document.querySelector("#rental-issue-form").addEventListener("submit", issueRentalOrder);
}

function toggleManualIssueTime() {
  const manual = document.querySelector("#manual-issue-toggle").checked;
  document.querySelector("#manual-issue-field").classList.toggle("hidden", !manual);
  updateIssuePreviewTimes();
}

function updateIssuePreviewTimes() {
  const manual = document.querySelector("#manual-issue-toggle")?.checked;
  const input = document.querySelector('[name="issued_at"]');
  const issuedAt = manual && input?.value ? new Date(input.value) : new Date(state.rental.issuePreview.server_now);
  document.querySelectorAll("[data-issue-preview-item]").forEach((element) => {
    let dueAt = element.dataset.fixedDue ? new Date(element.dataset.fixedDue) : null;
    if (element.dataset.tariffType === "duration") dueAt = new Date(issuedAt.getTime() + Number(element.dataset.durationMinutes) * 60000);
    element.querySelector("[data-issue-at-label]").textContent = `${manual ? "Вручную" : "Сейчас"}, ${formatDate(issuedAt)}`;
    element.querySelector("[data-issue-due-label]").textContent = dueAt ? formatDate(dueAt) : "будет рассчитано при выдаче";
  });
}

async function issueRentalOrder(event) {
  event.preventDefault();
  const submit = document.querySelector("#confirm-rental-issue");
  submit.disabled = true;
  submit.innerHTML = '<span class="spinner"></span> Выдаём';
  const manual = document.querySelector("#manual-issue-toggle").checked;
  const issuedAtValue = new FormData(event.currentTarget).get("issued_at");
  const payload = manual ? { issued_at: new Date(String(issuedAtValue)).toISOString() } : {};
  try {
    state.rental.order = await api(`/rental/orders/${state.rental.order.id}/issue`, { method: "POST", body: JSON.stringify(payload) });
    await openRentalOperationalOrder(state.rental.order.id, state.rental.order);
    showToast("Оборудование выдано");
  } catch (error) {
    showToast(error.message, true);
    submit.disabled = false;
    submit.textContent = "Выдать оборудование";
  }
}

async function openRentalOperationalOrder(orderId, loadedOrder = null) {
  try {
    recordRoute("order", { orderId });
    state.rental.order = loadedOrder || await api(`/rental/orders/${orderId}`);
    logicalParent = () => openRentalHub();
    const order = state.rental.order;
    root.innerHTML = `<div class="shell narrow-shell">${topbar(true)}<p class="eyebrow">Активная аренда</p><h1>${escapeHtml(order.order_number)}</h1><section class="card"><h2>${escapeHtml(order.customer_name_snapshot)}</h2><p>${escapeHtml(order.customer_phone_snapshot)}</p></section><div class="rental-order-grid">${renderActiveRentalCard(order, false)}</div></div>`;
    bindTopbar();
    document.querySelector("[data-return-rental]")?.addEventListener("click", () => openRentalReturnOrder(order.id));
    document.querySelectorAll("[data-return-item]").forEach((button) => button.addEventListener("click", () => openRentalReturnOrder(order.id, button.dataset.returnItem)));
    updateRentalTimers();
    if (state.rental.timerHandle) window.clearInterval(state.rental.timerHandle);
    state.rental.timerHandle = window.setInterval(updateRentalTimers, 1000);
  } catch (error) { showToast(error.message, true); }
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

async function openRentalReturnOrder(orderId, focusItemId = null) {
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
    const activeIds = state.rental.order.items.filter((item) => item.status === "issued").map((item) => item.id);
    state.rental.returnItemIds = focusItemId ? activeIds.filter((id) => id === focusItemId) : activeIds;
    state.rental.returnPreview = state.rental.returnItemIds.length
      ? await requestRentalReturnPreview(state.rental.returnItemIds.map((item_id) => {
        const item = state.rental.order.items.find((candidate) => candidate.id === item_id);
        return { item_id, condition: "good", damage_charge: "0", notes: null, seal_verifications: defaultSealVerifications(item), component_verifications: defaultComponentVerifications(item) };
      }))
      : null;
    state.rental.agreement = await api(`/rental/orders/${orderId}/agreement`);
    renderRentalReturnOrder();
  } catch (error) { showToast(error.message, true); }
}

function renderRentalReturnOrder() {
  const order = state.rental.order;
  const requiredDepositTotal = Number(order.required_deposit_total ?? order.recommended_deposit_total ?? 0);
  const rawDepositReceived = order.deposit_received ?? order.deposit_amount ?? null;
  const depositReceivedConfirmed = order.deposit_received_confirmed === true
    || (order.deposit_received_confirmed === undefined && rawDepositReceived !== null);
  const depositReceived = depositReceivedConfirmed ? Number(rawDepositReceived) : null;
  const total = order.items.reduce(
    (sum, item) => sum + Number(item.agreed_price) - Number(item.discount),
    0,
  );
  const activeItems = order.items.filter((item) => item.status === "issued");
  const previewById = new Map((state.rental.returnPreview?.items || []).map((item) => [item.item_id, item]));
  const itemCards = order.items.map((item) => {
    const asset = state.rental.returnAssets.get(item.rental_asset_id);
    const completed = item.status !== "issued";
    const preview = previewById.get(item.id);
    return `<article class="card return-item ${completed ? "completed-item" : ""}" data-return-item="${item.id}">
      <div class="return-item-head">
        ${completed ? "" : `<input class="return-check" type="checkbox" data-return-select="${item.id}" aria-label="Выбрать ${escapeHtml(item.asset_number_snapshot)}" ${state.rental.returnItemIds.includes(item.id) ? "checked" : ""}>`}
        <div>
          <h3>${escapeHtml(item.title_snapshot)}</h3>
          <div class="muted small">${escapeHtml(item.asset_number_snapshot)} · ${escapeHtml(itemStatusLabel(item.status))}${asset ? ` · ${escapeHtml(availabilityLabel(asset.availability))}` : ""}</div>
        </div>
        ${asset ? `<button class="button ghost compact" data-return-open-asset="${asset.id}">Предмет аренды →</button>` : ""}
      </div>
      ${completed ? renderCompletedRentalItem(item) : `
      <div class="return-timing-facts"><span><small>Выдано</small><strong>${formatDate(item.issued_at)}</strong></span><span><small>Вернуть до</small><strong>${formatDate(item.due_at)}</strong></span><span><small>Льгота</small><strong>${Number(item.tariff_snapshot?.grace_minutes || 0)} мин</strong></span><span><small>Тариф</small><strong>${escapeHtml(item.tariff_snapshot?.tariff_name || "Тариф")}</strong></span></div>
      ${renderReturnIdentification(item, preview)}
      <div class="field-row">
        <div class="field"><label>Состояние при возврате</label><select data-return-condition="${item.id}">
          <option value="good" ${preview?.condition === "good" ? "selected" : ""}>Без замечаний</option>
          <option value="damaged" ${preview?.condition === "damaged" ? "selected" : ""}>Повреждён</option>
          <option value="incomplete" ${preview?.condition === "incomplete" ? "selected" : ""}>Неполный комплект</option>
          <option value="lost" ${preview?.condition === "lost" ? "selected" : ""}>Утрачен</option>
        </select></div>
        <div class="field"><label>Повреждение / компенсация, ₽</label><input data-return-damage-charge="${item.id}" type="number" inputmode="decimal" min="0" step="0.01" value="${preview?.damage_charge || 0}" required></div>
      </div>
      <div class="field"><label>Комментарий <span class="muted">(обязателен при проблеме)</span></label><textarea data-return-note="${item.id}" placeholder="Опишите повреждение, недостачу или обстоятельства утраты">${escapeHtml(preview?.notes || "")}</textarea></div>
      ${preview ? renderReturnItemCalculation(preview) : ""}`}
    </article>`;
  }).join("");
  root.innerHTML = `<div class="shell">
    ${topbar(true)}
    <p class="eyebrow">${order.status === "closed" ? "История аренды" : "Активная аренда"} · ${escapeHtml(order.order_number)}</p>
    <h1>${escapeHtml(order.customer_name_snapshot)}</h1>
    <p class="muted">${escapeHtml(order.customer_phone_snapshot)}</p>
    <button class="button ghost compact" id="return-open-customer">Клиент →</button>
    <section class="card order-facts">
      <div><span class="muted small">Срок</span><strong>${formatShortDate(order.planned_start_at)} — ${formatShortDate(order.planned_return_at)}</strong></div>
      <div><span class="muted small">Стоимость</span><strong>${formatMoney(total)}</strong></div>
      <div><span class="muted small">Обеспечительный платёж</span><strong>${formatMoney(requiredDepositTotal)}</strong><small class="muted">Фактически получено: ${depositReceivedConfirmed ? formatMoney(depositReceived) : "не подтверждено"}</small></div>
      <div><span class="muted small">Статус</span><strong>${order.status === "closed" ? "Завершён" : order.is_overdue ? "Просрочен" : "Активен"}</strong></div>
    </section>
    ${depositReceivedConfirmed ? "" : `<section class="notice rental-deposit-warning"><strong>Фактическое получение обеспечительного платежа не было подтверждено.</strong><p>Подтвердите полученную сумму перед оформлением возврата и расчётом. Ноль указывается явно.</p><form id="return-deposit-form" class="rental-deposit-form"><div class="field"><label>Фактически получено, ₽</label><input name="deposit_received" type="number" min="0" step="0.01" inputmode="decimal" value="${escapeHtml(order.required_deposit_total ?? order.recommended_deposit_total ?? 0)}" required></div><button class="button secondary compact" type="submit">Сохранить залог</button></form></section>`}
    <div class="section-heading"><h2>Предметы аренды · ${order.items.length}</h2><span class="muted small">Ожидают: ${activeItems.length}</span></div>
    <div>${itemCards}</div>
    ${state.rental.returnPreview ? renderReturnDepositPreview(state.rental.returnPreview) : ""}
    ${order.status === "closed" ? `<section class="checkout-summary return-deposit-preview"><div class="section-heading"><h2>Финальный расчёт</h2><span class="chip good">Завершён</span></div><span>Обеспечительный платёж <strong>${formatMoney(requiredDepositTotal)}</strong></span><span>Фактически получено <strong>${formatMoney(depositReceived)}</strong></span><span>Просрочка <strong>${formatMoney(Number(order.overdue_charge_total || 0))}</strong></span><span>Повреждения / компенсация <strong>${formatMoney(Number(order.damage_charge_total || 0))}</strong></span><span>Удержано из залога <strong>${formatMoney(Number(order.deposit_applied_total || 0))}</strong></span><span>К возврату <strong>${formatMoney(Number(order.deposit_to_refund || 0))}</strong></span><span>Доплатить <strong>${formatMoney(Number(order.remaining_amount_due || 0))}</strong></span></section>` : ""}
    ${activeItems.length ? `<section class="checkout-summary">
      <button class="button secondary" id="complete-selected-items" ${depositReceivedConfirmed ? "" : "disabled"}>Подтвердить выбранные</button>
      <button class="button" id="complete-all-items" ${depositReceivedConfirmed ? "" : "disabled"}>Оформить возврат всех</button>
    </section>` : ""}
    ${state.rental.agreement ? `<section class="card"><span class="muted small">Договор</span><strong>№ ${escapeHtml(state.rental.agreement.agreement_number)}</strong><button class="button ghost compact" data-open-return-agreement>Открыть PDF</button></section>` : ""}
  </div>`;
  bindTopbar();
  document.querySelectorAll("[data-return-select]").forEach((checkbox) => {
    checkbox.addEventListener("change", updateReturnSelection);
  });
  document.querySelectorAll("[data-return-condition], [data-return-damage-charge], [data-return-note], [data-return-seal-result], [data-return-seal-note], [data-return-component-result], [data-return-component-note]").forEach((input) => input.addEventListener("change", refreshRentalReturnPreview));
  document.querySelectorAll("[data-return-seals-ok]").forEach((button) => button.addEventListener("click", markAllReturnSealsOk));
  document.querySelectorAll("[data-return-components-ok]").forEach((button) => button.addEventListener("click", markAllReturnComponentsPresent));
  document.querySelectorAll("[data-return-open-asset]").forEach((button) => {
    button.addEventListener("click", () => openOperationsAsset(button.dataset.returnOpenAsset));
  });
  document.querySelector("#return-open-customer").addEventListener("click", () => selectRentalCustomer(order.customer_id));
  document.querySelector("#return-deposit-form")?.addEventListener("submit", saveRentalReturnDeposit);
  document.querySelector("#complete-selected-items")?.addEventListener("click", () => completeRentalItems(false));
  document.querySelector("#complete-all-items")?.addEventListener("click", () => completeRentalItems(true));
  document.querySelector("[data-open-return-agreement]")?.addEventListener("click", () => openAuthenticatedFile(`/rental/agreements/${state.rental.agreement.id}/pdf`));
  updateReturnSelection();
}

function defaultSealVerifications(item) {
  return (item?.asset_identification_snapshot?.seals || []).map((seal) => ({
    location: seal.location,
    seal_number: seal.seal_number,
    result: "ok",
    note: null,
  }));
}

function defaultComponentVerifications(item) {
  return (item?.asset_identification_snapshot?.components || []).map((component) => ({
    component_id: component.component_id,
    name: component.name,
    result: "present",
    note: null,
  }));
}

function renderReturnIdentification(item, preview) {
  const identification = item.asset_identification_snapshot || {};
  const seals = identification.seals || [];
  const components = identification.components || [];
  if (!identification.serial_number && !seals.length && !components.length) return "";
  const verificationByNumber = new Map((preview?.seal_verifications || []).map((verification) => [verification.expected_seal_number, verification]));
  const sealRows = seals.map((seal) => {
    const verification = verificationByNumber.get(seal.seal_number) || { result: "ok", note: null };
    return `<div class="return-seal-row ${verification.result === "ok" ? "" : "seal-problem"}">
      <div><strong>${escapeHtml(seal.location)} — ${escapeHtml(seal.seal_number)}</strong><small class="muted">Ожидаемая пломба из снимка выдачи</small></div>
      <div class="field"><label>Проверка</label><select data-return-seal-result="${item.id}" data-seal-location="${escapeHtml(seal.location)}" data-seal-number="${escapeHtml(seal.seal_number)}">
        <option value="ok" ${verification.result === "ok" ? "selected" : ""}>OK</option>
        <option value="broken" ${verification.result === "broken" ? "selected" : ""}>BROKEN</option>
        <option value="missing" ${verification.result === "missing" ? "selected" : ""}>MISSING</option>
        <option value="mismatch" ${verification.result === "mismatch" ? "selected" : ""}>MISMATCH</option>
      </select></div>
      <div class="field"><label>Комментарий при проблеме</label><input data-return-seal-note="${item.id}" data-seal-number="${escapeHtml(seal.seal_number)}" value="${escapeHtml(verification.note || "")}" placeholder="Что обнаружено"></div>
    </div>`;
  }).join("");
  const verificationByComponent = new Map((preview?.component_verifications || []).map((verification) => [verification.component_id, verification]));
  const componentRows = components.map((component) => {
    const verification = verificationByComponent.get(component.component_id) || { result: "present", note: null };
    return `<div class="return-component-row ${verification.result === "present" ? "" : "component-problem"}">
      <div><strong>${escapeHtml(component.name)}</strong>${component.note ? `<small class="muted">${escapeHtml(component.note)}</small>` : ""}</div>
      <div class="field"><label>Проверка</label><select data-return-component-result="${item.id}" data-component-id="${component.component_id}" data-component-name="${escapeHtml(component.name)}">
        <option value="present" ${verification.result === "present" ? "selected" : ""}>PRESENT</option>
        <option value="missing" ${verification.result === "missing" ? "selected" : ""}>MISSING</option>
        <option value="damaged" ${verification.result === "damaged" ? "selected" : ""}>DAMAGED</option>
      </select></div>
      <div class="field"><label>Комментарий при проблеме</label><input data-return-component-note="${item.id}" data-component-id="${component.component_id}" value="${escapeHtml(verification.note || "")}" placeholder="Что отсутствует или повреждено"></div>
    </div>`;
  }).join("");
  return `<section class="return-identification">
    <div class="section-heading"><strong>Идентификация и комплектность при выдаче</strong></div>
    ${identification.serial_number ? `<p>Ожидаемый серийный номер: <strong>${escapeHtml(identification.serial_number)}</strong></p>` : ""}
    ${seals.length ? `<div class="section-heading"><strong>Пломбы</strong><button class="button ghost compact" type="button" data-return-seals-ok="${item.id}">Все соответствуют</button></div>` : ""}
    ${(preview?.seal_verifications || []).some((verification) => verification.result !== "ok") ? '<p class="seal-warning"><strong>Есть расхождение по пломбам — проверьте комментарий перед возвратом.</strong></p>' : ""}
    ${sealRows}
    ${components.length ? `<div class="section-heading"><strong>Комплектность</strong><button class="button ghost compact" type="button" data-return-components-ok="${item.id}">Все элементы на месте</button></div>` : ""}
    ${(preview?.component_verifications || []).some((verification) => verification.result !== "present") ? '<p class="seal-warning"><strong>Есть проблема с комплектностью — возврат будет отмечен как INCOMPLETE или DAMAGED.</strong></p>' : ""}
    ${componentRows}
  </section>`;
}

function markAllReturnSealsOk(event) {
  const itemId = event.currentTarget.dataset.returnSealsOk;
  document.querySelectorAll(`[data-return-seal-result="${itemId}"]`).forEach((select) => { select.value = "ok"; });
  document.querySelectorAll(`[data-return-seal-note="${itemId}"]`).forEach((input) => { input.value = ""; });
  refreshRentalReturnPreview();
}

function markAllReturnComponentsPresent(event) {
  const itemId = event.currentTarget.dataset.returnComponentsOk;
  document.querySelectorAll(`[data-return-component-result="${itemId}"]`).forEach((select) => { select.value = "present"; });
  document.querySelectorAll(`[data-return-component-note="${itemId}"]`).forEach((input) => { input.value = ""; });
  refreshRentalReturnPreview();
}

function renderReturnItemCalculation(preview) {
  const stateLabel = {
    on_time: "В срок",
    overdue_grace: `Просрочка ${preview.overdue_minutes} мин · льготный период`,
    overdue_chargeable: `Просрочка ${preview.overdue_minutes} мин · к оплате`,
  }[preview.overdue_state] || preview.overdue_state;
  return `<section class="return-calculation">
    <div class="section-heading"><strong>Расчёт возврата</strong><span class="chip ${preview.overdue_state === "overdue_chargeable" ? "warn" : "good"}">${escapeHtml(stateLabel)}</span></div>
    <span>Базовая аренда <strong>${formatMoney(Number(preview.base_rental_price))}</strong></span>
    <span>Просрочка <strong>${formatMoney(Number(preview.overdue_charge))}</strong></span>
    <span>Повреждение / компенсация <strong>${formatMoney(Number(preview.damage_charge))}</strong></span>
    <span class="return-total">Итого <strong>${formatMoney(Number(preview.total_charge))}</strong></span>
    <small>${preview.overdue_blocks ? `${preview.overdue_blocks} × ${preview.billing_unit_minutes} мин по ${formatMoney(Number(preview.billing_unit_price))}` : "Дополнительный тарифный период не начислен"}</small>
  </section>`;
}

function renderReturnDepositPreview(preview) {
  if (!preview.deposit_received_confirmed) {
    return `<section class="checkout-summary return-deposit-preview rental-deposit-warning">
      <div class="section-heading"><h2>Обеспечительный платёж</h2><span class="chip warn">Не подтверждён</span></div>
      <span>Обеспечительный платёж заказа <strong>${formatMoney(Number(preview.required_deposit_total))}</strong></span>
      <p>Фактическое получение обеспечительного платежа не было подтверждено. Финальный расчёт нельзя завершить до подтверждения.</p>
    </section>`;
  }
  return `<section class="checkout-summary return-deposit-preview">
    <div class="section-heading"><h2>Обеспечительный платёж</h2><span class="chip ${preview.is_partial ? "warn" : "good"}">${preview.is_partial ? "Предварительный расчёт" : "Финальный расчёт"}</span></div>
    <span>Обеспечительный платёж заказа <strong>${formatMoney(Number(preview.required_deposit_total))}</strong></span>
    <span>Фактически получено <strong>${formatMoney(Number(preview.deposit_received))}</strong></span>
    <span>Удерживается этим возвратом <strong>${formatMoney(Number(preview.deposit_applied_by_return))}</strong></span>
    <span>${preview.is_partial ? "Предварительно остаётся" : "К возврату"} <strong>${formatMoney(Number(preview.is_partial ? preview.provisional_deposit_remaining : preview.deposit_to_refund))}</strong></span>
    ${preview.remaining_amount_due === null ? "" : `<span>Доплатить <strong>${formatMoney(Number(preview.remaining_amount_due))}</strong></span>`}
  </section>`;
}

async function saveRentalReturnDeposit(event) {
  event.preventDefault();
  const amount = String(new FormData(event.currentTarget).get("deposit_received"));
  try {
    state.rental.order = await api(`/rental/orders/${state.rental.order.id}/deposit`, {
      method: "PATCH",
      body: JSON.stringify({ deposit_received: amount }),
    });
    await openRentalReturnOrder(state.rental.order.id);
    showToast("Фактически полученный залог сохранён");
  } catch (error) { showToast(error.message, true); }
}

function renderCompletedRentalItem(item) {
  const condition = {
    good: "Без замечаний",
    damaged: "Повреждено",
    incomplete: "Неполный комплект",
    lost: "Утрачено",
  }[item.return_condition] || itemStatusLabel(item.status);
  const verificationRows = (item.seal_verification_snapshot || []).map((verification) => `<span class="${verification.result === "ok" ? "" : "seal-warning"}">${escapeHtml(verification.location)} — ${escapeHtml(verification.expected_seal_number)} <strong>${escapeHtml(String(verification.result).toUpperCase())}</strong>${verification.note ? ` · ${escapeHtml(verification.note)}` : ""}</span>`).join("");
  const componentRows = (item.component_verification_snapshot || []).map((verification) => `<span class="${verification.result === "present" ? "" : "seal-warning"}">${escapeHtml(verification.name)} <strong>${escapeHtml(String(verification.result).toUpperCase())}</strong>${verification.note ? ` · ${escapeHtml(verification.note)}` : ""}</span>`).join("");
  return `<div class="return-history-breakdown">
    <div class="chips"><span class="chip ${item.status === "lost" ? "warn" : "good"}">${escapeHtml(condition)}</span><span class="chip">${item.returned_at ? formatDate(item.returned_at) : "—"}</span></div>
    <span>Базовая аренда <strong>${formatMoney(Number(item.agreed_price) - Number(item.discount))}</strong></span>
    <span>Просрочка <strong>${formatMoney(Number(item.overdue_charge || 0))}</strong></span>
    <span>Повреждение / компенсация <strong>${formatMoney(Number(item.damage_charge || 0))}</strong></span>
    <span>Удержано из залога <strong>${formatMoney(Number(item.deposit_applied || 0))}</strong></span>
    ${verificationRows ? `<div class="return-verification-history"><strong>Проверка пломб при возврате</strong>${verificationRows}</div>` : ""}
    ${componentRows ? `<div class="return-verification-history"><strong>Проверка комплектности при возврате</strong>${componentRows}</div>` : ""}
    ${item.return_note ? `<p>${escapeHtml(item.return_note)}</p>` : ""}
  </div>`;
}

function updateReturnSelection(event = null) {
  const selected = [...document.querySelectorAll("[data-return-select]:checked")].map((input) => input.dataset.returnSelect);
  state.rental.returnItemIds = selected;
  const count = selected.length;
  const button = document.querySelector("#complete-selected-items");
  if (button) {
    button.disabled = count === 0;
    button.textContent = count ? `Подтвердить выбранные · ${count}` : "Подтвердить выбранные";
  }
  if (event) refreshRentalReturnPreview();
}

function buildReturnCompletion(itemId) {
  const sealNotes = [...document.querySelectorAll(`[data-return-seal-note="${itemId}"]`)];
  const sealVerifications = [...document.querySelectorAll(`[data-return-seal-result="${itemId}"]`)].map((select, index) => ({
    location: select.dataset.sealLocation,
    seal_number: select.dataset.sealNumber,
    result: select.value,
    note: nullableText(sealNotes[index]?.value),
  }));
  const componentNotes = new Map([...document.querySelectorAll(`[data-return-component-note="${itemId}"]`)].map((input) => [input.dataset.componentId, input]));
  const componentVerifications = [...document.querySelectorAll(`[data-return-component-result="${itemId}"]`)].map((select) => ({
    component_id: select.dataset.componentId,
    name: select.dataset.componentName,
    result: select.value,
    note: nullableText(componentNotes.get(select.dataset.componentId)?.value),
  }));
  return {
    item_id: itemId,
    condition: document.querySelector(`[data-return-condition="${itemId}"]`).value,
    damage_charge: document.querySelector(`[data-return-damage-charge="${itemId}"]`).value,
    notes: nullableText(document.querySelector(`[data-return-note="${itemId}"]`).value),
    seal_verifications: sealVerifications,
    component_verifications: componentVerifications,
  };
}

async function requestRentalReturnPreview(items) {
  return api(`/rental/orders/${state.rental.order.id}/return-preview`, {
    method: "POST",
    body: JSON.stringify({ items }),
  });
}

async function refreshRentalReturnPreview() {
  const itemIds = [...document.querySelectorAll("[data-return-select]:checked")].map((input) => input.dataset.returnSelect);
  if (!itemIds.length) {
    state.rental.returnPreview = null;
    return;
  }
  try {
    state.rental.returnItemIds = itemIds;
    state.rental.returnPreview = await requestRentalReturnPreview(itemIds.map(buildReturnCompletion));
    renderRentalReturnOrder();
  } catch (error) { showToast(error.message, true); }
}

async function completeRentalItems(all) {
  const itemIds = all
    ? state.rental.order.items.filter((item) => item.status === "issued").map((item) => item.id)
    : [...document.querySelectorAll("[data-return-select]:checked")].map((input) => input.dataset.returnSelect);
  if (!itemIds.length) return;
  const items = itemIds.map(buildReturnCompletion);
  const hasLost = items.some((item) => item.condition === "lost");
  const prompt = all ? "Оформить возврат всех оставшихся позиций?" : `Подтвердить возврат позиций: ${itemIds.length}?`;
  if (!window.confirm(`${prompt}${hasLost ? " Среди них отмечена утрата." : ""}`)) return;
  try {
    state.rental.returnPreview = await requestRentalReturnPreview(items);
    state.rental.order = await api(`/rental/orders/${state.rental.order.id}/return`, {
      method: "POST",
      body: JSON.stringify({ items }),
    });
    if (state.rental.order.status === "closed") {
      renderRentalReturnCompleted();
      return;
    }
    await openRentalReturnOrder(state.rental.order.id);
    showToast("Частичный возврат сохранён. Залог пока рассчитан предварительно.");
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

function formatRentalDate(value) {
  if (!value) return "не указана";
  return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" }).format(new Date(`${value}T12:00:00`));
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
