const token = sessionStorage.getItem("core.token");
const selected = new Map();
const pendingRequestId = crypto.randomUUID();
let directRequestId = crypto.randomUUID();
let pendingReference = sessionStorage.getItem("core.aqsi-spike.reference");
let directReference = sessionStorage.getItem("core.aqsi-direct.reference");
let submissionLocked = Boolean(pendingReference || directReference);
let directPolling = false;
let lastDirectResult = null;
let searching = 0;

const money = value => new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB" }).format(Number(value));
const escapeHtml = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.detail === "string" ? body.detail : body.detail?.message || `Ошибка ${response.status}`;
    throw new Error(detail);
  }
  return body;
}

function renderSearch(items, hasMore) {
  const target = document.querySelector("#search-results");
  target.innerHTML = items.length ? items.map(item => `<article class="catalog-search-result"><div><strong>${escapeHtml(item.product_title)}</strong><div>${escapeHtml(item.title)}</div><div class="muted small">${escapeHtml(item.sku)} · ${escapeHtml(item.barcode)} · Core: ${item.retail_price == null ? "нет цены" : money(item.retail_price)}</div></div><button class="button secondary" data-add="${item.id}" type="button" ${selected.has(item.id) || selected.size >= 3 ? "disabled" : ""}>Выбрать</button></article>`).join("") : '<div class="empty">Ничего не найдено</div>';
  if (hasMore) target.insertAdjacentHTML("beforeend", '<p class="muted small">Найдено много вариантов. Уточните запрос.</p>');
  target.querySelectorAll("[data-add]").forEach(button => button.addEventListener("click", () => {
    const item = items.find(value => value.id === button.dataset.add);
    if (!item || selected.size >= 3) return;
    selected.set(item.id, { ...item, quantity: 1, test_price: "10.00" });
    renderSelected();
    renderSearch(items, hasMore);
  }));
}

async function search(event) {
  event.preventDefault();
  const query = document.querySelector("#query").value.trim();
  if (!query) return;
  const current = ++searching;
  try {
    const page = await api(`/api/catalog/search/variants?query=${encodeURIComponent(query)}&limit=12`);
    if (current === searching) renderSearch(page.items, page.has_more);
  } catch (error) { document.querySelector("#search-results").textContent = error.message; }
}

function priceKopecks(value) {
  const match = String(value).trim().match(/^(\d+)(?:[.,](\d{0,2}))?$/);
  if (!match) return 0;
  return Number(match[1]) * 100 + Number((match[2] || "").padEnd(2, "0"));
}

function exactTotal() {
  let kopecks = 0;
  selected.forEach(line => { kopecks += priceKopecks(line.test_price) * Number.parseInt(line.quantity, 10); });
  return kopecks;
}

function renderSelected() {
  const target = document.querySelector("#selected");
  target.innerHTML = selected.size ? [...selected.values()].map(line => `<article class="spike-line" data-line="${line.id}"><div><strong>${escapeHtml(line.product_title)}</strong><div>${escapeHtml(line.title)} · ${escapeHtml(line.sku)} · ${escapeHtml(line.barcode)}</div><div class="muted small">Core price: ${line.retail_price == null ? "нет цены" : money(line.retail_price)}</div></div><div class="spike-line-controls"><label>Test price, ₽<input data-price type="number" min="0.01" step="0.01" value="${line.test_price}"></label><label>Qty<input data-quantity type="number" min="1" max="100" step="1" value="${line.quantity}"></label><button class="button ghost danger-text" data-remove type="button">Удалить</button></div></article>`).join("") : '<div class="empty">Выберите 2–3 реальных товара</div>';
  target.querySelectorAll("[data-line]").forEach(card => {
    const line = selected.get(card.dataset.line);
    card.querySelector("[data-price]").addEventListener("input", event => { line.test_price = event.target.value; updateTotal(); });
    card.querySelector("[data-quantity]").addEventListener("input", event => { line.quantity = event.target.value; updateTotal(); });
    card.querySelector("[data-remove]").addEventListener("click", () => { selected.delete(line.id); renderSelected(); });
  });
  updateTotal();
}

function updateTotal() {
  document.querySelector("#total").textContent = money(exactTotal() / 100);
  const disabled = submissionLocked || !document.querySelector("#confirm").checked || !selected.size;
  document.querySelector("#pending-send").disabled = disabled;
  document.querySelector("#direct-send").disabled = disabled;
}

