# Sprint 4.17 — Rental Agreements

**Status:** implemented; automated verification complete; physical print/UAT pending.

**Epic 4 status:** ✅ Operational milestone completed; active follow-up in progress.

## Operator workflow

A READY order that requires an agreement now shows «Сформировать договор» and keeps Issue
disabled. The operator first sees a clearly labelled preview, then explicitly creates one final
agreement. Generation assigns its number and PDF but leaves the order READY. The same Sprint 4.16
issue confirmation becomes available only afterward. Orders whose item snapshots do not require
an agreement retain direct Issue.

The READY detail shows the number, generation time, template version, Open, Print and a collapsed
technical SHA-256 section. Once created, agreement-relevant terms are locked: items, assets,
tariffs, timing, price and deposit cannot drift away from the signed paper. A future supersede /
amendment workflow can replace this conservative policy, but Sprint 4.17 never overwrites a final
document.

## Templates and personal data

Settings → Rental → Agreement contains lessor legal details, the current Markdown source,
safe preview, explicit placeholder reference and complete append-only version history. Meaningful
changes create a new version; existing versions are not updated or hard-deleted. Rendering accepts
only the documented allowlist and escapes business content and raw HTML.

Core intentionally stores no passport fields, scans, URLs or passport placeholders. The canonical
template keeps blank identity, passport, phone and signature fields for handwriting on the printed
paper. Customer data is not inserted into those fields.

## Immutable document

One `RentalAgreement` covers one whole `RentalOrder` and all of its items. Item names, asset
numbers, agreed prices, recommended deposits, DURATION/SLOT terms and grace come from the
`RentalOrderItem` snapshots, never the current tariff. The generated record freezes its template
version, rendered content, lessor and item snapshots, final PDF bytes and exact PDF SHA-256.
Reprint returns those stored bytes without re-rendering.

Displayed numbers use `R-YYYYMMDD-NNNN`. PostgreSQL allocates the numeric suffix from a sequence,
so concurrent generation remains unique and numbers contain no customer data. The unique
order constraint plus an order row lock keeps the normal model at one agreement per order.

The only attestation method is `INTERNAL_HASH`. SHA-256 verifies that a stored/reprinted PDF is
byte-for-byte the originally generated artifact. It is not a qualified signature, trusted
timestamp or external proof of creation time.

## PDF boundary

The canonical artifact is generated server-side with embedded Roboto fonts, A4 page geometry,
12 mm margins, 9.8 pt body text at 11.3 pt leading, 10.7 pt headings and page numbering. The
premature forced break before section 3 was removed from the new layout: content now flows to the
second side only when the first side is full. Identity and time rows provide 8–9 mm of vertical
writing space, remarks and deposit provide 10 mm, and signature rows provide 10 mm. These are real
bordered layout elements, not underscore text.

Measured boundary: 10 pt body text at 11.5 pt leading moves the final three-item return signature
row onto a third page. The verified 9.8 / 11.3 pt profile is the largest tested profile that keeps
all ordinary three-item content and practical writing fields on two sides without clipping.

The new layout and the feminine default wording `именуемая далее «Арендодатель»` are shipped as
append-only template version 2. Version 1 remains in history, and generated agreements continue to
return their original stored bytes and SHA-256 without re-rendering. Legal clauses are unchanged;
only header/fact reflow, field components and the required grammatical form differ. Representative
one-, two- and three-item documents are verified as exactly two pages. If content exceeds the
supported two-page layout, Core rejects generation instead of silently shrinking it below the
readable baseline. The current supported operational boundary is three ordinary items.

## Physical UAT

Automated checks cannot accept paper output. Stop and verify:

1. In Settings → Rental → Agreement, preview the template, make a harmless test edit and
   confirm a new version appears in history.
2. Open a READY agreement-required Puzzi order. Confirm Issue is disabled, preview the agreement,
   create it and verify the order remains READY.
3. Open the PDF: confirm exactly two pages, 12 mm margins, blank bordered identity/remarks fields,
   correct asset, tariff, fixed price, deposit and usable 10 mm signature rows.
4. Print duplex on one A4 sheet. Check readability, margins, no clipping and practical handwriting
   space on the real printer.
5. Repeat with Puzzi plus a second item. Confirm one agreement contains both positions and totals.
6. Reopen and print again. Confirm the same number and SHA-256; the endpoint returns the exact
   stored bytes.
7. After creating a new template version or changing lessor/test catalog data, confirm the old
   agreement remains unchanged.
8. Confirm Issue after agreement generation. DURATION timing must start only at physical issue;
   agreement creation itself must never start the rental.

Sprint 4.18 remains unimplemented and owns Rental Return & Overdue redesign/charging.
