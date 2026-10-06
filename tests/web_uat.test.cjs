// No frontend dependencies/build: exercise the shipped script with DOM/API doubles.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const source = readFileSync('src/core/web/static/app.js', 'utf8')
  .replace('if (state.token) bootstrap(); else renderLogin();', '');
const styles = readFileSync('src/core/web/static/styles.css', 'utf8');

function setup() {
  const timers = new Map();
  let sequence = 0;
  const status = { textContent: 'Сохранено' };
  const retry = { classList: { add() {}, remove() {} }, addEventListener() {} };
  const inputs = Object.fromEntries(['category_id', 'product_title', 'product_description'].map(name => [name, {
    value: '', handlers: {}, addEventListener(event, fn) { this.handlers[event] = fn; },
  }]));
  const form = {
    dataset: { productForm: 'draft' }, addEventListener() {},
    querySelector: selector => selector === '[data-save-status]' ? status : retry,
    querySelectorAll: selector => selector.startsWith('select') ? [inputs.category_id] : [inputs.product_title, inputs.product_description],
  };
  const context = vm.createContext({
    document: { querySelector() {}, querySelectorAll: () => [form] },
    window: { addEventListener() {} }, sessionStorage: { getItem() {} },
    URLSearchParams,
    setTimeout(fn, ms) { const id = ++sequence; timers.set(id, { fn, ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
    FormData: class { constructor() {} get(key) { return inputs[key].value; } },
  });
  vm.runInContext(source, context);
  vm.runInContext('state.session = { id: "session", items: [{id:"draft"}] };', context);
  const requests = [];
  context.api = async (url, options) => { const body = JSON.parse(options.body); requests.push({url, body}); return {id:'draft', ...body}; };
  context.bindProductAutosave(form);
  return { context, form, inputs, status, timers, requests };
}

test('Product text debounces, category saves immediately, PATCH includes only Product fields', async () => {
  const s = setup();
  s.inputs.product_title.value = 'Cola';
  s.inputs.product_title.handlers.input();
  s.inputs.product_description.value = 'Description';
  s.inputs.product_description.handlers.input();
  assert.equal(s.timers.size, 1);
  assert.equal([...s.timers.values()][0].ms, 600);
  assert.equal(s.requests.length, 0);
  s.inputs.category_id.value = 'category';
  s.inputs.category_id.handlers.change();
  assert.equal([...s.timers.values()][0].ms, 0);
  await s.context.persistProductForm(s.form);
  assert.deepEqual(s.requests[0].body, { category_id:'category', product_title:'Cola', product_description:'Description' });
  assert.equal(s.status.textContent, 'Сохранено');
});

test('Failure preserves input, never reports saved, retry succeeds', async () => {
  const s = setup();
  const api = s.context.api;
  s.context.api = async () => { throw new Error('offline'); };
  s.inputs.product_title.value = 'Unsaved';
  s.inputs.product_title.handlers.input();
  await assert.rejects(s.context.persistProductForm(s.form), /offline/);
  assert.match(s.status.textContent, /Не удалось сохранить/);
  assert.equal(s.inputs.product_title.value, 'Unsaved');
  s.context.api = api;
  await s.context.persistProductForm(s.form);
  assert.equal(s.status.textContent, 'Сохранено');
});

test('Autosave updates backend readiness hints without replacing Variant inputs', async () => {
  const s = setup();
  const chips = { innerHTML: 'Выберите категорию · Введите наименование товара' };
  const selectors = [];
  s.context.document.querySelector = selector => {
    selectors.push(selector);
    return selector === '[data-item-requirements="draft"]' ? chips : null;
  };
  s.context.api = async () => ({id:'draft', missing_requirements:['missing_quantity']});
  s.inputs.product_title.value = 'Saved product';
  s.inputs.product_title.handlers.input();
  await s.context.persistProductForm(s.form);
  assert.doesNotMatch(chips.innerHTML, /Выберите категорию|Введите наименование товара/);
  assert.match(chips.innerHTML, /Количество|количество/);
  assert.deepEqual(selectors, ['[data-item-requirements="draft"]']);
  s.context.api = async () => { throw new Error('offline'); };
  s.inputs.product_title.handlers.input();
  const previous = chips.innerHTML;
  await assert.rejects(s.context.persistProductForm(s.form));
  assert.equal(chips.innerHTML, previous);
  s.context.api = async () => ({id:'draft', missing_requirements:[]});
  await s.context.persistProductForm(s.form);
  assert.match(chips.innerHTML, /Позиция заполнена/);
  s.context.api = async () => ({id:'draft', missing_requirements:['missing_product_title']});
  s.inputs.product_title.value = '';
  s.inputs.product_title.handlers.input();
  await s.context.persistProductForm(s.form);
  assert.match(chips.innerHTML, /наименование|название/i);
});

test('Input during an in-flight save is serialized and latest value wins', async () => {
  const s = setup();
  let release;
  const api = s.context.api;
  s.context.api = async (...args) => { const result = await api(...args); if (s.requests.length === 1) await new Promise(resolve => { release = resolve; }); return result; };
  s.inputs.product_title.value = 'First';
  s.inputs.product_title.handlers.input();
  const saving = s.context.persistProductForm(s.form);
  await Promise.resolve();
  s.inputs.product_title.value = 'Latest';
  s.inputs.product_title.handlers.input();
  assert.notEqual(s.status.textContent, 'Сохранено');
  release();
  await saving;
  assert.equal(s.requests.length, 2);
  assert.equal(s.requests[1].body.product_title, 'Latest');
  assert.equal(s.status.textContent, 'Сохранено');
});

test('Contextual Media targets, camera/gallery, fallback and own photo', () => {
  const s = setup();
  vm.runInContext('state.operations.imageLinks = [{id:"p-link", entity_type:"catalog_product", entity_id:"p", image_id:"photo-p", role:"primary"}];', s.context);
  const product = {id:'p'};
  const fallback = s.context.renderCatalogMedia('catalog_variant', 'a', product);
  assert.match(fallback, /Используется общее фото товара/);
  assert.match(fallback, /data-image-id="photo-p"/);
  assert.match(fallback, /data-media-entity="a"/);
  assert.doesNotMatch(fallback, /К чему относится фото/);
  const fileInputs = fallback.match(/<input[^>]+>/g);
  assert.match(fileInputs[0], /capture="environment"/);
  assert.doesNotMatch(fileInputs[1], /capture/);
  vm.runInContext('state.operations.imageLinks.push({id:"a-link", entity_type:"catalog_variant", entity_id:"a", image_id:"photo-a", role:"primary"});', s.context);
  const own = s.context.renderCatalogMedia('catalog_variant', 'a', product);
  assert.match(own, /data-image-id="photo-a"/);
  assert.doesNotMatch(own, /Используется общее фото/);
  assert.match(s.context.renderCatalogMedia('catalog_variant', 'b', product), /Используется общее фото/);
  assert.match(own, /data-delete-link="a-link"/);
});

test('Variant retains explicit save, Intake Product has no save button', () => {
  assert.match(source, /Сохранить позицию/);
  const productTemplate = source.slice(source.indexOf('function renderProductGroup'), source.indexOf('function renderActionPanel'));
  assert.doesNotMatch(productTemplate, /Сохранить товар/);
});

test('Intake uses bounded server-side Catalog search for both picker projections', async () => {
  const s = setup();
  const variantResults = { innerHTML: '' };
  const productResults = { innerHTML: '' };
  s.context.document.querySelector = selector => ({
    '#variant-search-results': variantResults,
    '#product-search-results': productResults,
  })[selector] || null;
  s.context.document.querySelectorAll = () => [];
  const calls = [];
  s.context.api = async url => {
    calls.push(url);
    if (url.includes('/variants')) return {items:[{id:'v', product_id:'p', product_title:'Ручка', title:'Синяя', sku:'SKU-1', barcode:'4601', retail_price:'100.00'}], has_more:false};
    return {items:[{id:'p', title:'Ручка', variant_count:1, matched_variant_title:null, matched_sku:null, matched_barcode:null}], has_more:false};
  };

  await s.context.runCatalogSearch('variant', ' ручка син ', true);
  await s.context.runCatalogSearch('product', 'SKU-1', true);

  assert.equal(calls[0], '/api/catalog/search/variants?query=%D1%80%D1%83%D1%87%D0%BA%D0%B0%20%D1%81%D0%B8%D0%BD&limit=12');
  assert.equal(calls[1], '/api/catalog/search/products?query=SKU-1&limit=12');
  assert.match(variantResults.innerHTML, /Ручка/);
  assert.match(productResults.innerHTML, /1 вариант/);
  const actionPanel = source.slice(
    source.indexOf('function renderActionPanel'),
    source.indexOf('function meaningfulVariantSuffix'),
  );
  assert.doesNotMatch(actionPanel, /<select name="product_id"|<datalist/);
  assert.match(actionPanel, /Название, SKU или штрихкод/);
});

test('Uploads associate with their contextual Product or Variant, cancellation is a no-op', async () => {
  const s = setup();
  const calls = [];
  s.context.FormData = class { set() {} };
  s.context.showToast = () => {};
  s.context.api = async (url, options) => { calls.push({url, body: options.body}); return {id:'uploaded'}; };
  vm.runInContext('state.operations.product = {id:"p"};', s.context);
  for (const [type, id] of [['catalog_product', 'p'], ['catalog_variant', 'a'], ['catalog_variant', 'b']]) {
    const input = { files:[{}], dataset:{mediaUpload:type, mediaEntity:id},
      closest: () => ({querySelectorAll: () => []}), isConnected:false };
    await s.context.uploadCatalogImage(input);
    const link = JSON.parse(calls.at(-1).body);
    assert.equal(link.entity_type, type);
    assert.equal(link.entity_id, id);
    assert.equal(link.image_id, 'uploaded');
    assert.equal(link.role, 'primary');
  }
  const count = calls.length;
  await s.context.uploadCatalogImage({ files:[] });
  assert.equal(calls.length, count);
});

test('Catalog shell defaults to Sale and serializes reproducible server query state', () => {
  const s = setup();
  const defaults = s.context.normalizeCatalogState({});
  assert.equal(defaults.mode, 'sale');
  assert.equal(defaults.status, 'active');
  assert.equal(defaults.sort, 'title');
  assert.equal(defaults.attention.length, 0);

  const params = s.context.catalogUrlParams({
    mode: 'rental', status: 'archived', query: 'SKU-1', categoryId: 'category', supplierId: 'supplier',
    attention: ['missing_photo', 'out_of_stock'], productFilter: 'available', sort: 'last_rental',
  });
  assert.equal(params.get('mode'), 'rental');
  assert.equal(params.get('status'), 'archived');
  assert.equal(params.get('query'), 'SKU-1');
  assert.equal(params.get('category_id'), 'category');
  assert.equal(params.get('supplier_id'), 'supplier');
  assert.deepEqual(params.getAll('attention'), ['missing_photo', 'out_of_stock']);
  assert.equal(params.get('product_filter'), 'available');
  assert.equal(params.get('sort'), 'last_rental');
});

test('Catalog category selector renders hierarchy and selected state', () => {
  const s = setup();
  vm.runInContext(`state.categories = [
    {id:'root', title:'Канцелярия', parent_id:null, is_active:true},
    {id:'child', title:'Ручки', parent_id:'root', is_active:true},
    {id:'hidden', title:'Скрытая', parent_id:null, is_active:false}
  ];`, s.context);
  const html = s.context.renderCatalogCategoryNavigation(
    s.context.normalizeCatalogState({categoryId:'child'}),
  );
  assert.match(html, /Канцелярия/);
  assert.match(html, /Ручки/);
  assert.doesNotMatch(html, /Скрытая/);
  assert.match(html, /category-link active[^>]+data-category-id="child"/);
  assert.match(html, /--category-depth:1/);
});

test('Catalog mobile drawers use native modal open and explicit scroll lock', () => {
  const s = setup();
  let opened = false;
  let locked = false;
  s.context.document = {
    body: {classList: {add(value) { locked = value === 'catalog-drawer-open'; }}},
    querySelector: selector => selector === '#drawer' ? {showModal() { opened = true; }} : null,
  };
  s.context.openCatalogDrawer('#drawer');
  assert.equal(opened, true);
  assert.equal(locked, true);
  assert.match(source, /dialog\.addEventListener\("close"/);
  assert.match(source, /catalog-filters-reset/);
  assert.match(source, /data\.getAll\("attention"\)/);
  assert.match(source, /data\.get\("status"\)/);
});

test('Catalog desktop and mobile filters expose archive status selection', () => {
  const s = setup();
  vm.runInContext('state.suppliers = [];', s.context);
  const catalog = s.context.normalizeCatalogState({status:'archived'});
  const desktop = s.context.renderCatalogFilters(catalog, false);
  const mobile = s.context.renderCatalogFilters(catalog, true);

  for (const html of [desktop, mobile]) {
    assert.match(html, /<legend>Статус<\/legend>/);
    assert.match(html, /value="active"[\s\S]*Активные/);
    assert.match(html, /value="archived"[^>]*checked[\s\S]*Архивные/);
    assert.match(html, /value="all"[\s\S]*Все/);
  }
  assert.match(desktop, /data-desktop-status/);
  assert.doesNotMatch(mobile, /data-desktop-status/);
});

test('Archived Product and Variant reuse Catalog cards with visible markers', () => {
  const s = setup();
  const product = {
    id:'archived-product', title:'Старый товар', category_label:'Архив', is_archived:true,
    primary_image_id:null, card_variants:[{
      id:'archived-variant', title:'Вариант', sku:'SKU-ARCHIVED', is_archived:true,
      current_retail_price:'100.00', current_rental_price:null, sale_quantity:'1',
      rental_asset_count:0, sale_row_visible:true, rental_row_visible:false,
      aqsi_status:null, aqsi_is_current:false,
    }], rental_economics_applicable:false,
  };
  const html = s.context.renderCatalogProductCard(product, {mode:'all', status:'archived'});

  assert.match(html, /catalog-product-card archived/);
  assert.match(html, />АРХИВ</);
  assert.match(html, /АРХИВНЫЙ ВАРИАНТ/);
  assert.doesNotMatch(html, /data-change-product-category/);
  assert.doesNotMatch(source, /data-restore-(?:product|variant)/);
});

test('Explicit TEST Products are marked and expose a separate confirmed admin purge', () => {
  const s = setup();
  const product = {
    id:'test-product', title:'UAT fixture', category_label:'Тесты', is_test:true,
    is_archived:false, primary_image_id:null, card_variants:[],
    rental_economics_applicable:false,
  };
  const html = s.context.renderCatalogProductCard(product, {mode:'all', status:'active'});

  assert.match(html, />ТЕСТ</);
  assert.match(source, /data-purge-test-product/);
  assert.match(source, /data-classify-test-product/);
  assert.match(source, /test-data-preflight/);
  assert.match(source, /classify-test-data/);
  assert.match(source, /purge-test-data/);
  assert.match(source, /JSON\.stringify\(\{ confirm: true \}\)/);
  assert.match(source, /Эти данные не попадут в архив/);
  assert.match(source, /Удалить тест полностью/);
});

test('Catalog correction keeps search and results in one main column without SQLAdmin', () => {
  const template = source.slice(
    source.indexOf('function renderOperationsCatalog'),
    source.indexOf('function catalogModeButton'),
  );
  const mainColumn = template.slice(template.indexOf('<div class="catalog-main">'));

  assert.ok(mainColumn.indexOf('catalog-modes') < mainColumn.indexOf('catalog-search'));
  assert.ok(mainColumn.indexOf('catalog-search') < mainColumn.indexOf('catalog-results-head'));
  assert.ok(mainColumn.indexOf('catalog-results-head') < mainColumn.indexOf('session-list'));
  assert.doesNotMatch(source, /\/admin\/category\/list/);
});

test('Native Category create form exposes only title and optional parent', () => {
  const s = setup();
  vm.runInContext(`state.categories = [
    {id:'root', title:'Фото', parent_id:null, is_active:true},
    {id:'child', title:'Объективы', parent_id:'root', is_active:true}
  ];`, s.context);

  const html = s.context.renderCategoryCreateDialog();

  assert.match(html, /name="title"/);
  assert.match(html, /name="parent_id"/);
  assert.match(html, /Фото/);
  assert.match(html, /— Объективы/);
  assert.doesNotMatch(html, /name="slug"/);
  assert.match(source, /\/api\/catalog\/categories\/quick/);
});

test('Catalog Product cards separate Sale and Rental facts without false warnings', () => {
  const s = setup();
  const rentalOnly = {
    id:'rental', title:'Karcher', sku:'SKU-RENT', current_retail_price:null,
    current_rental_price:null, sale_quantity:'0', rental_asset_count:1,
    sale_row_visible:false, rental_row_visible:true, aqsi_status:null, aqsi_is_current:false,
  };
  const saleOnly = {
    id:'sale', title:'Ручка синяя', sku:'SKU-SALE', current_retail_price:null,
    current_rental_price:null, sale_quantity:'24', rental_asset_count:0,
    sale_row_visible:true, rental_row_visible:false, aqsi_status:null, aqsi_is_current:false,
  };
  const mixed = {
    id:'mixed', title:'Конструктор Цветы', sku:'SKU-MIXED', current_retail_price:'590.00',
    current_rental_price:'190.00', sale_quantity:'6', rental_asset_count:2,
    sale_row_visible:true, rental_row_visible:true, aqsi_status:'published', aqsi_is_current:true,
  };

  const rentalHtml = s.context.renderCatalogVariantRow(rentalOnly, 'all');
  const saleHtml = s.context.renderCatalogVariantRow(saleOnly, 'all');
  const mixedHtml = s.context.renderCatalogVariantRow(mixed, 'all');

  assert.match(rentalHtml, /Аренда/);
  assert.match(rentalHtml, /1 экз\./);
  assert.match(rentalHtml, /Цена аренды не указана/);
  assert.doesNotMatch(rentalHtml, /Цена не указана/);
  assert.doesNotMatch(rentalHtml, /AQSI|Не опубликовано/);
  assert.doesNotMatch(rentalHtml, /0 ₽|0 экз\./);

  assert.match(saleHtml, /Продажа/);
  assert.match(saleHtml, /Цена не указана/);
  assert.match(saleHtml, /24 шт\./);
  assert.match(saleHtml, /Не опубликовано/);
  assert.doesNotMatch(saleHtml, /Аренда/);

  assert.equal((mixedHtml.match(/SKU-MIXED/g) || []).length, 1);
  assert.match(mixedHtml, /Продажа[\s\S]*590[\s\S]*6 шт\.[\s\S]*✓ AQSI/);
  assert.match(mixedHtml, /Аренда[\s\S]*190[\s\S]*2 экз\./);
  assert.ok(mixedHtml.indexOf('✓ AQSI') < mixedHtml.indexOf('Аренда'));
});

test('Catalog modes preserve channel emphasis without duplicate card implementations', () => {
  const s = setup();
  const mixed = {
    id:'mixed', title:'Комплект', sku:'SKU-MIXED', current_retail_price:'590.00',
    current_rental_price:'190.00', sale_quantity:'6', rental_asset_count:2,
    sale_row_visible:true, rental_row_visible:true, aqsi_status:'published', aqsi_is_current:true,
  };

  const sale = s.context.renderCatalogVariantRow(mixed, 'sale');
  const rental = s.context.renderCatalogVariantRow(mixed, 'rental');
  const all = s.context.renderCatalogVariantRow(mixed, 'all');

  assert.match(sale, /Продажа/);
  assert.doesNotMatch(sale, /Аренда/);
  assert.match(rental, /Аренда/);
  assert.doesNotMatch(rental, /Продажа|AQSI/);
  assert.match(all, /Продажа[\s\S]*Аренда/);
  assert.equal((all.match(/SKU-MIXED/g) || []).length, 1);
});

test('Catalog economics footer separates applicability from a legitimate zero result', () => {
  const s = setup();
  const saleOnly = {
    rental_economics_applicable:false,
    economics:{profit:'0.00', revenue:'0.00', rental_count:0},
    available_asset_count:0,
  };
  const rentalZero = {
    rental_economics_applicable:true,
    economics:{profit:'0.00', revenue:'0.00', rental_count:0},
    available_asset_count:1,
  };
  const rentalNonZero = {
    rental_economics_applicable:true,
    economics:{profit:'1240.00', revenue:'2200.00', rental_count:8},
    available_asset_count:2,
  };

  assert.equal(s.context.renderCatalogEconomicsSummary(saleOnly, 'all'), '');
  assert.equal(s.context.renderCatalogEconomicsSummary(rentalZero, 'sale'), '');

  const rentalZeroHtml = s.context.renderCatalogEconomicsSummary(rentalZero, 'rental');
  const rentalNonZeroHtml = s.context.renderCatalogEconomicsSummary(rentalNonZero, 'rental');
  const allRentalHtml = s.context.renderCatalogEconomicsSummary(rentalNonZero, 'all');

  assert.match(rentalZeroHtml, /Результат аренды:[\s\S]*0/);
  assert.match(rentalNonZeroHtml, /Результат аренды:[\s\S]*1[^<]*240/);
  assert.match(rentalNonZeroHtml, /Доход[\s\S]*8 аренд[\s\S]*2 доступно/);
  assert.match(allRentalHtml, /Результат аренды:[\s\S]*8 аренд · 2 доступно/);
  assert.doesNotMatch(
    `${rentalZeroHtml}${rentalNonZeroHtml}${allRentalHtml}`,
    /Результат продаж|Продажи:|Операционный результат/,
  );
});

test('Catalog Product card keeps commercial rows in a wrapping mobile layout', () => {
  assert.match(styles, /@media \(max-width: 799px\)[\s\S]*\.catalog-variant-row \{ grid-template-columns: minmax\(0, 1fr\)/);
  assert.match(styles, /@media \(max-width: 799px\)[\s\S]*\.catalog-commercial-row \{ grid-template-columns: 64px minmax\(0, 1fr\) auto/);
  assert.match(styles, /\.catalog-aqsi \{ grid-column: 1 \/ -1/);
  assert.match(styles, /\.catalog-card-body \{[^}]*min-width: 0/);
});

test('Catalog delete actions live in the opened Product card, not the dense list card', () => {
  const listCardSource = source.slice(
    source.indexOf('function renderCatalogProductCard'),
    source.indexOf('function catalogVariantsForMode'),
  );
  const variantRowSource = source.slice(
    source.indexOf('function renderCatalogVariantRow'),
    source.indexOf('function renderCatalogSaleRow'),
  );
  const detailSource = source.slice(
    source.indexOf('function renderOperationsProduct'),
    source.indexOf('async function openVariantLabel'),
  );

  assert.match(source, /data-change-product-category/);
  assert.doesNotMatch(listCardSource, /data-delete-product/);
  assert.doesNotMatch(variantRowSource, /data-delete-variant/);
  assert.match(detailSource, /data-delete-product/);
  assert.match(detailSource, /data-delete-variant/);
  assert.match(source, /hard-delete-preflight/);
  assert.match(source, /Будет удалено/);
  assert.match(source, /защищённая бизнес-история/);
  assert.match(source, /Архивировать вместо удаления/);
  assert.match(source, /Это последний вариант товара/);
  assert.doesNotMatch(source, />Изменить категорию</);
});

test('Catalog action dialog is scrollable and becomes a touch-friendly mobile sheet', () => {
  assert.match(styles, /\.catalog-action-dialog \{[^}]*overflow-y: auto/);
  assert.match(styles, /@media \(max-width: 799px\)[\s\S]*\.catalog-action-dialog \{[^}]*max-height: 88dvh/);
  assert.match(styles, /@media \(max-width: 799px\)[\s\S]*\.variant-actions \.button\.danger \{ min-height: 44px/);
  assert.match(styles, /@media \(max-width: 799px\)[\s\S]*\.catalog-dialog-actions, \.catalog-dialog-actions\.three \{ grid-template-columns: 1fr/);
});

test('Settings exposes safe AQSI controls without a secret reveal action', () => {
  assert.match(source, /route\.name === "settings"/);
  assert.match(source, /<strong>Настройки<\/strong>/);
  assert.match(source, /Настройки[\s\S]*<h1>Интеграции<\/h1>/);
  assert.match(source, /API key[\s\S]*•{10,}/);
  assert.match(source, /type="password"[\s\S]*autocomplete="new-password"/);
  assert.match(source, /Заменить ключ/);
  assert.match(source, /Проверить подключение/);
  assert.match(source, /Автоматическая синхронизация каталога/);
  assert.match(source, /Синхронизировать сейчас/);
  assert.match(source, /catalog_sync_enabled/);
  assert.match(source, /\/api\/settings\/integrations\/\$\{integration\.id\}\/sync/);
  assert.match(source, /Используются устаревшие настройки из окружения/);
  assert.match(source, /\/api\/settings\/integrations\/aqsi\/migrate/);
  assert.doesNotMatch(source, /Показать ключ/);
  assert.match(styles, /\.settings-facts \{[^}]*grid-template-columns: minmax\(0, 1fr\) auto/);
  assert.match(styles, /@media \(max-width: 520px\)[\s\S]*\.settings-facts \{ grid-template-columns: 1fr/);
});
