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

  vm.runInContext('state.operations.imageLinks = [{id:"a-link", entity_type:"catalog_variant", entity_id:"a", image_id:"photo-a", role:"primary"}];', s.context);
  const productFallback = s.context.renderCatalogMedia('catalog_product', 'p', {id:'p', variants:[{id:'a'}]});
  assert.match(productFallback, /Используется фото единственного варианта/);
  assert.match(productFallback, /data-image-id="photo-a"/);
  vm.runInContext('state.operations.imageLinks.push({id:"b-link", entity_type:"catalog_variant", entity_id:"b", image_id:"photo-b", role:"primary"});', s.context);
  const multiProduct = {id:'p', title:'Мозаика', variants:[{id:'a', title:'Медведь', sku:'SKU-A'}, {id:'b', title:'Красный', sku:'SKU-B'}, {id:'c', title:'Без фото', sku:'SKU-C'}]};
  const ambiguous = s.context.renderCatalogMedia('catalog_product', 'p', multiProduct);
  assert.doesNotMatch(ambiguous, /Используется фото единственного варианта|data-image-id="photo-a"|data-image-id="photo-b"/);
  assert.match(ambiguous, /Выбрать из вариантов/);
  const selector = s.context.renderVariantPhotoSelector(multiProduct);
  assert.match(selector, /photo-a[\s\S]*Медведь[\s\S]*SKU-A/);
  assert.match(selector, /photo-b[\s\S]*Красный[\s\S]*SKU-B/);
  assert.doesNotMatch(selector, /Без фото|SKU-C/);

  vm.runInContext('state.operations.imageLinks.push({id:"p-link", entity_type:"catalog_product", entity_id:"p", image_id:"photo-p", role:"primary"});', s.context);
  const existingProductPhoto = s.context.renderCatalogMedia('catalog_product', 'p', multiProduct);
  assert.match(existingProductPhoto, /data-image-id="photo-p"[\s\S]*Выбрать из вариантов/);
  const variantAction = s.context.renderCatalogMedia('catalog_variant', 'a', multiProduct);
  assert.match(variantAction, /Сделать основным фото товара/);
  vm.runInContext('state.operations.imageLinks.find(link => link.id === "p-link").image_id = "photo-a";', s.context);
  const currentVariant = s.context.renderCatalogMedia('catalog_variant', 'a', multiProduct);
  assert.doesNotMatch(currentVariant, /Сделать основным фото товара/);
  assert.match(currentVariant, /Основное фото товара/);

  vm.runInContext('state.operations.imageLinks = [];', s.context);
  const empty = s.context.renderCatalogMedia('catalog_product', 'p', multiProduct);
  assert.doesNotMatch(empty, /Выбрать из вариантов/);
});

test('Variant photo becomes Product primary by reusing ImageLink image id', async () => {
  const s = setup();
  vm.runInContext(`state.operations.product = {id:'p', title:'Мозаика', variants:[{id:'a', title:'Медведь', sku:'SKU-A'}]};
    state.operations.imageLinks = [{id:'a-link', entity_type:'catalog_variant', entity_id:'a', image_id:'photo-a', role:'primary'}];`, s.context);
  const calls = [];
  s.context.showToast = () => {};
  s.context.openOperationsProduct = async () => {};
  s.context.api = async (url, options) => {
    calls.push({url, method:options?.method, body:options?.body ? JSON.parse(options.body) : null});
    return {id:'p-reused', ...JSON.parse(options.body)};
  };

  await s.context.useVariantImageAsProductPrimary('a-link');

  assert.deepEqual(calls, [{
    url:'/api/media/image-links', method:'POST',
    body:{image_id:'photo-a', entity_type:'catalog_product', entity_id:'p', role:'primary', sort_order:0},
  }]);
  assert.doesNotMatch(calls.map(call => call.url).join(' '), /\/api\/media\/images|DELETE/);
  assert.equal(vm.runInContext('state.operations.imageLinks[0].entity_type', s.context), 'catalog_variant');
});

