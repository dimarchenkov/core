const root = document.querySelector("#app");
const toast = document.querySelector("#toast");
let logicalParent = () => loadHome();
let restoringHistory = false;
let zxingLoader = null;

function recordRoute(name, data = {}) {
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
  return `${window.location.pathname}#${route.name}`;
}

function routeFromLocation() {
  const [name, id] = window.location.hash.slice(1).split("/");
  if (name === "product" && id) return { name, productId: id };
  if (name === "customer" && id) return { name, customerId: id };
  if (name === "order" && id) return { name, orderId: id };
  if (name === "intake" && id) return { name, sessionId: id };
  if (["workspace", "catalog", "rental"].includes(name)) return { name };
  return null;
}

async function restoreRoute(route) {
  restoringHistory = true;
  try {
    if (!route || route.name === "workspace") await loadHome();
    else if (route.name === "catalog") await openOperationsCatalog();
    else if (route.name === "product") await openOperationsProduct(route.productId);
    else if (route.name === "rental") await openRentalHub();
    else if (route.name === "customer") await selectRentalCustomer(route.customerId);
    else if (route.name === "order") await openRentalReturnOrder(route.orderId);
    else if (route.name === "intake") await openSession(route.sessionId);
    else await loadHome();
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
  products: [],
  variants: [],
  itemDisplay: new Map(),
  imageUrls: new Map(),
  mode: null,
  result: null,
  intakeBarcode: {
    value: "",
    result: null,
    unknown: false,
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

function showToast(message, error = false) {
  const text = message instanceof Event ? "Действие не удалось. Повторите ещё раз." : String(message ?? "");
  toast.textContent = text;
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
  document.querySelector("#start-session").addEventListener("click", () => startSession());
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
      await logicalParent();
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
    state.intakeBarcode = { value: "", result: null, unknown: false };
    document.querySelector("#photo-input").click();
  });
  document.querySelector("#photo-input")?.addEventListener("change", uploadNewPhoto);
  document.querySelector("#barcode-lookup-form")?.addEventListener("submit", lookupIntakeBarcode);
  document.querySelectorAll("[data-barcode-camera]").forEach((button) => {
    button.addEventListener("click", () => startBarcodeCamera(button.dataset.barcodeTarget));
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
  document.querySelector("#barcode-create-new")?.addEventListener("click", () => document.querySelector("#photo-input").click());
  document.querySelector("#barcode-use-existing")?.addEventListener("click", useLocatedBarcode);
  document.querySelector("#known-form")?.addEventListener("submit", addKnownItem);
  document.querySelectorAll("[data-item-form]").forEach((form) => form.addEventListener("submit", saveItem));
  document.querySelector("#supplier")?.addEventListener("change", saveSupplier);
  document.querySelector("#complete-session")?.addEventListener("click", completeSession);
  hydrateImages();
}

function renderActionPanel() {
  const options = state.variants.flatMap((variant) => {
    const product = state.products.find((item) => item.id === variant.product_id);
    const barcodes = variant.barcodes?.length ? variant.barcodes : [{ value: variant.barcode }];
    return barcodes.map((barcode) => `<option value="${escapeHtml(barcode.value)}">${escapeHtml(product?.title || "Товар")} · ${escapeHtml(variant.title)} · ${escapeHtml(variant.sku)}</option>`);
  }).join("");
  const lookup = renderIntakeBarcodeResult();
  return `
    <input class="hidden" id="photo-input" type="file" accept="image/*" capture="environment">
    <section class="card ${state.mode === "known" ? "" : "hidden"}">
      <h2>Найти товар</h2>
      <p class="muted small">Введите код вручную, отсканируйте аппаратным сканером с Enter или используйте камеру.</p>
      <form id="barcode-lookup-form" class="search-row barcode-lookup-row">
        <input id="intake-barcode" name="barcode" value="${escapeHtml(state.intakeBarcode.value)}" autocomplete="off" placeholder="EAN, UPC или Code 128" required autofocus>
        <button class="button" type="submit">Найти</button>
        <button class="button secondary" data-barcode-camera data-barcode-target="intake-barcode" type="button">📷 Сканировать камерой</button>
      </form>
      <div id="barcode-lookup-result">${lookup}</div>
      <hr>
      <p class="muted small">Можно также найти существующую позицию по SKU или названию.</p>
      <form id="known-form">
        <div class="field"><label for="barcode">Штрихкод, SKU или название</label><input id="barcode" name="query" list="variant-options" autocomplete="off" required autofocus><datalist id="variant-options">${options}</datalist></div>
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
      </form>
    </section>`;
}

function renderIntakeBarcodeResult() {
  const lookup = state.intakeBarcode;
  if (lookup.result) {
    const variant = lookup.result;
    const product = state.products.find((item) => item.id === variant.product_id);
    return `<div class="card"><strong>${escapeHtml(product?.title || "Товар")} · ${escapeHtml(variant.title)}</strong><div class="muted small">${escapeHtml(variant.sku)} · ${escapeHtml(lookup.value)}</div><button class="button secondary full" id="barcode-use-existing" type="button">Использовать найденный товар</button></div>`;
  }
  if (lookup.unknown) {
    return `<div class="card"><strong>Код ещё не зарегистрирован</strong><div class="muted small">${escapeHtml(lookup.value)} будет сохранён как штрихкод производителя.</div><button class="button secondary full" id="barcode-create-new" type="button">Создать новый товар</button></div>`;
  }
  return "";
}

async function lookupIntakeBarcode(eventOrValue) {
  eventOrValue?.preventDefault?.();
  const value = typeof eventOrValue === "string"
    ? eventOrValue
    : String(new FormData(eventOrValue.currentTarget).get("barcode") || "").trim();
  if (!value) return;
  try {
    const result = await lookupVariantBarcode(value);
    state.intakeBarcode = {
      value,
      result: result.variant,
      unknown: !result.variant,
    };
  } catch (error) {
    showToast(error.message, true);
    return;
  }
  renderWorkspace();
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

function useLocatedBarcode() {
  const input = document.querySelector("#barcode");
  input.value = state.intakeBarcode.value;
  document.querySelector("#known-quantity").focus();
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
  if (inputId === "intake-barcode") await lookupIntakeBarcode(input.value);
  else await identifyDraftManufacturerBarcode(input);
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
        <div class="field"><label>Цена продажи, ₽ <span class="muted">(необязательно)</span></label><input name="retail_price" type="number" inputmode="decimal" min="0" step="0.01" value="${item.retail_price ?? ""}"></div>
        <div class="rental-allocation">
          <div class="field"><label>Из них в аренду, шт.</label><input name="rental_quantity" type="number" inputmode="numeric" min="0" ${item.quantity === null ? "" : `max="${item.quantity}"`} value="${item.rental_quantity ?? 0}"></div>
          <p class="muted small">Каждый предмет аренды получит собственный инвентарный номер.</p>
        </div>
        <button class="button secondary full" type="submit">Сохранить позицию</button>
      </form>
    </article>`;
}

function renderNewItemFields(item) {
  const categories = state.categories.map((category) => `<option value="${category.id}" ${item.category_id === category.id ? "selected" : ""}>${escapeHtml(category.title)}</option>`).join("");
  const barcodeInputId = `manufacturer-barcode-${item.id}`;
  return `
    <div class="field"><label>Категория</label><select name="category_id" required><option value="">Выберите категорию</option>${categories}</select></div>
    <div class="field"><label>Название товара</label><input name="product_title" value="${escapeHtml(item.product_title || "")}" required></div>
    <div class="field"><label>Вариант — цвет, размер или исполнение</label><input name="variant_title" value="${escapeHtml(item.variant_title || "")}" required></div>
    <div class="field"><label for="${barcodeInputId}">Штрихкод производителя <span class="muted">(необязательно)</span></label><input id="${barcodeInputId}" name="manufacturer_barcode" data-manufacturer-barcode value="${escapeHtml(item.manufacturer_barcode || "")}" autocomplete="off" inputmode="text"><button class="button secondary full" data-barcode-camera data-barcode-target="${barcodeInputId}" type="button">📷 Сканировать камерой</button><div class="small" data-barcode-status="${barcodeInputId}"></div></div>
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

async function addKnownItem(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  const query = String(data.get("query")).trim();
  const normalized = query.toLocaleLowerCase("ru");
  const variant = state.variants.find((item) => {
    const product = state.products.find((value) => value.id === item.product_id);
    const matchesBarcode = (item.barcodes || [{ value: item.barcode }]).some((barcode) => barcode.value === query);
    return matchesBarcode || item.sku.toLocaleLowerCase("ru") === normalized || `${product?.title || ""} ${item.title}`.toLocaleLowerCase("ru") === normalized;
  });
  const payload = {
    ...(variant ? { variant_id: variant.id } : { barcode: query }),
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
  const retailPrice = nullableText(data.get("retail_price"));
  const payload = {
    quantity: quantity === null ? null : Number(quantity),
    rental_quantity: rentalQuantity === null ? 0 : Number(rentalQuantity),
    purchase_price: purchasePrice,
    retail_price: retailPrice,
  };
  if (item.kind !== "existing_variant") {
    Object.assign(payload, {
      category_id: nullableText(data.get("category_id")),
      product_title: nullableText(data.get("product_title")),
      variant_title: nullableText(data.get("variant_title")),
      product_description: nullableText(data.get("product_description")),
      manufacturer_barcode: nullableText(data.get("manufacturer_barcode")),
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

async function openOperationsCatalog(query = "", productFilter = "all", sort = "title") {
  try {
    recordRoute("catalog");
    logicalParent = () => loadHome();
    const params = new URLSearchParams({ product_filter: productFilter, sort });
    if (query) params.set("query", query);
    state.operations.products = await api(`/api/operations/catalog/products?${params}`);
    renderOperationsCatalog(query, productFilter, sort);
  } catch (error) { showToast(error.message, true); }
}

function renderOperationsCatalog(query, productFilter, sort) {
  const rows = state.operations.products.length
    ? state.operations.products.map((product) => `
      <button class="catalog-row catalog-product-row" data-product-id="${product.id}">
        ${product.primary_image_id ? `<img class="catalog-photo" data-image-id="${product.primary_image_id}" alt="${escapeHtml(product.title)}">` : '<span class="catalog-photo photo-placeholder">◎</span>'}
        <span><strong>${escapeHtml(product.title)}</strong><br><span class="muted small">${escapeHtml(product.skus.join(", ") || "Без SKU")}</span></span>
        <span class="catalog-counts"><strong>${formatMoney(product.economics.profit)}</strong><span>Операционный результат</span><span>Доход ${formatMoney(product.economics.revenue)}</span><span>${product.economics.rental_count} аренд</span><span class="${product.available_asset_count ? "available-text" : "muted"}">${product.available_asset_count} доступно</span>${product.needs_initial_price ? '<span class="chip warn">Нужно указать цену</span>' : ""}</span>
      </button>`).join("")
    : '<div class="empty">Товары не найдены</div>';
  root.innerHTML = `<div class="shell">
    ${topbar(true)}
    <p class="eyebrow">Каталог</p>
    <h1>Товары</h1>
    <form class="search-row" id="operations-product-search">
      <input name="query" value="${escapeHtml(query)}" placeholder="Название, SKU или штрихкод" autocomplete="off">
      <button class="button" type="submit">Найти</button>
    </form>
    <div class="filter-bar" data-product-filters>
      ${operationsFilterButton("all", "Все", productFilter)}
      ${operationsFilterButton("rental", "Для аренды", productFilter)}
      ${operationsFilterButton("available", "Есть доступные", productFilter)}
      ${operationsFilterButton("needs_price", "Нужно указать цену", productFilter)}
      ${operationsFilterButton("never_rented", "Не сдавался", productFilter)}
      ${operationsFilterButton("paid_back", "Окупился", productFilter)}
      ${operationsFilterButton("high_expenses", "Высокие расходы", productFilter)}
      ${operationsFilterButton("long_idle", "Давно не сдавался", productFilter)}
    </div>
    <div class="field sort-field"><label>Сортировка</label><select id="operations-product-sort">
      <option value="title" ${sort === "title" ? "selected" : ""}>По названию</option>
      <option value="revenue" ${sort === "revenue" ? "selected" : ""}>По доходу</option>
      <option value="rental_count" ${sort === "rental_count" ? "selected" : ""}>По количеству аренд</option>
      <option value="profit" ${sort === "profit" ? "selected" : ""}>По операционному результату</option>
      <option value="last_rental" ${sort === "last_rental" ? "selected" : ""}>По последней аренде</option>
    </select></div>
    <div class="session-list">${rows}</div>
  </div>`;
  bindTopbar();
  hydrateImages();
  document.querySelector("#operations-product-search").addEventListener("submit", (event) => {
    event.preventDefault();
    openOperationsCatalog(String(new FormData(event.currentTarget).get("query") || "").trim(), productFilter, sort);
  });
  document.querySelectorAll("[data-product-filter]").forEach((button) => {
    button.addEventListener("click", () => openOperationsCatalog(query, button.dataset.productFilter, sort));
  });
  document.querySelector("#operations-product-sort").addEventListener("change", (event) => openOperationsCatalog(query, productFilter, event.target.value));
  document.querySelectorAll("[data-product-id]").forEach((button) => {
    button.addEventListener("click", () => openOperationsProduct(button.dataset.productId));
  });
}

function operationsFilterButton(value, label, active) {
  return `<button class="filter-chip ${value === active ? "active" : ""}" data-product-filter="${value}">${label}</button>`;
}

async function openOperationsProduct(productId) {
  try {
    recordRoute("product", { productId });
    logicalParent = () => openOperationsCatalog();
    [state.operations.product, state.categories, state.operations.imageLinks] = await Promise.all([
      api(`/api/operations/catalog/products/${productId}`),
      api("/api/catalog/categories"),
      api("/api/media/image-links"),
    ]);
    await loadAqsiStates(state.operations.product.variants);
    renderOperationsProduct();
  } catch (error) { showToast(error.message, true); }
}

function renderOperationsProduct() {
  const product = state.operations.product;
  const categoryOptions = state.categories.map((category) => `<option value="${category.id}" ${category.id === product.category_id ? "selected" : ""}>${escapeHtml(category.title)}</option>`).join("");
  const variants = product.variants.length
    ? product.variants.map((variant) => `<article class="variant-commercial-card card">
        ${variant.primary_image_id ? `<img class="catalog-photo" data-image-id="${variant.primary_image_id}" alt="${escapeHtml(variant.title)}">` : '<span class="catalog-photo photo-placeholder">◎</span>'}
        <span class="variant-commercial-main"><strong>${escapeHtml(variant.title)}</strong><span class="muted small">${escapeHtml(variant.sku)}</span>${renderVariantBarcodes(variant)}
          <span class="commercial-block"><strong>Продажа</strong><span>Цена: ${variant.current_retail_price === null ? "не настроена" : formatMoney(variant.current_retail_price)}</span><button class="link-button" data-set-sale-price="${variant.id}">Изменить</button></span>
          <span class="commercial-block"><strong>Аренда</strong><span>Цена: ${variant.current_rental_price === null ? "не настроена" : formatMoney(variant.current_rental_price)}</span><span>Залог: ${variant.current_recommended_deposit === null ? "не указан" : formatMoney(variant.current_recommended_deposit)}</span><button class="link-button" data-set-rental-prices="${variant.id}">Изменить условия</button></span>
          ${renderAqsiState(variant)}
        </span>
        <span class="catalog-counts"><strong>На учёте ${formatQuantity(variant.physical_quantity)}</strong><span>Для продажи ${formatQuantity(variant.ordinary_quantity)}</span><span>Арендных экземпляров ${variant.rental_asset_count}</span><span class="available-text">Доступно сейчас ${variant.available_asset_count}</span><span>Выдано ${variant.rented_asset_count}</span></span>
        <span class="variant-actions">
          <button class="button secondary compact" data-edit-variant="${variant.id}">Редактировать</button>
          <button class="button ghost compact" data-add-manufacturer-barcode="${variant.id}">Добавить штрихкод</button>
          ${Number(variant.ordinary_quantity) > 0 ? `<button class="button secondary compact" data-allocate-rental="${variant.id}">Выделить в аренду</button>` : ""}
          ${state.user?.is_admin ? `<button class="button ghost compact" data-adjust-inventory="${variant.id}">Корректировка остатка</button>` : ""}
          ${variant.current_retail_price !== null && variant.primary_image_id ? `<button class="button ghost compact" data-open-label="${variant.id}">Открыть PDF</button><button class="button compact" data-print-label="${variant.id}">Печать</button>` : ""}
          ${renderAqsiAction(variant)}
        </span>
      </article>`).join("")
    : '<div class="empty">У товара пока нет вариантов</div>';
  const assets = product.rental_assets.length
    ? product.rental_assets.map(renderOperationsAssetRow).join("")
    : '<div class="empty">У товара нет предметов аренды</div>';
  root.innerHTML = `<div class="shell">
    ${topbar(true)}
    <p class="eyebrow">Карточка товара</p>
    <h1>${escapeHtml(product.title)}</h1>
    ${product.description ? `<p>${escapeHtml(product.description)}</p>` : '<p class="muted">Описание не заполнено.</p>'}
    <details class="card"><summary><strong>Управление товаром</strong></summary>
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
        <div class="field"><label>Атрибуты JSON <span class="muted">(необязательно)</span></label><textarea name="attributes" placeholder='{"color":"blue"}'></textarea></div>
        <button class="button full" type="submit">Создать вариант</button>
      </form>
    </details>
    <section class="card order-facts">
      <div><span class="muted small">SKU</span><strong>${escapeHtml(product.skus.join(", ") || "—")}</strong></div>
      <div><span class="muted small">Варианты</span><strong>${product.variant_count}</strong></div>
      <div><span class="muted small">Арендные единицы</span><strong>${product.rental_asset_count}</strong></div>
      <div><span class="muted small">Доступно</span><strong>${product.available_asset_count}</strong></div>
      <div><span class="muted small">Доход</span><strong>${formatMoney(product.economics.revenue)}</strong></div>
      <div><span class="muted small">Расходы</span><strong>${formatMoney(product.economics.expenses)}</strong></div>
      <div><span class="muted small">Операционный результат</span><strong>${formatMoney(product.economics.profit)}</strong></div>
      <div><span class="muted small">Аренд</span><strong>${product.economics.rental_count}</strong></div>
    </section>
    <div class="section-heading"><h2>Варианты</h2><span class="muted small">${product.variant_count}</span></div>
    <div class="session-list">${variants}</div>
    ${renderCatalogMedia(product)}
    <div class="section-heading"><h2>Предметы аренды</h2></div>
    <div class="session-list">${assets}</div>
  </div>`;
  bindTopbar();
  bindOperationsAssetRows();
  hydrateImages();
  document.querySelector("#catalog-product-form").addEventListener("submit", saveCatalogProduct);
  document.querySelector("#catalog-variant-create-form").addEventListener("submit", createCatalogVariant);
  document.querySelector("#catalog-media-form").addEventListener("submit", uploadCatalogImage);
  document.querySelectorAll("[data-edit-variant]").forEach((button) => button.addEventListener("click", () => editCatalogVariant(button.dataset.editVariant)));
  document.querySelectorAll("[data-add-manufacturer-barcode]").forEach((button) => button.addEventListener("click", () => addManufacturerBarcode(button.dataset.addManufacturerBarcode)));
  document.querySelectorAll("[data-set-sale-price]").forEach((button) => button.addEventListener("click", () => editCatalogSalePrice(button.dataset.setSalePrice)));
  document.querySelectorAll("[data-set-rental-prices]").forEach((button) => button.addEventListener("click", () => editCatalogRentalPrices(button.dataset.setRentalPrices)));
  document.querySelectorAll("[data-allocate-rental]").forEach((button) => button.addEventListener("click", () => allocateRental(button.dataset.allocateRental)));
  document.querySelectorAll("[data-adjust-inventory]").forEach((button) => button.addEventListener("click", () => adjustInventory(button.dataset.adjustInventory)));
  document.querySelectorAll("[data-publish-aqsi]").forEach((button) => button.addEventListener("click", () => publishCatalogVariant(button.dataset.publishAqsi)));
  document.querySelectorAll("[data-verify-aqsi]").forEach((button) => button.addEventListener("click", () => verifyCatalogVariant(button.dataset.verifyAqsi)));
  document.querySelectorAll("[data-primary-link]").forEach((button) => button.addEventListener("click", () => selectCatalogPrimary(button.dataset.primaryLink)));
  document.querySelectorAll("[data-delete-link]").forEach((button) => button.addEventListener("click", () => deleteCatalogImageLink(button.dataset.deleteLink)));
  document.querySelectorAll("[data-open-label]").forEach((button) => button.addEventListener("click", () => openVariantLabel(button.dataset.openLabel, false)));
  document.querySelectorAll("[data-print-label]").forEach((button) => button.addEventListener("click", () => openVariantLabel(button.dataset.printLabel, true)));
}

async function openVariantLabel(variantId, print) {
  const previous = localStorage.getItem("core.label-profile") || "40x30";
  const profile = await selectLabelProfile(previous, print);
  if (profile === null) return;
  localStorage.setItem("core.label-profile", profile);
  openAuthenticatedFile(`/api/labels/variants/${variantId}/${profile}.pdf?dpi=203`, print);
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
  const manufacturer = (variant.barcodes || [])
    .filter((barcode) => barcode.source === "manufacturer" && /^\d{4,22}$/.test(barcode.value))
    .map((barcode) => barcode.value)
    .sort();
  return manufacturer[0] || variant.barcode;
}

function renderVariantBarcodes(variant) {
  const values = variant.barcodes?.length
    ? variant.barcodes
    : [{ value: variant.barcode, source: "internal" }];
  return `<span class="commercial-block"><strong>Штрихкоды</strong>${values.map((barcode) => `<span><span class="barcode-value">${escapeHtml(barcode.value)}</span> <span class="muted small">${barcode.source === "manufacturer" ? "Производитель" : "Core"}</span></span>`).join("")}</span>`;
}

async function addManufacturerBarcode(variantId) {
  const value = window.prompt("Штрихкод производителя");
  if (value === null || !value.trim()) return;
  try {
    await api(`/api/catalog/variants/${variantId}/barcodes`, {
      method: "POST",
      body: JSON.stringify({ value, source: "manufacturer" }),
    });
    await openOperationsProduct(state.operations.product.id);
    showToast("Штрихкод производителя зарегистрирован");
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

function renderCatalogMedia(product) {
  const entityIds = new Set([product.id, ...product.variants.map((variant) => variant.id)]);
  const links = state.operations.imageLinks.filter((link) => entityIds.has(link.entity_id));
  const targetOptions = [`<option value="catalog_product:${product.id}">Товар целиком</option>`, ...product.variants.map((variant) => `<option value="catalog_variant:${variant.id}">Вариант: ${escapeHtml(variant.title)}</option>`)].join("");
  const gallery = links.length ? links.map((link) => `<figure class="catalog-media-item">
    <img data-image-id="${link.image_id}" alt="Фото товара">
    <figcaption><span class="chip ${link.role === "primary" ? "good" : ""}">${link.role === "primary" ? "Основное" : "Галерея"}</span>
      ${link.role !== "primary" ? `<button class="link-button" data-primary-link="${link.id}">Сделать основным</button>` : ""}
      <button class="link-button danger-text" data-delete-link="${link.id}">Отвязать</button></figcaption>
  </figure>`).join("") : '<div class="empty">Фотографий пока нет</div>';
  return `<section class="card"><div class="section-heading"><h2>Фотографии</h2><span class="muted small">${links.length}</span></div>
    <div class="catalog-media-grid">${gallery}</div>
    <form id="catalog-media-form"><div class="field"><label>К чему относится фото</label><select name="target">${targetOptions}</select></div><div class="field"><label>Новое фото</label><input name="file" type="file" accept="image/*" capture="environment" required></div><button class="button full" type="submit">Добавить фото</button></form>
  </section>`;
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

function parseAttributes(value) {
  const normalized = String(value || "").trim();
  if (!normalized) return {};
  const parsed = JSON.parse(normalized);
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("Атрибуты должны быть JSON-объектом");
  return parsed;
}

async function createCatalogVariant(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  try {
    await api("/api/catalog/variants", { method: "POST", body: JSON.stringify({ product_id: state.operations.product.id, title: data.get("title"), manufacturer_barcode: nullableText(data.get("manufacturer_barcode")), attributes: parseAttributes(data.get("attributes")), is_active: true }) });
    await openOperationsProduct(state.operations.product.id);
    showToast("Вариант создан");
  } catch (error) { showToast(error.message, true); }
}

async function editCatalogVariant(variantId) {
  const variant = state.operations.product.variants.find((item) => item.id === variantId);
  const title = window.prompt("Название варианта", variant.title);
  if (title === null) return;
  const attributes = window.prompt("Атрибуты JSON", JSON.stringify(variant.attributes));
  if (attributes === null) return;
  try {
    await api(`/api/catalog/variants/${variantId}`, { method: "PATCH", body: JSON.stringify({ title, attributes: parseAttributes(attributes) }) });
    await openOperationsProduct(state.operations.product.id);
    showToast("Вариант сохранён");
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

async function adjustInventory(variantId) {
  const quantityDelta = window.prompt("Изменение остатка (например, -1 или 2)", "-1");
  if (quantityDelta === null) return;
  const reason = window.prompt("Причина: shortage, damage, gift, personal_use, stocktake, other", "stocktake");
  if (reason === null) return;
  const comment = window.prompt("Комментарий", "");
  if (comment === null) return;
  try {
    await api(`/api/operations/catalog/variants/${variantId}/inventory-adjustments`, { method: "POST", body: JSON.stringify({ quantity_delta: quantityDelta, reason, comment: nullableText(comment) }) });
    await openOperationsProduct(state.operations.product.id);
    showToast("Корректировка записана в складской ledger");
  } catch (error) { showToast(error.message, true); }
}

async function uploadCatalogImage(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  const [entityType, entityId] = String(data.get("target")).split(":");
  const links = state.operations.imageLinks.filter((link) => link.entity_type === entityType && link.entity_id === entityId);
  const upload = new FormData();
  upload.set("file", data.get("file"));
  try {
    const image = await api("/api/media/images/upload", { method: "POST", body: upload });
    await api("/api/media/image-links", { method: "POST", body: JSON.stringify({ image_id: image.id, entity_type: entityType, entity_id: entityId, role: links.length ? "gallery" : "primary", sort_order: links.length }) });
    await openOperationsProduct(state.operations.product.id);
    showToast("Фото добавлено");
  } catch (error) { showToast(error.message, true); }
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
