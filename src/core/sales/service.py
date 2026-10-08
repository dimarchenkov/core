from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal

from sqlalchemy.orm import Session

from core.catalog.barcodes import normalize_barcode
from core.catalog.models import CatalogVariant
from core.pricing.enums import PriceType
from core.pricing.repository import PriceRepository
from core.sales.enums import SaleDiscountType, SaleItemSource, SaleStatus
from core.sales.models import Sale, SaleItem
from core.sales.repository import SaleRepository
from core.shared.db import UUIDv7
from core.shared.money import DEFAULT_CURRENCY, quantize_money


class SaleNotFoundError(Exception):
    """Raised when a Sale does not exist or belongs to another operator."""


class SaleNotDraftError(Exception):
    """Raised when a cart mutation targets a non-draft Sale."""


class SaleItemNotFoundError(Exception):
    """Raised when a SaleItem does not belong to the target Sale."""


class SaleVariantUnavailableError(Exception):
    """Raised when a Variant is missing, archived, or inactive."""


class SaleBarcodeNotFoundError(Exception):
    """Raised when no operational Variant owns the scanned canonical barcode."""


class SalePriceMissingError(Exception):
    """Raised when an operational Variant has no current retail price."""


class SaleDiscountInvalidError(Exception):
    """Raised when a receipt discount cannot produce a safe payable fiscal snapshot."""


class SaleManualItemInvalidError(Exception):
    """Raised when a manual/open SaleItem is incomplete or invalid."""


