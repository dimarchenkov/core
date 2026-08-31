# Sprint 9.11 — HEIC/HEIF ingestion

## Findings

The original HEIC support gap was in ImageInspector's allowlist (JPEG/PNG/WebP).
The supplied IMG_9978.heic is genuine HEIF, 3024×4032, 2,341,201 bytes, and passes both
host and Docker decoding. On the repeated iPhone Gallery upload, server diagnostics
identified **MPO**, 4032×3024, 2,821,494 bytes, rejected at the format allowlist.
Thus the failing mobile request contains a different representation of the photo.
Its declared multipart MIME was not recorded; the detected format is authoritative.

Gallery already uses `accept="image/*"` without capture; Camera is a separate input.
The API does not trust either filename suffix or declared Content-Type. ImageInspector
opens and decodes the actual content. Tests include image/heic, image/heif,
image/heic-sequence, image/heif-sequence, application/octet-stream and misleading image/jpeg.
These are compatibility inputs, not a claim about observed Safari headers.

## Minimal normalization boundary

All photo uploads use ImageService.upload_source_image and ImageInspector:

- Catalog Product/Variant: POST /api/media/images/upload, then existing ImageLink association.
- Intake Product/Variant: POST /api/intake/sessions/{id}/items/new.
- Intake replacement: PUT /api/intake/sessions/{id}/items/{item_id}/image.

Existing single-image JPEG/PNG/WebP bytes remain unchanged. HEIC/HEIF and JPEG/MPO
primary images are decoded to
8-bit RGB/RGBA and encoded as WebP quality 95, retaining dimensions (after rotation),
alpha and ICC profile when present. No resizing or separate HEIC storage is introduced.
HDR is reduced to SDR/8-bit by the decoder; this is not archival HDR preservation.
Depth/auxiliary frames, animation and EXIF/XMP metadata are not retained in WebP.
Pillow's existing MPO decoder opens the primary image; auxiliary pictures are not used
as the product photo. MPO uses this same normalization path, without a new dependency.
Its EXIF orientation is applied before metadata removal. Regression tests encode a real
two-frame MPO with distinct colours, EXIF rotation and ICC profile, sent as image/jpeg.
See [Pillow MPO support](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#mpo).

libheif applies HEIF rotation/mirror transforms. The Pillow plugin resets EXIF orientation;
ImageOps.exif_transpose handles remaining orientation without rotating twice. Tests use
orientation 6, asymmetric colours, dimension checks and ICC retention.

The original HEIC is not retained. The existing immutable source entry stores normalized
WebP; mime_type, dimensions, checksum and size_bytes describe those stored bytes.
original_filename remains provenance only and may end in .HEIC. Existing /source delivery
therefore works in browser previews without another converter or thumbnail subsystem.

Upload and normalized output limits: 15 MB; decoded image dimensions: at most 20 million
pixels, checked before pixel decoding. Invalid/truncated images get a fixed Russian 415
message; native decoder exception details are not returned. 48 MP originals remain outside
the current pixel limit; increasing it needs a separate resource-limit decision.

## Dependency and deployment

pillow-heif **1.5.0**, pinned in pyproject.toml/uv.lock, extends the existing Pillow decoder.
Core currently runs Python 3.13 (not 3.12). Upstream also provides Python 3.12 wheels,
macOS Intel/Apple Silicon and Linux x86_64/aarch64 wheels. Debian Bookworm Docker can use
the bundled native codecs without apt packages, a custom driver or a compiler.

Licensing: Python source BSD-3-Clause; upstream binary wheels are labelled GPLv2 due to
bundled codecs, including x265 (GPLv2), libheif/libde265 (LGPLv3). Do not describe the entire
binary dependency as BSD-only. Review redistribution obligations before distributing Core
images outside the current internal deployment.

Sources: [release and wheel matrix](https://pypi.org/project/pillow-heif/1.5.0/),
[orientation behaviour](https://pillow-heif.readthedocs.io/en/latest/workaround-orientation.html).
Installed distribution also includes LICENSE.txt and LICENSES_bundled.txt.

## Pending acceptance

Automated verification: full pytest 361 passed; JavaScript 7 passed; Ruff, JavaScript
syntax check and git diff --check passed. Docker build succeeded with the prebuilt wheel;
Linux x86_64 decoder smoke produced image/webp at 48×32 from the orientation fixture.
Local /health returned ok. No commit was created.

On a real iPhone: Gallery → Product photo, another photo → Variant, and one Intake photo;
check preview, correct orientation, ownership and persistence after reload. No successful physical iPhone
smoke-test is claimed by automated fixture tests.
