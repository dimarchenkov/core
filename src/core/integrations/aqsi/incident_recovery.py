"""Offline, explicitly approved receipt recovery; never calls an AQSI provider.

This is an incident tool, not a synchronization mechanism or checkout entry point.
The caller owns the transaction and must stop workers during production application.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from core.catalog.models import CatalogVariant
from core.identity.models import User
from core.integrations.enums import IntegrationProvider
from core.integrations.models import Integration
from core.inventory.service import InventoryService
from core.sales.enums import (
    FiscalizationStatus,
    PaymentJobStatus,
    PaymentMethod,
    PaymentStatus,
    SaleItemSource,
    SaleStatus,
)
from core.sales.models import Fiscalization, PaymentAttempt, Sale, SaleItem
from core.sales.repository import SaleRepository

# Heterogeneous raw provider JSON is retained as evidence, not exposed by API schemas.
JsonObject = dict[str, Any]
INCIDENT_DATE = "2026-10-20"
CONFIRMED_LINKS = {1670: 109, 1672: 110, 1676: 112}
ALREADY_LINKED = {1667: 104, 1668: 106, 1669: 107}
INCIDENT_DOCS = {*range(1667, 1688), 1690}


def _require(condition: bool, message: str) -> None:
    """Fail closed on any ambiguous or changed recovery input."""
    if not condition:
        raise ValueError(message)


def _money(value: object) -> Decimal:
    """Convert exact provider minor units without binary float arithmetic."""
    amount = Decimal(str(value))
    _require(amount.is_finite() and amount >= 0 and amount == int(amount), "Invalid amount")
    return amount / 100


def _snapshot(model: object) -> JsonObject:
    """Retain a JSON-safe, complete pre-correction row for the audit trail."""
    values = {attr.key: getattr(model, attr.key) for attr in inspect(type(model)).column_attrs}
    return json.loads(json.dumps(values, default=str))


def _assert_unchanged(sale: Sale, evidence: JsonObject) -> None:
    """Refuse to overwrite intervening edits after the operator reviewed the export."""
    tables = (
        ([sale], "core_sales"),
        (sale.items, "core_sale_items"),
        (sale.payments, "core_payment_attempts"),
        ([sale.fiscalization] if sale.fiscalization else [], "core_fiscalizations"),
    )
    for records, table in tables:
        expected = {
            row["id"]: row
            for row in evidence[table]
            if row.get("sale_id", row["id"]) == str(sale.id)
        }
        actual = {str(row.id): _snapshot(row) for row in records}
        _require(actual == expected, f"Sale #{sale.sale_number}: {table} changed since export")


def _validate_receipt(receipt: JsonObject, slips: JsonObject, device: int) -> None:
    """Check fiscal identity, day, line totals and independently exported tender evidence."""
    info = receipt["info"]
    _require(receipt["device"]["id"] == device, "Wrong device")
    _require(info["typeId"] == 1 and not receipt["isNonFiscal"], "Not a fiscal sale receipt")
    _require(not receipt.get("correctionInfo") and not receipt.get("originalId"), "Correction")
    _require(bool(info["docInfo"]["docFiscalAttributeInt"]), "Missing fiscal signature")
    date = datetime.fromisoformat(info["dateTime"])
    _require(date.tzinfo is not None, "Receipt time lacks timezone")
    _require(
        date.astimezone(ZoneInfo("Europe/Moscow")).date().isoformat() == INCIDENT_DATE,
        "Receipt outside approved day",
    )
    amount = _money(info["sum"])
    _require(amount > 0, "Empty receipt")
    line_total = Decimal(0)
    for position in receipt["positions"]:
        line = position["info"]
        quantity = Decimal(str(line["quantity"]))
        _require(quantity > 0 and quantity == int(quantity), "Non-integer item quantity")
        total = _money(line["totalAmount"])
        _require(_money(line["finalPrice"]) * quantity == total, "Line amount mismatch")
        _require(bool(line["name"]) and len(line["name"]) <= 511, "Invalid item label")
        line_total += total
    _require(line_total == amount, "Receipt lines do not add up")
    tender = {0: Decimal(0), 1: Decimal(0)}
    for part in receipt["payments"]:
        kind = part["type"]
        _require(kind in tender, "Unsupported payment type")
        paid = _money(part["amount"])
        _require(paid > 0, "Empty payment")
        tender[kind] += paid
        if kind == 1:
            slip = slips.get(part["slip"]["id"])
            _require(slip is not None, "Missing independently exported slip")
            content = slip["content"]
            _require(content == part["slip"]["content"], "Slip evidence mismatch")
            approved = content["responseCode"] == "000" or (
                content.get("iface") == "sbp" and content["responseCode"] == "SUCCESS"
            )
            _require(
                content["type"] == "purchase" and approved,
                "Slip is not an approved purchase",
            )
            _require(_money(content["amount"]) == paid, "Slip amount mismatch")
    _require(tender[0] + tender[1] == amount and not receipt.get("change"), "Tender mismatch")
    _require(tender[0] == _money(info["amounts"]["cash"]), "Cash total mismatch")
    _require(tender[1] == _money(info["amounts"]["cashless"]), "Card total mismatch")
    _require(
        all(not info["amounts"].get(k) for k in ("prepayment", "credit", "consideration")),
        "Unsupported settlement type",
    )


def _new_sale(session: Session, receipt: JsonObject, owner: UUID) -> Sale:
    """Import frozen lines; only exact external Variant IDs may affect inventory."""
    amount = _money(receipt["info"]["sum"])
    sale = Sale(
        sale_number=SaleRepository(session).next_sale_number(),
        owner_id=owner,
        status=SaleStatus.PAID,
        subtotal_amount=amount,
        total_amount=amount,
        discount_amount=Decimal(0),
        currency="RUB",
        created_by_id=owner,
        updated_by_id=owner,
    )
    session.add(sale)
    for position in receipt["positions"]:
        line = position["info"]
        external = position.get("externalId")
        variant = session.get(CatalogVariant, UUID(external)) if external else None
        _require(not external or variant is not None, f"Unknown Variant: {external}")
        price, total = _money(line["finalPrice"]), _money(line["totalAmount"])
        sale.items.append(
            SaleItem(
                source=SaleItemSource.CATALOG if variant else SaleItemSource.MANUAL,
                variant_id=variant.id if variant else None,
                product_title_snapshot=variant.product.title if variant else None,
                variant_title_snapshot=variant.title if variant else None,
                display_label_snapshot=line["name"],
                sku_snapshot=variant.sku if variant else None,
                barcode_snapshot=variant.barcode if variant else None,
                unit_price=price,
                quantity=int(line["quantity"]),
                line_total=total,
                fiscal_line_total=total,
                fiscal_allocations=[{"unit_price": str(price), "quantity": int(line["quantity"])}],
                currency="RUB",
                created_by_id=owner,
                updated_by_id=owner,
            )
        )
    session.flush()
    return sale


def recover_receipts(session: Session, evidence: JsonObject, digest: str) -> list[JsonObject]:
    """Apply the approved incident atomically in the caller's transaction, without network IO."""
    _require(
        evidence["date"] == INCIDENT_DATE and evidence["timezone"] == "Europe/Moscow",
        "Wrong incident scope",
    )
    receipts = sorted(evidence["receipts"], key=lambda r: r["info"]["docInfo"]["docNumber"])
    _require(
        len(receipts) == 22
        and {r["info"]["docInfo"]["docNumber"] for r in receipts} == INCIDENT_DOCS,
        "Unexpected receipt set",
    )
    _require(len({r["id"] for r in receipts}) == 22, "Duplicate receipt IDs")
    _require(
        sum(_money(r["info"]["sum"]) for r in receipts) == Decimal("20914.50"),
        "Unexpected incident total",
    )
    slips = {s["id"]: s for s in evidence["slips"]}
    used_slips = [p["slip"]["id"] for r in receipts for p in r["payments"] if p["type"] == 1]
    _require(
        len(used_slips) == len(set(used_slips)) and set(used_slips) == set(slips),
        "Duplicated or unmatched acquiring slips",
    )
    for receipt in receipts:
        _validate_receipt(receipt, slips, evidence["device_id"])
    if session.bind.dialect.name == "postgresql":
        # Exclusive incident writes also serialize against normal checkout and inventory writers.
        session.execute(
            text(
                "LOCK TABLE sales, sale_items, payment_attempts, fiscalizations, "
                "payment_jobs, fiscalization_jobs, stock_movements "
                "IN SHARE ROW EXCLUSIVE MODE"
            )
        )
    integration = session.get(Integration, UUID(evidence["integration_id"]))
    _require(
        integration is not None and integration.provider is IntegrationProvider.AQSI,
        "Missing AQSI integration",
    )
    linked_sales = {
        s.sale_number: s
        for s in session.scalars(
            select(Sale)
            .where(Sale.sale_number.in_(list(CONFIRMED_LINKS.values())))
            .with_for_update()
        ).all()
    }
    _require(set(linked_sales) == set(CONFIRMED_LINKS.values()), "Missing approved sales")
    owners = {s.owner_id for s in linked_sales.values()}
    _require(len(owners) == 1, "Ambiguous recovery owner")
    owner = owners.pop()
    user = session.get(User, owner)
    _require(user is not None and user.is_active, "Inactive recovery owner")
    results = []
    for receipt in receipts:
        receipt_id = receipt["id"]
        doc = receipt["info"]["docInfo"]["docNumber"]
        amount = _money(receipt["info"]["sum"])
        matches = session.scalars(
            select(Fiscalization).where(Fiscalization.external_receipt_id == receipt_id)
        ).all()
        _require(len(matches) <= 1, "Duplicate receipt binding")
        if matches:
            fiscal = matches[0]
            sale = session.get(Sale, fiscal.sale_id)
            expected_number = (CONFIRMED_LINKS | ALREADY_LINKED).get(doc)
            _require(
                expected_number is None or sale.sale_number == expected_number,
                "Receipt bound to a different Sale",
            )
            _require(
                fiscal.status is FiscalizationStatus.SUCCEEDED
                and fiscal.integration_id == integration.id
                and fiscal.fiscal_amount == amount
                and sale.status is SaleStatus.COMPLETED
                and sale.total_amount == amount,
                "Existing receipt binding is not complete",
            )
            effective = [
                p
                for p in sale.payments
                if p.status is PaymentStatus.SUCCEEDED and not p.accounting_superseded
            ]
            _require(
                sum(p.requested_amount for p in effective) == amount,
                "Existing sale payment total mismatch",
            )
            for method, key in ((PaymentMethod.CASH, "cash"), (PaymentMethod.CARD, "cashless")):
                _require(
                    sum(p.requested_amount for p in effective if p.payment_method is method)
                    == _money(receipt["info"]["amounts"][key]),
                    "Existing tender mismatch",
                )
            if doc not in ALREADY_LINKED:
                _require(
                    fiscal.provider_metadata.get("recovery", {}).get("evidence_sha256") == digest,
                    "Existing recovery has different evidence",
                )
            results.append({"doc": doc, "sale": sale.sale_number, "action": "already_recorded"})
            continue
        _require(doc not in ALREADY_LINKED, "Expected existing receipt binding is missing")
        sale = linked_sales.get(CONFIRMED_LINKS.get(doc))
        prior: JsonObject = {}
        if sale is not None:
            _assert_unchanged(sale, evidence)
            _require(sale.total_amount == amount and sale.deleted_at is None, "Sale mismatch")
            _require(
                not sale.fiscalization or sale.fiscalization.status is FiscalizationStatus.SKIPPED,
                "Existing fiscal obligation needs separate review",
            )
            prior = {
                "sale": _snapshot(sale),
                "payments": [_snapshot(p) for p in sale.payments],
                "fiscalization": _snapshot(sale.fiscalization) if sale.fiscalization else None,
                "payment_jobs": [_snapshot(j) for j in sale.payment_jobs],
            }
        else:
            sale = _new_sale(session, receipt, owner)
        recorded_at = datetime.now(UTC)
        audit = {
            "incident": INCIDENT_DATE,
            "evidence_sha256": digest,
            "recorded_at": recorded_at.isoformat(),
            "actor_id": str(owner),
            "receipt_id": receipt_id,
            "fiscal_document": doc,
            "approved_sale_number": CONFIRMED_LINKS.get(doc),
            "basis": "Operator-authorized recovery of manually completed receipts",
        }
        for old in sale.payments:
            if old.status in {
                PaymentStatus.SUCCEEDED,
                PaymentStatus.PENDING,
                PaymentStatus.UNKNOWN,
            }:
                old.provider_metadata = {
                    **old.provider_metadata,
                    "accounting_superseded": True,
                    "superseded_by_receipt": receipt_id,
                    "recovery": audit,
                }
                old.updated_by_id = owner
                old.version += 1
        number = max((p.attempt_number for p in sale.payments), default=0)
        new_payments = []
        for index, part in enumerate(receipt["payments"], start=1):
            card = part["type"] == 1
            slip_id = part["slip"]["id"] if card else None
            if slip_id:
                # Legacy checkout stored slip IDs in external_id; imports keep them in metadata.
                duplicates = session.scalars(
                    select(PaymentAttempt).where(
                        (PaymentAttempt.external_id == slip_id)
                        | (
                            PaymentAttempt.provider_metadata["imported_slip_id"].as_string()
                            == slip_id
                        )
                    )
                ).all()
                _require(not duplicates, "Acquiring slip already recorded")
            payment = PaymentAttempt(
                sale_id=sale.id,
                integration_id=integration.id if card else None,
                provider=IntegrationProvider.AQSI if card else None,
                payment_method=PaymentMethod.CARD if card else PaymentMethod.CASH,
                requested_amount=_money(part["amount"]),
                fiscalization_required=True,
                currency="RUB",
                status=PaymentStatus.SUCCEEDED,
                idempotency_key=f"recovery:{integration.id}:{receipt_id}:{index}",
                attempt_number=number + index,
                provider_metadata={"recovery": audit, "imported_slip_id": slip_id},
                created_by_id=owner,
                updated_by_id=owner,
            )
            sale.payments.append(payment)
            new_payments.append(payment)
        session.flush()
        fiscal = sale.fiscalization
        if fiscal is None:
            fiscal = Fiscalization(
                sale_id=sale.id,
                idempotency_key=f"recovery:{integration.id}:{receipt_id}",
                created_by_id=owner,
            )
            sale.fiscalization = fiscal
        fiscal.payment_attempt_id = new_payments[0].id
        fiscal.integration_id = integration.id
        fiscal.provider = IntegrationProvider.AQSI
        fiscal.status = FiscalizationStatus.SUCCEEDED
        fiscal.external_id = None
        fiscal.external_receipt_id = receipt_id
        fiscal.fiscal_amount = amount
        fiscal.currency = "RUB"
        fiscal.error_code = fiscal.error_message = None
        fiscal.updated_by_id = owner
        fiscal.provider_metadata = {
            "recovery": audit,
            "prior_records": prior,
            "receipt_evidence": receipt,
            "payment_ids": [str(p.id) for p in new_payments],
        }
        for job in sale.payment_jobs:
            job.status = PaymentJobStatus.COMPLETED
            job.next_retry_at = None
            job.completed_at = recorded_at
            job.last_error = "MANUAL_RECONCILIATION: receipt recorded; no remote cancellation sent"
            job.updated_by_id = owner
        if sale.fiscalization_job:
            raise ValueError("Unexpected existing fiscalization job; separate review required")
        InventoryService(session).create_sale_movements_once(
            sale.id,
            [(i.variant_id, i.quantity) for i in sale.items if i.variant_id],
            actor_id=owner,
        )
        sale.inventory_posted_at = sale.inventory_posted_at or recorded_at
        sale.paid_at = sale.completed_at = datetime.fromisoformat(receipt["info"]["dateTime"])
        sale.status = SaleStatus.COMPLETED
        sale.cancelled_at = sale.cancelled_by_id = None
        sale.updated_by_id = owner
        sale.version += 1
        session.flush()
        results.append(
            {"doc": doc, "sale": sale.sale_number, "action": "linked" if prior else "imported"}
        )
    return results


def main() -> None:
    """Run a rollback-only rehearsal by default; --apply is explicit and atomic."""
    import core.main  # noqa: F401 -- register every model before relationship resolution
    from core.database import SessionLocal

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    raw = args.evidence.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    _require(digest == args.sha256, "Evidence checksum mismatch")
    with SessionLocal() as session:
        try:
            result = recover_receipts(session, json.loads(raw), digest)
            if args.apply:
                session.commit()
            else:
                session.rollback()
        except Exception:
            session.rollback()
            raise
    print(json.dumps({"applied": args.apply, "receipts": result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