function showPendingReference() {
  document.querySelector("#pending-result").classList.remove("hidden");
  document.querySelector("#pending-result-summary").textContent = `Reference: ${pendingReference}. Проверьте статус перед дальнейшими действиями.`;
}

function basketLines() {
  return [...selected.values()].map(line => ({ variant_id: line.id, quantity: Number(line.quantity), test_price: line.test_price }));
}

async function sendPending() {
  const button = document.querySelector("#pending-send");
  if (button.disabled || !window.confirm(`Создать РЕАЛЬНЫЙ отложенный заказ AQSI на ${money(exactTotal() / 100)}?`)) return;
  submissionLocked = true;
  pendingReference = `CORE-SPIKE-${pendingRequestId.replaceAll("-", "").slice(0, 20)}`;
  sessionStorage.setItem("core.aqsi-spike.reference", pendingReference);
  showPendingReference();
  button.disabled = true;
  document.querySelector("#direct-send").disabled = true;
  document.querySelector("#error").textContent = "";
  try {
    const result = await api("/api/dev/aqsi-sale-spike/orders", {
      method: "POST",
      body: JSON.stringify({ request_id: pendingRequestId, confirm_real_operation: true, lines: basketLines() }),
    });
    pendingReference = result.core_reference;
    document.querySelector("#pending-result-summary").innerHTML = `<p><strong>Core:</strong> ${escapeHtml(result.core_reference)}<br><strong>AQSI:</strong> ${escapeHtml(result.aqsi_reference || "reference не возвращён")}<br><strong>Статус:</strong> ${escapeHtml(result.aqsi_status)}<br><strong>Device:</strong> ${escapeHtml(result.device_id)}<br><strong>Ожидаемый итог:</strong> ${money(result.expected_total)}</p>`;
    document.querySelector("#pending-result").classList.remove("hidden");
    button.textContent = "ЗАКАЗ УЖЕ ОТПРАВЛЕН";
  } catch (error) {
    document.querySelector("#error").textContent = `${error.message} Не повторяйте отправку: сначала проверьте заказ в AQSI.`;
    button.textContent = "РЕЗУЛЬТАТ НЕИЗВЕСТЕН — НЕ ПОВТОРЯТЬ";
  }
}

async function checkStatus() {
  if (!pendingReference) return;
  try { document.querySelector("#pending-status-output").textContent = JSON.stringify(await api(`/api/dev/aqsi-sale-spike/orders/${encodeURIComponent(pendingReference)}`), null, 2); }
  catch (error) { document.querySelector("#pending-status-output").textContent = error.message; }
}

function renderDirect(result) {
  lastDirectResult = result;
  const target = document.querySelector("#direct-result-summary");
  const critical = ["payment_success_fiscalization_failed", "payment_success_fiscalization_unknown"].includes(result.state);
  target.className = critical ? "critical-result" : "";
  target.innerHTML = `<p>${escapeHtml(result.message)}</p><p><strong>Core:</strong> ${escapeHtml(result.core_reference)}<br><strong>Сумма:</strong> ${money(result.expected_total)}<br><strong>Device:</strong> ${escapeHtml(result.device_id)}<br><strong>Оплата:</strong> ${escapeHtml(result.purchase_status || "запуск/статус неизвестен")}<br><strong>Purchase operation:</strong> ${escapeHtml(result.purchase_operation_id || "не получен")}<br><strong>Slip:</strong> ${escapeHtml(result.slip_reference || "—")}<br><strong>Чек:</strong> ${escapeHtml(result.receipt_status || "не запущен")}<br><strong>Receipt operation:</strong> ${escapeHtml(result.receipt_operation_id || "—")}</p>${result.aqsi_problem ? `<p>Ответ AQSI: ${escapeHtml(result.aqsi_problem)}</p>` : ""}`;
  document.querySelector("#direct-result").classList.remove("hidden");
  document.querySelector("#direct-resume").classList.toggle("hidden", !["unknown", "payment_success_fiscalization_unknown"].includes(result.state));
  document.querySelector("#direct-new").classList.toggle("hidden", result.state !== "payment_failed");
}