test('Existing Product primary can be replaced without unlinking Variant image', async () => {
  const s = setup();
  vm.runInContext(`state.operations.product = {id:'p', title:'Мозаика', variants:[{id:'a', title:'Медведь', sku:'SKU-A'}]};
    state.operations.imageLinks = [
      {id:'p-old', entity_type:'catalog_product', entity_id:'p', image_id:'photo-old', role:'primary'},
      {id:'a-link', entity_type:'catalog_variant', entity_id:'a', image_id:'photo-a', role:'primary'}
    ];`, s.context);
  const calls = [];
  s.context.showToast = () => {};
  s.context.openOperationsProduct = async () => {};
  s.context.api = async (url, options) => {
    const body = options?.body ? JSON.parse(options.body) : null;
    calls.push({url, method:options?.method, body});
    return url === '/api/media/image-links' ? {id:'p-reused', ...body} : {id:'p-reused', role:'primary'};
  };

  await s.context.useVariantImageAsProductPrimary('a-link');

  assert.equal(calls[0].body.image_id, 'photo-a');
  assert.equal(calls[0].body.role, 'gallery');
  assert.equal(calls[1].url, '/api/media/image-links/p-reused/primary');
  assert.equal(calls[1].method, 'POST');
  assert.equal(calls.some(call => call.method === 'DELETE'), false);
  assert.equal(vm.runInContext('state.operations.imageLinks.find(link => link.id === "a-link").image_id', s.context), 'photo-a');
});

test('Variant photo selector stays single-column and touch-friendly on mobile', () => {
  assert.match(styles, /\.variant-photo-options \{[^}]*display: grid/);
  assert.match(styles, /\.variant-photo-option \{[^}]*grid-template-columns: 72px minmax\(0, 1fr\)[^}]*width: 100%[^}]*min-height: 84px/);
  assert.match(styles, /@media \(max-width: 799px\)[\s\S]*\.variant-photo-option \{[^}]*grid-template-columns: 64px minmax\(0, 1fr\)[^}]*min-height: 76px/);
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
  assert.equal(defaults.sort, 'newest');
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

  const incompatible = s.context.normalizeCatalogState({mode:'sale', sort:'profit'});
  assert.equal(incompatible.sort, 'newest');
  const rentalIncompatible = s.context.normalizeCatalogState({mode:'rental', sort:'price_desc'});
  assert.equal(rentalIncompatible.sort, 'title');
  const compact = s.context.catalogUrlParams({mode:'sale', sort:'newest'}, false);
  assert.equal(compact.has('sort'), false);
  const stable = s.context.catalogUrlParams({mode:'all', sort:'stock_desc'});
  assert.equal(stable.get('mode'), 'all');
  assert.equal(stable.get('sort'), 'stock_desc');
});

test('Catalog sort options are mode-aware and keep the native mobile control', () => {
  const s = setup();
  const sale = s.context.renderCatalogSort(s.context.normalizeCatalogState({mode:'sale'}));
  const rental = s.context.renderCatalogSort(s.context.normalizeCatalogState({mode:'rental'}));
  const all = s.context.renderCatalogSort(s.context.normalizeCatalogState({mode:'all'}));

  assert.match(sale, /<select[^>]*>[\s\S]*Сначала новые[\s\S]*Сначала старые[\s\S]*Цена: сначала дешевле[\s\S]*Остаток: сначала больше/);
  assert.doesNotMatch(sale, /доходу от аренды|количеству аренд|результату аренды|последней аренде/);
  assert.match(rental, /По названию[\s\S]*По доходу от аренды[\s\S]*По количеству аренд[\s\S]*По результату аренды[\s\S]*По последней аренде/);
  assert.doesNotMatch(rental, /Сначала новые|Цена:|Остаток:/);
  assert.match(all, /Сначала новые[\s\S]*По названию[\s\S]*Цена: сначала дороже[\s\S]*Остаток: сначала меньше/);
  assert.doesNotMatch(all, /доходу от аренды|результату аренды/);
  assert.match(styles, /@media \(max-width: 520px\)[\s\S]*\.sort-field \{ grid-template-columns: 1fr/);
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
  assert.match(html, /data-restore-product="archived-product"[^>]*>Восстановить товар/);
  assert.match(html, /data-restore-variant="archived-variant"[^>]*>Восстановить вариант/);
  assert.match(source, /\/api\/catalog\/\$\{collection\}\/\$\{entityId\}\/restore/);
  assert.match(source, /Товар снова появится в рабочем каталоге/);
  assert.match(styles, /@media \(max-width: 799px\)[\s\S]*\.catalog-card-lifecycle-actions \.button[^}]*min-height: 44px/);
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
  assert.match(html, /Фото › Объективы/);
  assert.doesNotMatch(html, /name="slug"/);
  assert.match(source, /\/api\/catalog\/categories\/quick/);
});

