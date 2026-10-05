# Sprint 9.11 — Catalog Management Foundation

This is a historical implementation and UAT record. Current work is tracked in
[`planning/current.md`](../../planning/current.md); remaining debt is tracked in the roadmap.

## Delete Intake Draft — accepted

Administrator can delete only an unfinished Intake Draft from its own page. The application
workflow locks the row, rejects Completed/posted Intake, removes dependent Variant drafts before
the Product draft, soft-deletes draft Media under the existing policy, and then physically removes
the workspace. Reserved SKU/barcode values are not reused: the shared sequence stays monotonic.
Product/Variant, Inventory and posted history are unchanged.

## HEIC/HEIF and MPO ingestion — implemented, physical mobile acceptance pending

The common Media ingestion path decodes HEIC/HEIF and stores browser-compatible WebP;
JPEG/PNG/WebP remain unchanged. Limits stay at 15 MB / 20 million pixels. Tests cover orientation,
ICC, damaged files and Catalog/Intake associations. The supplied `IMG_9978.heic` decodes
successfully. A repeated iPhone Gallery upload exposed MPO; Core now normalizes its primary frame
to WebP after applying EXIF orientation.

A real iPhone Gallery/Camera smoke test after the MPO fix remains required. Dependency, licensing
and normalization details are in [HEIC ingestion](../heic-ingestion.md). Automated fixtures do not
claim physical iPhone acceptance.

## Intake Product autosave and Catalog Media — implemented, authenticated mobile UAT pending

- Product draft fields (category, name, description) save through the existing PATCH command:
  select immediately, text with a 600 ms debounce, serialized requests and visible retry errors.
- Product Save was removed only from Intake; Variant “Сохранить позицию” remains.
- Catalog Media is managed next to Product/Variant through existing ImageLink APIs with separate
  Camera/Gallery inputs, primary/additional/unlink actions and explicit Product fallback.
- Domain model, reserved identifiers and Media storage were not changed.
- Variant readiness hints refresh from the backend response without redrawing the form.

Physical iPhone/Safari Camera/Gallery and authenticated UI smoke tests remain open. Historical
automated evidence at the time: full pytest 336 passed; JavaScript 7 passed; Ruff, JavaScript
syntax and diff checks passed; Alembic head was `0029_intake_label_identity`; Docker Compose and
`/health` worked. The unauthenticated browser check reached `/app` but did not verify cards.

## Canonical Product label 40×30 — physically accepted

- Open PDF, Intake and Catalog direct print use one `VariantLabelRenderer`; CUPS receives the
  finished one-page PDF and only controls copy count.
- EAN-13 is vector-rendered without fit/scale: X-dimension 0.300 mm, 95 bar modules = 28.5 mm,
  quiet zones 9 modules each, total symbol width 33.9 mm inside a 40×30 MediaBox.
- The label contains Product, meaningful Variant, current retail price and current EAN. It omits
  the technical default Variant and SKU; missing price is not replaced by zero.
- Automated and visual PDF checks passed. Remote-CUPS physical acceptance then confirmed one and
  three correctly sized/oriented/fed labels on Xprinter.

See [Product Labels](../16_labels.md) and [Direct CUPS printing](../direct-cups-printing.md).

## Planning convention

Starting with this Sprint, new numbers use `Epic.Sprint` while the Sprint number remains global.
Historical names Sprint 1–10G are not renamed.