class SaleService:
    """Application commands for persistent operator-owned draft Sales."""

    def __init__(self, session: Session) -> None:
        """Create a Sales service bound to one database session."""
        self._session = session
        self._sales = SaleRepository(session)
        self._prices = PriceRepository(session)

    def create_sale(self, *, owner_id: UUIDv7) -> Sale:
        """Create and commit one empty persistent DRAFT Sale."""
        sale = self._new_sale(owner_id)
        self._commit()
        return sale

    def list_draft_sales(self, *, owner_id: UUIDv7) -> list[Sale]:
        """List all draft carts owned by the current operator."""
        return list(self._sales.list_drafts(owner_id))

    def get_sale(self, sale_id: UUIDv7, *, owner_id: UUIDv7) -> Sale:
        """Return one owned Sale, including cancelled history."""
        sale = self._sales.get_owned(sale_id, owner_id)
        if sale is None:
            raise SaleNotFoundError
        return sale

    def add_variant(
        self,
        sale_id: UUIDv7,
        variant_id: UUIDv7,
        *,
        owner_id: UUIDv7,
    ) -> Sale:
        """Add or increment a Variant after snapshotting its current retail facts."""
        variant, unit_price = self._resolve_variant_and_price(variant_id)
        sale = self._owned_draft_for_update(sale_id, owner_id)
        self._add_resolved_variant(sale, variant, unit_price, actor_id=owner_id)
        self._recalculate_and_commit(sale, actor_id=owner_id)
        return sale

    def add_variant_to_new_sale(
        self,
        variant_id: UUIDv7,
        *,
        owner_id: UUIDv7,
    ) -> Sale:
        """Atomically create a DRAFT Sale and add a priced Variant to it."""
        variant, unit_price = self._resolve_variant_and_price(variant_id)
        sale = self._new_sale(owner_id)
        self._add_resolved_variant(sale, variant, unit_price, actor_id=owner_id)
        self._recalculate_and_commit(sale, actor_id=owner_id)
        return sale

    def add_barcode(
        self,
        sale_id: UUIDv7,
        barcode: str,
        *,
        owner_id: UUIDv7,
    ) -> Sale:
        """Resolve an exact canonical barcode and add it to an owned DRAFT Sale."""
        variant, unit_price = self._resolve_barcode_and_price(barcode)
        sale = self._owned_draft_for_update(sale_id, owner_id)
        self._add_resolved_variant(sale, variant, unit_price, actor_id=owner_id)
        self._recalculate_and_commit(sale, actor_id=owner_id)
        return sale

    def add_barcode_to_new_sale(
        self,
        barcode: str,
        *,
        owner_id: UUIDv7,
    ) -> Sale:
        """Atomically create a DRAFT Sale only after a scan resolves and has a price."""
        variant, unit_price = self._resolve_barcode_and_price(barcode)
        sale = self._new_sale(owner_id)
        self._add_resolved_variant(sale, variant, unit_price, actor_id=owner_id)
        self._recalculate_and_commit(sale, actor_id=owner_id)
        return sale

    def add_manual_item(
        self,
        sale_id: UUIDv7,
        name: str,
        unit_price: Decimal,
        quantity: int,
        *,
        owner_id: UUIDv7,
    ) -> Sale:
        """Add one uncatalogued commercial snapshot without inventing stock identity."""
        normalized_name = name.strip()
        normalized_price = quantize_money(unit_price)
        if not normalized_name or normalized_price <= 0 or quantity <= 0:
            raise SaleManualItemInvalidError
        sale = self._owned_draft_for_update(sale_id, owner_id)
        item = SaleItem(
            sale_id=sale.id,
            source=SaleItemSource.MANUAL,
            variant_id=None,
            product_title_snapshot=None,
            variant_title_snapshot=None,
            display_label_snapshot=normalized_name,
            sku_snapshot=None,
            barcode_snapshot=None,
            unit_price=normalized_price,
            quantity=quantity,
            line_total=quantize_money(normalized_price * quantity),
            fiscal_line_total=Decimal("0.00"),
            fiscal_allocations=[],
            currency=DEFAULT_CURRENCY,
            created_by_id=owner_id,
            updated_by_id=owner_id,
        )
        sale.items.append(item)
        self._sales.add_item(item)
        self._recalculate_and_commit(sale, actor_id=owner_id)
        return sale

    def set_discount(
        self,
        sale_id: UUIDv7,
        discount_value: Decimal,
        *,
        owner_id: UUIDv7,
    ) -> Sale:
        """Set a DRAFT receipt-level percentage and persist its exact allocation."""
        normalized = discount_value.quantize(Decimal("0.01"))
        if normalized < 0 or normalized > Decimal("99.99"):
            raise SaleDiscountInvalidError
        sale = self._owned_draft_for_update(sale_id, owner_id)
        sale.discount_type = SaleDiscountType.PERCENT
        sale.discount_value = normalized
        self._recalculate_and_commit(sale, actor_id=owner_id)
        return sale

    def change_quantity(
        self,
        sale_id: UUIDv7,
        item_id: UUIDv7,
        delta: int,
        *,
        owner_id: UUIDv7,
    ) -> Sale:
        """Apply a relative quantity change, removing the line when it reaches zero."""
        if delta not in {-1, 1}:
            msg = "Quantity delta must be -1 or 1."
            raise ValueError(msg)
        sale = self._owned_draft_for_update(sale_id, owner_id)
        item = next((candidate for candidate in sale.items if candidate.id == item_id), None)
        if item is None:
            raise SaleItemNotFoundError
        next_quantity = item.quantity + delta
        if next_quantity <= 0:
            sale.items.remove(item)
            self._session.delete(item)
        else:
            item.quantity = next_quantity
            item.line_total = quantize_money(item.unit_price * next_quantity)
            item.updated_by_id = owner_id
        self._recalculate_and_commit(sale, actor_id=owner_id)
        return sale

    def remove_item(
        self,
        sale_id: UUIDv7,
        item_id: UUIDv7,
        *,
        owner_id: UUIDv7,
    ) -> Sale:
        """Remove one line from an owned DRAFT Sale."""
        sale = self._owned_draft_for_update(sale_id, owner_id)
        item = next((candidate for candidate in sale.items if candidate.id == item_id), None)
        if item is None:
            raise SaleItemNotFoundError
        sale.items.remove(item)
        self._session.delete(item)
        self._recalculate_and_commit(sale, actor_id=owner_id)
        return sale

    def cancel_sale(self, sale_id: UUIDv7, *, owner_id: UUIDv7) -> Sale:
        """Cancel a DRAFT Sale without inventory, payment, or fiscal side effects."""
        sale = self._owned_draft_for_update(sale_id, owner_id)
        sale.status = SaleStatus.CANCELLED
        sale.cancelled_at = datetime.now(UTC)
        sale.cancelled_by_id = owner_id
        sale.updated_by_id = owner_id
        self._commit()
        return sale

    def _new_sale(self, owner_id: UUIDv7) -> Sale:
        """Stage one empty DRAFT Sale with its business number and owner."""
        sale = Sale(
            sale_number=self._sales.next_sale_number(),
            owner_id=owner_id,
            status=SaleStatus.DRAFT,
            subtotal_amount=Decimal("0.00"),
            discount_type=SaleDiscountType.PERCENT,
            discount_value=Decimal("0.00"),
            discount_amount=Decimal("0.00"),
            total_amount=Decimal("0.00"),
            currency=DEFAULT_CURRENCY,
            created_by_id=owner_id,
            updated_by_id=owner_id,
        )
        self._sales.add(sale)
        self._session.flush()
        return sale

    def _owned_draft_for_update(self, sale_id: UUIDv7, owner_id: UUIDv7) -> Sale:
        """Load and lock one owned Sale and require its mutable DRAFT state."""
        sale = self._sales.get_owned_for_update(sale_id, owner_id)
        if sale is None:
            raise SaleNotFoundError
        if sale.status is not SaleStatus.DRAFT:
            raise SaleNotDraftError
        return sale

    def _resolve_variant_and_price(
        self,
        variant_id: UUIDv7,
    ) -> tuple[CatalogVariant, Decimal]:
        """Resolve an operational Variant and its current canonical retail price."""
        variant = self._sales.get_operational_variant(variant_id)
        if variant is None:
            raise SaleVariantUnavailableError
        return variant, self._current_retail_price(variant.id)

    def _resolve_barcode_and_price(self, barcode: str) -> tuple[CatalogVariant, Decimal]:
        """Normalize and resolve only the current canonical Variant barcode."""
        normalized = normalize_barcode(barcode)
        variant = self._sales.get_operational_variant_by_barcode(normalized)
        if variant is None:
            raise SaleBarcodeNotFoundError(normalized)
        return variant, self._current_retail_price(variant.id)

    def _current_retail_price(self, variant_id: UUIDv7) -> Decimal:
        """Return a normalized current retail price or reject the add command."""
        price = self._prices.get_current(variant_id, PriceType.RETAIL, at=datetime.now(UTC))
        if price is None:
            raise SalePriceMissingError
        return quantize_money(price.amount)

    def _add_resolved_variant(
        self,
        sale: Sale,
        variant: CatalogVariant,
        unit_price: Decimal,
        *,
        actor_id: UUIDv7,
    ) -> None:
        """Add a captured Variant snapshot or increment the existing unique line."""
        item = next(
            (candidate for candidate in sale.items if candidate.variant_id == variant.id),
            None,
        )
        if item is not None:
            item.quantity += 1
            item.line_total = quantize_money(item.unit_price * item.quantity)
            item.updated_by_id = actor_id
        else:
            variant_title = variant.title.strip()
            product_title = variant.product.title.strip()
            generic_titles = {"default", "default variant", "основной"}
            label = (
                product_title
                if variant_title.casefold() in generic_titles
                else f"{product_title} · {variant_title}"
            )
            item = SaleItem(
                sale_id=sale.id,
                source=SaleItemSource.CATALOG,
                variant_id=variant.id,
                product_title_snapshot=product_title,
                variant_title_snapshot=variant_title,
                display_label_snapshot=label,
                sku_snapshot=variant.sku,
                barcode_snapshot=variant.barcode,
                unit_price=unit_price,
                quantity=1,
                line_total=unit_price,
                fiscal_line_total=unit_price,
                fiscal_allocations=[{"unit_price": str(unit_price), "quantity": 1}],
                currency=DEFAULT_CURRENCY,
                created_by_id=actor_id,
                updated_by_id=actor_id,
            )
            sale.items.append(item)
            self._sales.add_item(item)

    @staticmethod
    def _recalculate_total(sale: Sale, *, actor_id: UUIDv7) -> None:
        """Recompute totals and allocate the payable amount to fiscal units exactly."""
        subtotal = quantize_money(sum((item.line_total for item in sale.items), Decimal("0")))
        discount = quantize_money(
            subtotal * sale.discount_value / Decimal("100")
        )
        total = quantize_money(subtotal - discount)
        unit_count = sum(item.quantity for item in sale.items)
        target_kopecks = int(total * 100)
        if subtotal > 0 and (total <= 0 or target_kopecks < unit_count):
            raise SaleDiscountInvalidError

        multiplier = (Decimal("100") - sale.discount_value) / Decimal("100")
        rows: list[tuple[int, SaleItem, int, Decimal]] = []
        allocated = 0
        for index, item in enumerate(sale.items):
            exact = item.unit_price * Decimal("100") * multiplier
            floor = int(exact.to_integral_value(rounding=ROUND_FLOOR))
            rows.append((index, item, floor, exact - floor))
            allocated += floor * item.quantity
        remainder = target_kopecks - allocated
        additions = {index: 0 for index in range(len(sale.items))}
        for index, item, _, _ in sorted(rows, key=lambda row: (-row[3], row[0])):
            added = min(item.quantity, remainder)
            additions[index] = added
            remainder -= added
        if remainder != 0:
            raise SaleDiscountInvalidError

        for index, item, floor, _ in rows:
            higher = additions[index]
            lower = item.quantity - higher
            if floor <= 0 and lower > 0:
                raise SaleDiscountInvalidError
            allocations: list[dict[str, object]] = []
            if higher:
                allocations.append(
                    {"unit_price": str(Decimal(floor + 1) / 100), "quantity": higher}
                )
            if lower:
                allocations.append(
                    {"unit_price": str(Decimal(floor) / 100), "quantity": lower}
                )
            item.fiscal_allocations = allocations
            item.fiscal_line_total = quantize_money(
                sum(
                    Decimal(str(allocation["unit_price"]))
                    * int(allocation["quantity"])
                    for allocation in allocations
                )
            )
            item.updated_by_id = actor_id

        if sum((item.fiscal_line_total for item in sale.items), Decimal("0")) != total:
            raise SaleDiscountInvalidError
        sale.subtotal_amount = subtotal
        sale.discount_amount = discount
        sale.total_amount = total
        sale.updated_by_id = actor_id

    def _recalculate_and_commit(self, sale: Sale, *, actor_id: UUIDv7) -> None:
        """Atomically persist one cart mutation and its recalculated fiscal snapshot."""
        try:
            self._recalculate_total(sale, actor_id=actor_id)
            self._commit()
        except Exception:
            self._session.rollback()
            raise

    def _commit(self) -> None:
        """Commit one Sales command and roll back its whole transaction on failure."""
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