function unknownDirectResult(problem) {
  const paymentKnown = Boolean(lastDirectResult?.slip_reference) || String(lastDirectResult?.state || "").startsWith("payment_success");
  return {
    ...(lastDirectResult || {}),
    core_reference: directReference,
    state: paymentKnown ? "payment_success_fiscalization_unknown" : "unknown",
    message: paymentKnown ? "Оплата прошла, статус фискализации неизвестен. Не повторяйте оплату." : "Статус оплаты неизвестен. Не повторяйте оплату. Проверьте операцию AQSI.",
    expected_total: lastDirectResult?.expected_total ?? exactTotal() / 100,
    device_id: lastDirectResult?.device_id ?? "—",
    aqsi_problem: problem,
  };
}

async function pollDirect() {
  if (!directReference || directPolling) return;
  directPolling = true;
  document.querySelector("#direct-resume").classList.add("hidden");
  const deadline = Date.now() + 180000;
  try {
    while (Date.now() < deadline) {
      const result = await api(`/api/dev/aqsi-sale-spike/direct/${encodeURIComponent(directReference)}/progress`, { method: "POST" });
      renderDirect(result);
      if (!["payment_in_progress", "payment_success_fiscalization_pending"].includes(result.state)) return;
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    renderDirect(unknownDirectResult("Истекло время ожидания в интерфейсе Core."));
  } catch (error) {
    renderDirect(unknownDirectResult(error.message));
  } finally { directPolling = false; }
}

function openDirectConfirmation() {
  if (document.querySelector("#direct-send").disabled) return;
  document.querySelector("#direct-confirm-total").textContent = money(exactTotal() / 100);
  document.querySelector("#direct-confirmation").showModal();
}

async function sendDirect() {
  document.querySelector("#direct-confirmation").close();
  if (submissionLocked) return;
  submissionLocked = true;
  directReference = `CORE-DIRECT-${directRequestId.replaceAll("-", "").slice(0, 20)}`;
  sessionStorage.setItem("core.aqsi-direct.reference", directReference);
  document.querySelector("#pending-send").disabled = true;
  document.querySelector("#direct-send").disabled = true;
  document.querySelector("#direct-result").classList.remove("hidden");
  document.querySelector("#direct-result-summary").textContent = `Запускаем реальную оплату ${money(exactTotal() / 100)}. Не нажимайте повторно.`;
  try {
    const result = await api("/api/dev/aqsi-sale-spike/direct", { method: "POST", body: JSON.stringify({ request_id: directRequestId, confirm_real_operation: true, lines: basketLines() }) });
    directReference = result.core_reference;
    sessionStorage.setItem("core.aqsi-direct.reference", directReference);
    renderDirect(result);
    if (["payment_in_progress", "payment_success_fiscalization_pending"].includes(result.state)) await pollDirect();
  } catch (error) {
    renderDirect(unknownDirectResult(error.message));
  }
}

function resetFailedDirectAttempt() {
  sessionStorage.removeItem("core.aqsi-direct.reference");
  directReference = null;
  directRequestId = crypto.randomUUID();
  submissionLocked = Boolean(pendingReference);
  document.querySelector("#direct-result").classList.add("hidden");
  updateTotal();
}

if (!token) document.querySelector("#error").innerHTML = 'Сначала <a href="/app">войдите в Core</a>.';
document.querySelector("#search-form").addEventListener("submit", search);
document.querySelector("#confirm").addEventListener("change", updateTotal);
document.querySelector("#pending-send").addEventListener("click", sendPending);
document.querySelector("#pending-status").addEventListener("click", checkStatus);
document.querySelector("#direct-send").addEventListener("click", openDirectConfirmation);
document.querySelector("#direct-cancel").addEventListener("click", () => document.querySelector("#direct-confirmation").close());
document.querySelector("#direct-confirm").addEventListener("click", sendDirect);
document.querySelector("#direct-resume").addEventListener("click", pollDirect);
document.querySelector("#direct-new").addEventListener("click", resetFailedDirectAttempt);
renderSelected();
if (pendingReference) showPendingReference();
if (directReference) {
  document.querySelector("#direct-result").classList.remove("hidden");
  document.querySelector("#direct-result-summary").textContent = `Найдена незавершённая операция ${directReference}. Не начинайте новую оплату.`;
  document.querySelector("#direct-resume").classList.remove("hidden");
}
