"""Offline recovery of one verified AQSI day plus operator-recorded cash sales.

The command is rollback-only unless ``--apply`` is supplied.  It never constructs or
calls payment/fiscal providers, so importing already printed receipts cannot create a
second charge or receipt.
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

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from core.catalog.models import CatalogVariant
from core.identity.models import User
from core.integrations.enums import IntegrationProvider
from core.integrations.models import Integration
from core.inventory.service import InventoryService
from core.sales.enums import (
    FiscalizationStatus,
    PaymentMethod,
    PaymentStatus,
    SaleItemSource,
    SaleStatus,
)
from core.sales.models import Fiscalization, PaymentAttempt, Sale, SaleItem
from core.sales.repository import SaleRepository

JsonObject = dict[str, Any]
RECOVERY_DATE = "2026-10-21"
RECOVERY_TIMEZONE = "Europe/Moscow"
EXPECTED_DOCUMENTS = set(range(1693, 1706))
EXPECTED_RECEIPT_TOTAL = Decimal("3780.00")
EXPECTED_OPERATOR_TOTAL = Decimal("2490.00")
DATE_ONLY_ANCHOR = datetime(2026, 10, 21, 12, tzinfo=ZoneInfo(RECOVERY_TIMEZONE))


def _require(condition: bool, message: str) -> None:
    """Fail closed when approved recovery input or database state is ambiguous."""
    if not condition:
        raise ValueError(message)


def _money(value: object) -> Decimal:
    """Convert AQSI minor units to exact roubles without binary float arithmetic."""
    amount = Decimal(str(value))
    _require(amount.is_finite() and amount >= 0 and amount == int(amount), "Invalid amount")
    return amount / 100


def _validate_receipt(receipt: JsonObject, slips: dict[str, JsonObject], device_id: int) -> None:
    """Validate fiscal identity, lines and independently exported acquiring evidence."""
    info = receipt["info"]
    _require(receipt["device"]["id"] == device_id, "Wrong AQSI device")
    _require(info["typeId"] == 1 and not receipt["isNonFiscal"], "Not a fiscal sale receipt")
    _require(not receipt.get("correctionInfo") and not receipt.get("originalId"), "Correction")
    _require(bool(info["docInfo"]["docFiscalAttributeInt"]), "Missing fiscal signature")
    receipt_time = datetime.fromisoformat(info["dateTime"])
    _require(receipt_time.tzinfo is not None, "Receipt time lacks timezone")
    _require(
        receipt_time.astimezone(ZoneInfo(RECOVERY_TIMEZONE)).date().isoformat() == RECOVERY_DATE,
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
            slip_id = part["slip"]["id"]
            slip = slips.get(slip_id)
            _require(slip is not None, "Missing independently exported slip")
            content = slip["content"]
            _require(content == part["slip"]["content"], "Slip evidence mismatch")
            approved = content["responseCode"] == "000" or (
                content.get("iface") == "sbp" and content["responseCode"] == "SUCCESS"
            )
            _require(content["type"] == "purchase" and approved, "Slip is not approved")
            _require(_money(content["amount"]) == paid, "Slip amount mismatch")
    _require(tender[0] + tender[1] == amount and not receipt.get("change"), "Tender mismatch")
    _require(tender[0] == _money(info["amounts"]["cash"]), "Cash total mismatch")
    _require(tender[1] == _money(info["amounts"]["cashless"]), "Card total mismatch")
    _require(
        all(not info["amounts"].get(key) for key in ("prepayment", "credit", "consideration")),
        "Unsupported settlement type",
    )


def _validate_inputs(
    session: Session,
    evidence: JsonObject,
    operator_manifest: JsonObject,
) -> tuple[Integration, User, list[JsonObject], list[JsonObject], dict[str, JsonObject]]:
    """Validate the immutable provider export and operator manifest before writing."""
    _require(
        evidence["date"] == RECOVERY_DATE and evidence["timezone"] == RECOVERY_TIMEZONE,
        "Wrong AQSI evidence scope",
    )
    _require(
        operator_manifest["date"] == RECOVERY_DATE
        and operator_manifest["timezone"] == RECOVERY_TIMEZONE,
        "Wrong operator manifest scope",
    )
    integration = session.get(Integration, UUID(evidence["integration_id"]))
    _require(
        integration is not None and integration.provider is IntegrationProvider.AQSI,
        "Missing AQSI integration",
    )
    owner = session.get(User, UUID(operator_manifest["owner_id"]))
    _require(owner is not None and owner.is_active, "Missing or inactive recovery owner")

    receipts = sorted(evidence["receipts"], key=lambda row: row["info"]["docInfo"]["docNumber"])
    _require(len(receipts) == 13, "Unexpected receipt count")
    _require(
        {row["info"]["docInfo"]["docNumber"] for row in receipts} == EXPECTED_DOCUMENTS,
        "Unexpected fiscal document set",
    )
    _require(len({row["id"] for row in receipts}) == len(receipts), "Duplicate receipt IDs")
    _require(
        sum((_money(row["info"]["sum"]) for row in receipts), Decimal(0)) == EXPECTED_RECEIPT_TOTAL,
        "Unexpected AQSI receipt total",
    )
    device_ids = {row["device"]["id"] for row in receipts}
    _require(len(device_ids) == 1, "Receipts contain multiple AQSI devices")
    device_id = device_ids.pop()
    slips = {row["id"]: row for row in evidence["slips"]}
    used_slips = [
        part["slip"]["id"]
        for receipt in receipts
        for part in receipt["payments"]
        if part["type"] == 1
    ]
    _require(
        len(used_slips) == len(set(used_slips)) and set(used_slips) == set(slips),
        "Duplicated or unmatched acquiring slips",
    )
    for receipt in receipts:
        _validate_receipt(receipt, slips, device_id)

    operator_sales = sorted(operator_manifest["sales"], key=lambda row: row["sequence"])
    _require(
        len(operator_sales) == 13
        and {row["sequence"] for row in operator_sales} == set(range(1, 14)),
        "Unexpected operator sale set",
    )
    operator_total = Decimal(0)
    for row in operator_sales:
        quantity = int(row["quantity"])
        unit_price = Decimal(str(row["unit_price"]))
        _require(quantity > 0 and unit_price > 0, "Invalid operator sale amount")
        _require(bool(row["label"]) and len(row["label"]) <= 511, "Invalid operator label")
        _require(row["source"] in {"manual", "catalog"}, "Invalid operator sale source")
        _require(
            (row["source"] == "catalog") == bool(row.get("variant_id")),
            "Catalog source/Variant mismatch",
        )
        operator_total += unit_price * quantity
    _require(operator_total == EXPECTED_OPERATOR_TOTAL, "Unexpected operator sale total")
    return integration, owner, receipts, operator_sales, slips


def _new_sale(session: Session, owner: User, amount: Decimal, occurred_at: datetime) -> Sale:
    """Create one historical, frozen Sale aggregate before attaching facts."""
    sale = Sale(
        sale_number=SaleRepository(session).next_sale_number(),
        owner_id=owner.id,
        status=SaleStatus.PAID,
        subtotal_amount=amount,
        total_amount=amount,
        discount_amount=Decimal(0),
        currency="RUB",
        paid_at=occurred_at,
        created_at=occurred_at,
        updated_at=occurred_at,
        created_by_id=owner.id,
        updated_by_id=owner.id,
    )
    session.add(sale)
    session.flush()
    return sale


def _add_receipt_sale(
    session: Session,
    integration: Integration,
    owner: User,
    receipt: JsonObject,
    evidence_digest: str,
) -> JsonObject:
    """Add one already-paid, already-fiscalized AQSI receipt as historical facts."""
    receipt_id = receipt["id"]
    doc = receipt["info"]["docInfo"]["docNumber"]
    amount = _money(receipt["info"]["sum"])
    existing = session.scalars(
        select(Fiscalization).where(Fiscalization.external_receipt_id == receipt_id)
    ).all()
    _require(len(existing) <= 1, "Duplicate receipt binding")
    if existing:
        fiscal = existing[0]
        sale = session.get(Sale, fiscal.sale_id)
        recovery = fiscal.provider_metadata.get("recovery", {})
        _require(
            sale is not None
            and sale.status is SaleStatus.COMPLETED
            and sale.total_amount == amount
            and fiscal.status is FiscalizationStatus.SUCCEEDED
            and fiscal.integration_id == integration.id
            and recovery.get("evidence_sha256") == evidence_digest,
            "Existing receipt binding does not match this recovery",
        )
        return {
            "kind": "aqsi_receipt",
            "doc": doc,
            "sale": sale.sale_number,
            "action": "already_recorded",
        }

    receipt_time = datetime.fromisoformat(receipt["info"]["dateTime"])
    sale = _new_sale(session, owner, amount, receipt_time)
    inventory_lines: list[tuple[UUID, int]] = []
    for position in receipt["positions"]:
        line = position["info"]
        external_id = position.get("externalId") or None
        variant = session.get(CatalogVariant, UUID(external_id)) if external_id else None
        _require(not external_id or variant is not None, f"Unknown Variant: {external_id}")
        quantity = int(line["quantity"])
        unit_price = _money(line["finalPrice"])
        line_total = _money(line["totalAmount"])
        sale.items.append(
            SaleItem(
                source=SaleItemSource.CATALOG if variant else SaleItemSource.MANUAL,
                variant_id=variant.id if variant else None,
                product_title_snapshot=variant.product.title if variant else None,
                variant_title_snapshot=variant.title if variant else None,
                display_label_snapshot=line["name"],
                sku_snapshot=variant.sku if variant else None,
                barcode_snapshot=variant.barcode if variant else None,
                unit_price=unit_price,
                quantity=quantity,
                line_total=line_total,
                fiscal_line_total=line_total,
                fiscal_allocations=[{"unit_price": str(unit_price), "quantity": quantity}],
                currency="RUB",
                created_at=receipt_time,
                updated_at=receipt_time,
                created_by_id=owner.id,
                updated_by_id=owner.id,
            )
        )
        if variant:
            inventory_lines.append((variant.id, quantity))
    audit = {
        "recovery_date": RECOVERY_DATE,
        "evidence_sha256": evidence_digest,
        "recorded_at": datetime.now(UTC).isoformat(),
        "basis": "Verified AQSI fiscal receipt export",
        "receipt_id": receipt_id,
        "fiscal_document": doc,
    }
    payments: list[PaymentAttempt] = []
    for index, part in enumerate(receipt["payments"], start=1):
        card = part["type"] == 1
        slip_id = part["slip"]["id"] if card else None
        if slip_id:
            duplicates = session.scalars(
                select(PaymentAttempt).where(
                    (PaymentAttempt.external_id == slip_id)
                    | (PaymentAttempt.provider_metadata["imported_slip_id"].as_string() == slip_id)
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
            idempotency_key=f"daily-recovery:{RECOVERY_DATE}:{receipt_id}:{index}",
            attempt_number=index,
            provider_metadata={"recovery": audit, "imported_slip_id": slip_id},
            created_at=receipt_time,
            updated_at=receipt_time,
            created_by_id=owner.id,
            updated_by_id=owner.id,
        )
        sale.payments.append(payment)
        payments.append(payment)
    session.flush()
    sale.fiscalization = Fiscalization(
        sale_id=sale.id,
        payment_attempt_id=payments[0].id,
        integration_id=integration.id,
        provider=IntegrationProvider.AQSI,
        status=FiscalizationStatus.SUCCEEDED,
        idempotency_key=f"daily-recovery:{RECOVERY_DATE}:{receipt_id}:fiscal",
        external_receipt_id=receipt_id,
        fiscal_amount=amount,
        currency="RUB",
        provider_metadata={
            "recovery": audit,
            "receipt_evidence": receipt,
            "payment_ids": [str(payment.id) for payment in payments],
        },
        created_at=receipt_time,
        updated_at=receipt_time,
        created_by_id=owner.id,
        updated_by_id=owner.id,
    )
    session.flush()
    recorded_at = datetime.now(UTC)
    InventoryService(session).create_sale_movements_once(
        sale.id, inventory_lines, actor_id=owner.id
    )
    sale.inventory_posted_at = recorded_at
    sale.completed_at = receipt_time
    sale.status = SaleStatus.COMPLETED
    session.flush()
    return {"kind": "aqsi_receipt", "doc": doc, "sale": sale.sale_number, "action": "imported"}


def _add_operator_sale(
    session: Session,
    owner: User,
    row: JsonObject,
    manifest_digest: str,
) -> JsonObject:
    """Add one operator-confirmed cash-without-receipt Sale without any provider call."""
    sequence = int(row["sequence"])
    idempotency_key = f"daily-recovery:{RECOVERY_DATE}:operator:{sequence}:payment"
    existing = session.scalar(
        select(PaymentAttempt).where(PaymentAttempt.idempotency_key == idempotency_key)
    )
    amount = Decimal(str(row["unit_price"])) * int(row["quantity"])
    if existing:
        sale = session.get(Sale, existing.sale_id)
        recovery = existing.provider_metadata.get("recovery", {})
        _require(
            sale is not None
            and sale.status is SaleStatus.COMPLETED
            and sale.total_amount == amount
            and existing.status is PaymentStatus.SUCCEEDED
            and existing.payment_method is PaymentMethod.CASH
            and sale.fiscalization is not None
            and sale.fiscalization.status is FiscalizationStatus.SKIPPED
            and recovery.get("manifest_sha256") == manifest_digest,
            "Existing operator sale does not match this recovery",
        )
        return {
            "kind": "cash_without_receipt",
            "sequence": sequence,
            "sale": sale.sale_number,
            "action": "already_recorded",
        }

    variant = None
    if row["source"] == "catalog":
        variant = session.get(CatalogVariant, UUID(row["variant_id"]))
        _require(variant is not None and variant.is_active, "Missing active catalog Variant")
    sale = _new_sale(session, owner, amount, DATE_ONLY_ANCHOR)
    quantity = int(row["quantity"])
    unit_price = Decimal(str(row["unit_price"]))
    sale.items.append(
        SaleItem(
            source=SaleItemSource.CATALOG if variant else SaleItemSource.MANUAL,
            variant_id=variant.id if variant else None,
            product_title_snapshot=variant.product.title if variant else None,
            variant_title_snapshot=variant.title if variant else None,
            display_label_snapshot=row["label"],
            sku_snapshot=variant.sku if variant else None,
            barcode_snapshot=variant.barcode if variant else None,
            unit_price=unit_price,
            quantity=quantity,
            line_total=amount,
            fiscal_line_total=amount,
            fiscal_allocations=[{"unit_price": str(unit_price), "quantity": quantity}],
            currency="RUB",
            created_at=DATE_ONLY_ANCHOR,
            updated_at=DATE_ONLY_ANCHOR,
            created_by_id=owner.id,
            updated_by_id=owner.id,
        )
    )
    audit = {
        "recovery_date": RECOVERY_DATE,
        "manifest_sha256": manifest_digest,
        "recorded_at": datetime.now(UTC).isoformat(),
        "basis": "Operator-confirmed historical cash sale without receipt",
        "sequence": sequence,
        "occurred_at_precision": "date_only",
    }
    payment = PaymentAttempt(
        sale_id=sale.id,
        integration_id=None,
        provider=None,
        payment_method=PaymentMethod.CASH,
        requested_amount=amount,
        fiscalization_required=False,
        currency="RUB",
        status=PaymentStatus.SUCCEEDED,
        idempotency_key=idempotency_key,
        attempt_number=1,
        provider_metadata={"confirmed_by_operator": True, "recovery": audit},
        created_at=DATE_ONLY_ANCHOR,
        updated_at=DATE_ONLY_ANCHOR,
        created_by_id=owner.id,
        updated_by_id=owner.id,
    )
    sale.payments.append(payment)
    session.flush()
    sale.fiscalization = Fiscalization(
        sale_id=sale.id,
        payment_attempt_id=payment.id,
        integration_id=None,
        provider=None,
        status=FiscalizationStatus.SKIPPED,
        idempotency_key=f"daily-recovery:{RECOVERY_DATE}:operator:{sequence}:fiscal",
        fiscal_amount=amount,
        currency="RUB",
        provider_metadata={
            "reason": "operator_selected_cash_without_receipt",
            "recovery": audit,
        },
        created_at=DATE_ONLY_ANCHOR,
        updated_at=DATE_ONLY_ANCHOR,
        created_by_id=owner.id,
        updated_by_id=owner.id,
    )
    session.flush()
    if variant:
        InventoryService(session).create_sale_movements_once(
            sale.id, [(variant.id, quantity)], actor_id=owner.id
        )
    sale.inventory_posted_at = datetime.now(UTC)
    sale.completed_at = DATE_ONLY_ANCHOR
    sale.status = SaleStatus.COMPLETED
    session.flush()
    return {
        "kind": "cash_without_receipt",
        "sequence": sequence,
        "sale": sale.sale_number,
        "action": "imported",
    }


def recover_daily_sales(
    session: Session,
    evidence: JsonObject,
    evidence_digest: str,
    operator_manifest: JsonObject,
    manifest_digest: str,
) -> list[JsonObject]:
    """Import the approved day atomically and idempotently without network IO."""
    integration, owner, receipts, operator_sales, _ = _validate_inputs(
        session, evidence, operator_manifest
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(
            text(
                "LOCK TABLE sales, sale_items, payment_attempts, fiscalizations, "
                "payment_jobs, fiscalization_jobs, stock_movements "
                "IN SHARE ROW EXCLUSIVE MODE"
            )
        )
    results = [
        _add_receipt_sale(session, integration, owner, receipt, evidence_digest)
        for receipt in receipts
    ]
    results.extend(
        _add_operator_sale(session, owner, row, manifest_digest) for row in operator_sales
    )
    return results


def main() -> None:
    """Run a rollback-only rehearsal by default; require explicit checksums and --apply."""
    import core.main  # noqa: F401 -- register all models before ORM configuration
    from core.database import SessionLocal

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("operator_manifest", type=Path)
    parser.add_argument("--evidence-sha256", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    evidence_raw = args.evidence.read_bytes()
    manifest_raw = args.operator_manifest.read_bytes()
    evidence_digest = hashlib.sha256(evidence_raw).hexdigest()
    manifest_digest = hashlib.sha256(manifest_raw).hexdigest()
    _require(evidence_digest == args.evidence_sha256, "Evidence checksum mismatch")
    _require(manifest_digest == args.manifest_sha256, "Manifest checksum mismatch")
    with SessionLocal() as session:
        try:
            result = recover_daily_sales(
                session,
                json.loads(evidence_raw),
                evidence_digest,
                json.loads(manifest_raw),
                manifest_digest,
            )
            if args.apply:
                session.commit()
            else:
                session.rollback()
        except Exception:
            session.rollback()
            raise
    print(json.dumps({"applied": args.apply, "sales": result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