test('Category management exposes tree editing, archive and restore without SQLAdmin', () => {
  const s = setup();
  vm.runInContext(`state.categories = [
    {id:'root', title:'Канцелярия', parent_id:null, is_active:true, is_archived:false},
    {id:'child', title:'Ручки', parent_id:'root', is_active:true, is_archived:false},
    {id:'archived', title:'Старое', parent_id:null, is_active:true, is_archived:true}
  ];`, s.context);

  const options = vm.runInContext('renderCategoryOptions(state.categories)', s.context);

  assert.match(options, /Канцелярия › Ручки/);
  assert.doesNotMatch(options, /Старое/);
  assert.match(source, /data-open-category-management/);
  assert.match(source, />Управление категориями</);
  assert.match(source, /data-edit-category/);
  assert.match(source, /data-archive-category/);
  assert.match(source, /data-restore-category/);
  assert.match(source, /\/api\/catalog\/categories\?status=all/);
  assert.match(source, /\/api\/catalog\/categories\/\$\{categoryId\}\/restore/);
  assert.doesNotMatch(source, /\/admin\/category\/list/);
});

test('Category management remains single-column and touch-friendly on mobile', () => {
  assert.match(styles, /@media \(max-width: 799px\)[\s\S]*\.category-management-row \{ grid-template-columns: minmax\(0, 1fr\)/);
  assert.match(styles, /@media \(max-width: 799px\)[\s\S]*\.category-management-actions \.button \{ min-height: 44px/);
  assert.match(styles, /\.category-management-list \{[^}]*overflow-y: auto/);
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
  const deleteDialogSource = source.slice(
    source.indexOf('function renderCatalogDeletePreflight'),
    source.indexOf('async function executeCatalogDelete'),
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
  assert.doesNotMatch(deleteDialogSource, />Изменить категорию</);
});

test('Catalog action dialog is scrollable and becomes a touch-friendly mobile sheet', () => {
  assert.match(styles, /\.catalog-action-dialog \{[^}]*overflow-y: auto/);
  assert.match(styles, /@media \(max-width: 799px\)[\s\S]*\.catalog-action-dialog \{[^}]*max-height: 88dvh/);
  assert.match(
    styles,
    /@media \(max-width: 799px\)[\s\S]*\.variant-actions \.button\.danger[^{]*\{[^}]*min-height: 44px/,
  );
  assert.match(styles, /@media \(max-width: 799px\)[\s\S]*\.catalog-dialog-actions, \.catalog-dialog-actions\.three \{ grid-template-columns: 1fr/);
});

test('Product detail removes the generic Info summary but keeps Product management', () => {
  const detailSource = source.slice(
    source.indexOf('function renderOperationsProduct'),
    source.indexOf('async function openVariantLabel'),
  );

  assert.doesNotMatch(detailSource, /<section class="card order-facts">/);
  assert.doesNotMatch(detailSource, /Арендные единицы|Результат аренды|product\.skus/);
  assert.match(detailSource, /Управление товаром/);
  assert.match(detailSource, /Название[\s\S]*Описание[\s\S]*Категория[\s\S]*Сохранить товар/);
  assert.match(detailSource, /Добавить вариант/);
});

test('Variant detail uses readable characteristics and concise name editing', () => {
  const s = setup();
  vm.runInContext('state.operations.imageLinks = []; state.operations.aqsi = new Map(); state.user = {is_admin:true};', s.context);
  const product = {id:'product', is_archived:false, variants:[]};
  const variant = {
    id:'variant', title:'С медвежонком', sku:'SKU-READONLY', barcode:'2000000000015',
    barcode_source:'internal', attributes:{Цвет:'Синий', Размер:'A4', Материал:'Картон'},
    is_archived:false, physical_quantity:'19', ordinary_quantity:'19', rental_asset_count:0,
    available_asset_count:0, rented_asset_count:0, current_retail_price:'450.00',
    current_rental_price:null, current_recommended_deposit:null,
  };

  const html = s.context.renderOperationsVariant(variant, product, true);

  assert.match(html, /Название варианта[\s\S]*С медвежонком[\s\S]*data-rename-variant="variant"[^>]*>Изменить</);
  assert.doesNotMatch(html, /Изменить название/);
  assert.match(html, /SKU · SKU-READONLY/);
  assert.match(html, /Характеристики[\s\S]*Цвет[\s\S]*Синий[\s\S]*Материал[\s\S]*Картон/);
  assert.match(html, /Добавить характеристику/);
  assert.match(html, /data-edit-characteristic/);
  assert.match(html, /data-delete-characteristic/);
  assert.doesNotMatch(html, />Редактировать</);
  assert.doesNotMatch(source, /Атрибуты JSON|parseAttributes|data-edit-variant/);
});

test('Variant value editors share one desktop action column and collapse on mobile', () => {
  const s = setup();
  vm.runInContext('state.operations.imageLinks = []; state.operations.aqsi = new Map(); state.user = {is_admin:true};', s.context);
  const product = {id:'product', is_archived:false, variants:[]};
  const variant = {
    id:'variant', title:'С медвежонком', sku:'SKU-ALIGN', barcode:'2000000000084',
    barcode_source:'internal', attributes:{Цвет:'Синий'}, is_archived:false,
    physical_quantity:'5', ordinary_quantity:'3', rental_asset_count:2,
    available_asset_count:1, rented_asset_count:1, current_retail_price:'500.00',
    current_rental_price:'100.00', current_recommended_deposit:'700.00',
  };

  const html = s.context.renderOperationsVariant(variant, product, true);

  assert.equal((html.match(/class="variant-info-action/g) || []).length, 4);
  assert.match(html, /variant-info-section[\s\S]*Название варианта[\s\S]*data-rename-variant/);
  assert.match(html, /commercial-block variant-info-section[\s\S]*Штрихкод[\s\S]*data-replace-barcode/);
  assert.match(html, /commercial-block variant-info-section[\s\S]*Продажа[\s\S]*data-set-sale-price/);
  assert.match(html, /rental-commercial-block variant-info-section[\s\S]*Аренда[\s\S]*data-set-rental-prices/);
  assert.match(html, /class="catalog-counts"[\s\S]*На учёте 5 шт\.[\s\S]*Для продажи 3 шт\./);
  assert.doesNotMatch(html, /variant-info-action[^<]*[\s\S]{0,120}На учёте/);
  assert.match(html, /class="variant-actions"[\s\S]*Корректировка остатка[\s\S]*Открыть PDF[\s\S]*Системная печать[\s\S]*Удалить вариант/);
  assert.match(styles, /\.variant-info-section \{[^}]*display: grid;[^}]*grid-template-columns: minmax\(0, 1fr\) auto/);
  assert.match(styles, /@media \(max-width: 520px\)[\s\S]*\.variant-info-section \{[^}]*grid-template-columns: minmax\(0, 1fr\)/);
  assert.match(styles, /@media \(max-width: 520px\)[\s\S]*\.variant-info-action \{[^}]*justify-self: start/);
});

test('Characteristic builder allows zero pairs and rejects blank or duplicate pairs', () => {
  const s = setup();
  const row = (name, value) => ({querySelector: selector => ({value: selector.includes('characteristic_name') ? name : value})});
  const form = rows => ({querySelectorAll: () => rows});

  assert.deepEqual(JSON.parse(JSON.stringify(s.context.collectCharacteristicRows(form([])))), {});
  assert.deepEqual(
    JSON.parse(JSON.stringify(s.context.collectCharacteristicRows(form([row(' Цвет ', ' Синий '), row('Размер', 'A4')])))),
    {Цвет:'Синий', Размер:'A4'},
  );
  assert.throws(() => s.context.collectCharacteristicRows(form([row(' ', 'Синий')])), /название/);
  assert.throws(() => s.context.collectCharacteristicRows(form([row('Цвет', ' ')])), /значение/);
  assert.throws(() => s.context.collectCharacteristicRows(form([row('Цвет', 'Синий'), row(' Цвет ', 'Красный')])), /уже добавлена/);

  const added = s.context.setVariantCharacteristic({Цвет:'Синий'}, ' Материал ', ' Картон ');
  assert.deepEqual(JSON.parse(JSON.stringify(added)), {Цвет:'Синий', Материал:'Картон'});
  const edited = s.context.setVariantCharacteristic(added, 'Оттенок', 'Голубой', 'Цвет');
  assert.deepEqual(JSON.parse(JSON.stringify(edited)), {Материал:'Картон', Оттенок:'Голубой'});
  const removed = s.context.removeVariantCharacteristic(edited, 'Материал');
  assert.deepEqual(JSON.parse(JSON.stringify(removed)), {Оттенок:'Голубой'});
  assert.throws(() => s.context.setVariantCharacteristic(added, 'Цвет', 'Красный'), /уже существует/);
});

test('Rental details are conditional and sale-only Variant keeps one rental entry action', () => {
  const s = setup();
  vm.runInContext('state.operations.imageLinks = []; state.operations.aqsi = new Map(); state.user = {is_admin:true};', s.context);
  const product = {id:'product', is_archived:false, variants:[]};
  const base = {
    id:'variant', title:'Основной', sku:'SKU-1', barcode:'2000000000015', barcode_source:'internal',
    attributes:{}, is_archived:false, physical_quantity:'3', ordinary_quantity:'3',
    current_retail_price:'450.00', current_rental_price:null, current_recommended_deposit:null,
    available_asset_count:0, rented_asset_count:0,
  };

  const saleOnly = s.context.renderOperationsVariant({...base, rental_asset_count:0}, product, true);
  assert.doesNotMatch(saleOnly, /Аренда|Арендных экземпляров|Доступно сейчас|Выдано/);
  assert.match(saleOnly, /Выделить в аренду/);

  const rental = s.context.renderOperationsVariant({
    ...base, rental_asset_count:3, available_asset_count:2, rented_asset_count:1,
    current_rental_price:'350.00', current_recommended_deposit:'5000.00',
  }, product, true);
  assert.match(rental, /Аренда[\s\S]*350[\s\S]*5[^<]*000/);
  assert.match(rental, /Экземпляров: 3[\s\S]*Доступно: 2[\s\S]*Выдано: 1/);
  assert.match(rental, /Изменить условия/);
});

test('Inventory adjustment uses Russian reason labels and submits a ledger delta', () => {
  const s = setup();
  const reasons = vm.runInContext('inventoryAdjustmentReasons', s.context);
  assert.deepEqual(JSON.parse(JSON.stringify(reasons)), [
    ['stocktake', 'Пересчёт остатков'],
    ['shortage', 'Недостача'],
    ['damage', 'Повреждение / брак'],
    ['gift', 'Подарок'],
    ['personal_use', 'Личное использование'],
    ['other', 'Другое'],
  ]);
  const adjustmentSource = source.slice(
    source.indexOf('function adjustInventory'),
    source.indexOf('async function uploadCatalogImage'),
  );
  assert.match(adjustmentSource, /Новый фактический остаток/);
  assert.match(adjustmentSource, /quantityDelta = actual - current/);
  assert.match(adjustmentSource, /quantity_delta: String\(quantityDelta\)/);
  assert.match(adjustmentSource, /обязательно для «Другое»/);
  assert.match(adjustmentSource, /inventory-adjustments/);
});

test('Characteristics and dialogs stay single-column on narrow mobile screens', () => {
  assert.match(styles, /@media \(max-width: 520px\)[\s\S]*\.characteristic-row \{ grid-template-columns: minmax\(92px, \.8fr\) minmax\(0, 1\.2fr\)/);
  assert.match(styles, /@media \(max-width: 520px\)[\s\S]*\.characteristic-builder-row \{ grid-template-columns: 1fr/);
  assert.match(styles, /\.characteristic-name \{[^}]*overflow-wrap: anywhere/);
  assert.match(styles, /\.characteristic-value \{[^}]*overflow-wrap: anywhere/);
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
  assert.match(source, /aqsi-checkout-form/);
  assert.match(source, /device_id: deviceId/);
  assert.match(source, /tax_system_code: taxSystemCode/);
  assert.match(source, /tax_code: taxCode/);
  assert.match(source, /\/api\/settings\/integrations\/\$\{integration\.id\}\/sync/);
  assert.match(source, /Используются устаревшие настройки из окружения/);
  assert.match(source, /\/api\/settings\/integrations\/aqsi\/migrate/);
  assert.doesNotMatch(source, /Показать ключ/);
  assert.match(styles, /\.settings-facts \{[^}]*grid-template-columns: minmax\(0, 1fr\) auto/);
  assert.match(styles, /@media \(max-width: 520px\)[\s\S]*\.settings-facts \{ grid-template-columns: 1fr/);
});

function scannerHarness() {
  let keydown;
  const document = {
    addEventListener(event, handler) { if (event === 'keydown') keydown = handler; },
    querySelector() { return null; },
  };
  const context = vm.createContext({
    document,
    window: { addEventListener() {} },
    sessionStorage: { getItem() {} },
    URLSearchParams,
    performance: { now: () => 1 },
    setTimeout() {},
    clearTimeout() {},
  });
  vm.runInContext(source, context);
  const scanner = vm.runInContext('new ScannerService(document)', context);
  const event = (key, timeStamp, editable = false) => ({
    key, timeStamp, isComposing:false, ctrlKey:false, altKey:false, metaKey:false,
    target:{closest: () => editable ? {} : null},
    preventDefault() {}, stopPropagation() {},
  });
  return { scanner, keydown, event };
}

test('Global HID scanner routes local page context before Sales', () => {
  const { scanner, keydown, event } = scannerHarness();
  const routed = [];
  scanner.setLocalHandler('intake', value => { routed.push(`intake:${value}`); return true; });
  scanner.setGlobalHandler(value => { routed.push(`sale:${value}`); return true; });
  [...'4601'].forEach((key, index) => keydown(event(key, 1 + index * 10)));
  keydown(event('Enter', 45));
  assert.deepEqual(routed, ['intake:4601']);

  scanner.clearLocalHandler('intake');
  [...'4602'].forEach((key, index) => keydown(event(key, 101 + index * 10)));
  keydown(event('Enter', 145));
  assert.deepEqual(routed, ['intake:4601', 'sale:4602']);
});

test('Scanner ignores editable fields and manually paced sequences', () => {
  const { scanner, keydown, event } = scannerHarness();
  const routed = [];
  scanner.setGlobalHandler(value => { routed.push(value); return true; });
  [...'FAST'].forEach((key, index) => keydown(event(key, 1 + index * 10, true)));
  keydown(event('Enter', 45, true));
  [...'SLOW'].forEach((key, index) => keydown(event(key, 100 + index * 80)));
  keydown(event('Enter', 430));
  [...'FAST'].forEach((key, index) => keydown(event(key, 500 + index * 10)));
  keydown(event('Enter', 900));
  assert.deepEqual(routed, []);
});

test('Scanner accepts slower Bluetooth HID input and Tab suffix used by Safari devices', () => {
  const { scanner, keydown, event } = scannerHarness();
  const routed = [];
  scanner.setGlobalHandler(value => { routed.push(value); return true; });
  [...'4601234567893'].forEach((key, index) => keydown(event(key, 1 + index * 70)));
  keydown(event('Tab', 870));
  assert.deepEqual(routed, ['4601234567893']);
});

test('Sales UI keeps browser-scoped active selection and touch-sized controls', () => {
  assert.match(source, /core\.activeSale\.\$\{state\.user\.id\}/);
  assert.match(source, /\/api\/sales\/auto\/items\/by-variant/);
  assert.match(source, /\/api\/sales\/auto\/items\/by-barcode/);
  assert.match(source, /Добавить из каталога/);
  assert.match(source, /id="sale-barcode-form"/);
  assert.match(source, /id="sale-barcode-input"/);
  assert.match(source, /Отложенные/);
  assert.match(source, /Продажа #\$\{sale\.sale_number\}/);
  assert.match(styles, /\.quantity-button \{[^}]*width: 48px[^}]*min-height: 48px/);
  assert.match(styles, /\.catalog-sale-add \{[^}]*width: 44px[^}]*min-height: 44px/);
  assert.match(styles, /@media \(max-width: 520px\)[\s\S]*\.sale-actions \{ grid-template-columns: 1fr/);
});

test('Checkout UI selects cash or card/QR and recovers persisted provider states', () => {
  assert.match(source, /\/checkout\/context/);
  assert.match(source, /＋ Свободная позиция/);
  assert.match(source, /\/items\/manual/);
  assert.match(source, /\/discount/);
  assert.match(source, /data-sale-discount/);
  assert.match(source, /Подытог/);
  assert.match(source, /К оплате/);
  assert.match(source, /Оплатить \$\{formatMoney\(sale\.total_amount\)\}/);
  assert.match(source, /Получено наличными/);
  assert.match(source, /name="payment_method" value="cash"/);
  assert.match(source, /name="payment_method" value="card"/);
  assert.match(source, /payment_method: paymentMethod/);
  assert.match(source, /Карта \/ QR/);
  assert.match(source, /Только карта/);
  assert.match(source, /Только QR/);
  assert.match(source, /acquiring_mode/);
  assert.match(source, /Ожидаем оплату на кассе/);
  assert.match(source, /Оплата отменена на терминале/);
  assert.match(source, /Деньги не списаны/);
  assert.match(source, /Результат оплаты пока неизвестен/);
  assert.match(source, /Оплата прошла, но чек не сформирован/);
  assert.match(source, /checkout\/progress/);
  assert.match(source, /retry-payment/);
  assert.match(source, /retry-fiscalization/);
  assert.match(source, /stock_warnings/);
  assert.match(source, /item\.source === "manual"/);
  assert.match(source, /Не удалось проверить оплату/);
  assert.match(styles, /\.discount-choices/);
  assert.match(styles, /\.sale-item-source/);
  assert.match(styles, /\.transaction-summary/);
  assert.match(styles, /\.checkout-method-options/);
});

test('Switching active Sale changes the Catalog add target without sharing users', async () => {
  const s = setup();
  const stored = new Map();
  s.context.window.localStorage = {
    getItem(key) { return stored.get(key) || null; },
    setItem(key, value) { stored.set(key, value); },
    removeItem(key) { stored.delete(key); },
  };
  vm.runInContext(`state.user = {id:'operator-1'};
    state.sales.drafts = [
      {id:'sale-1', sale_number:1, status:'draft', item_quantity:0, total_amount:'0.00', items:[]},
      {id:'sale-2', sale_number:2, status:'draft', item_quantity:0, total_amount:'0.00', items:[]}
    ];`, s.context);
  s.context.selectActiveSale('sale-2');
  assert.equal(vm.runInContext('state.sales.activeId', s.context), 'sale-2');
  assert.equal(stored.get('core.activeSale.operator-1'), 'sale-2');

  const calls = [];
  s.context.showToast = () => {};
  s.context.api = async (url) => {
    calls.push(url);
    return {id:'sale-2', sale_number:2, status:'draft', item_quantity:1, total_amount:'100.00', items:[{variant_id:'variant', display_label_snapshot:'Ручка'}]};
  };
  await s.context.addVariantToActiveSale('variant');
  assert.equal(calls[0], '/api/sales/sale-2/items/by-variant');

  vm.runInContext("state.user = {id:'operator-2'};", s.context);
  assert.equal(s.context.storedActiveSaleId(), null);
});
