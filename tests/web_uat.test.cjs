// No frontend dependencies/build: exercise the shipped script with DOM/API doubles.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const source = readFileSync('src/core/web/static/app.js', 'utf8')
  .replace('if (state.token) bootstrap(); else renderLogin();', '');

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
