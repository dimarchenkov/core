from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from core.activity.enums import ActivityEntityType
from core.activity.models import ActivityEvent
from core.catalog.models import CatalogProduct, CatalogVariant, CatalogVariantBarcode
from core.catalog.schemas import CatalogDeletionDependency, CatalogTestDataPreflight
from core.intake.models import IntakeItemDraft, IntakeSession
from core.integrations.aqsi.models import Publication, PublicationAttempt
from core.inventory.enums import SourceType
from core.inventory.models import StockMovement
from core.media.enums import ImageLinkEntityType
from core.media.models import ImageLink
from core.pricing.models import Price
from core.receipt.models import Receipt, ReceiptItem
from core.rental.models import (
    RentalAssetRecord,
    RentalConditionPhotoRecord,
    RentalDamageRecord,
    RentalMaintenanceRecord,
    RentalOrderItemRecord,
)
from core.sales.models import SaleItem
from core.shared.db import UUIDv7


class CatalogTestDataNotFoundError(Exception):
    """Raised when a physical Product row no longer exists."""


class CatalogTestDataBlockedError(Exception):
    """Raised when the authoritative graph is not eligible for classification or purge."""

    def __init__(self, preflight: CatalogTestDataPreflight) -> None:
        """Retain the fresh graph projection for an HTTP conflict response."""
        super().__init__("Catalog test-data operation is blocked.")
        self.preflight = preflight


@dataclass(frozen=True)
class _TestGraph:
    """Materialized dependency graph for one Product test-data operation."""

    product: CatalogProduct
    variant_ids: tuple[UUIDv7, ...]
    intake_sessions: tuple[IntakeSession, ...]
    intake_item_ids: tuple[UUIDv7, ...]
    receipt_rows: tuple[Receipt, ...]
    receipt_item_ids: tuple[UUIDv7, ...]
    stock_movement_ids: tuple[UUIDv7, ...]
    rental_asset_ids: tuple[UUIDv7, ...]
    publication_ids: tuple[UUIDv7, ...]
    activity_event_ids: tuple[UUIDv7, ...]
    mixed_intake_count: int
    mixed_receipt_count: int
    mixed_receipt_movement_count: int
    unattributed_stock_count: int
    unclassified_rental_asset_count: int
    rental_order_item_count: int
    rental_maintenance_count: int
    rental_damage_count: int
    rental_condition_photo_count: int
    sale_item_count: int

    @property
    def intake_session_ids(self) -> tuple[UUIDv7, ...]:
        return tuple(row.id for row in self.intake_sessions)

    @property
    def receipt_ids(self) -> tuple[UUIDv7, ...]:
        return tuple(row.id for row in self.receipt_rows)


class CatalogTestDataService:
    """Classify and atomically purge explicitly disposable Product test graphs."""

    def __init__(self, session: Session) -> None:
        """Bind graph operations to the route-owned transaction."""
        self._session = session

    def preflight(self, product_id: UUIDv7) -> CatalogTestDataPreflight:
        """Return current graph impact without changing TEST classification."""
        return self._build_preflight(self._graph(product_id))

    def classify(self, product_id: UUIDv7) -> None:
        """Mark an exclusive legacy Product workflow graph as explicitly TEST."""
        graph = self._graph(product_id, lock=True)
        preflight = self._build_preflight(graph)
        if not preflight.can_classify:
            raise CatalogTestDataBlockedError(preflight)
        graph.product.is_test = True
        for intake in graph.intake_sessions:
            intake.is_test = True
        for receipt in graph.receipt_rows:
            receipt.is_test = True
        self._session.flush()

    def purge(self, product_id: UUIDv7) -> None:
        """Revalidate and physically delete one complete eligible TEST graph."""
        graph = self._graph(product_id, lock=True)
        preflight = self._build_preflight(graph)
        if not preflight.can_purge:
            raise CatalogTestDataBlockedError(preflight)
        self._delete_graph(graph)

    def _graph(self, product_id: UUIDv7, *, lock: bool = False) -> _TestGraph:
        product_statement = select(CatalogProduct).where(CatalogProduct.id == product_id)
        if lock:
            product_statement = product_statement.with_for_update()
        product = self._session.scalar(product_statement)
        if product is None:
            raise CatalogTestDataNotFoundError

        variants_statement = select(CatalogVariant.id).where(
            CatalogVariant.product_id == product.id
        )
        if lock:
            variants_statement = variants_statement.with_for_update()
        variant_ids = tuple(self._session.scalars(variants_statement).all())

        direct_items_statement = select(IntakeItemDraft).where(
            self._intake_reference_condition(product.id, variant_ids)
        )
        if lock:
            direct_items_statement = direct_items_statement.with_for_update()
        direct_items = tuple(self._session.scalars(direct_items_statement).all())
        intake_session_ids = tuple({item.session_id for item in direct_items})
        intake_sessions = self._rows_by_ids(IntakeSession, intake_session_ids, lock=lock)
        intake_items = self._rows_by_foreign_ids(
            IntakeItemDraft,
            IntakeItemDraft.session_id,
            intake_session_ids,
            lock=lock,
        )
        intake_item_ids = tuple(item.id for item in intake_items)
        mixed_intake_ids = {
            item.session_id
            for item in intake_items
            if not self._intake_item_belongs_to_scope(item, product.id, variant_ids)
        }

        linked_receipt_ids = {
            row.receipt_id for row in intake_sessions if row.receipt_id is not None
        }
        direct_receipt_items = self._rows_by_foreign_ids(
            ReceiptItem,
            ReceiptItem.variant_id,
            variant_ids,
            lock=lock,
        )
        linked_receipt_ids.update(item.receipt_id for item in direct_receipt_items)
        receipt_ids = tuple(linked_receipt_ids)
        receipt_rows = self._rows_by_ids(Receipt, receipt_ids, lock=lock)
        receipt_items = self._rows_by_foreign_ids(
            ReceiptItem,
            ReceiptItem.receipt_id,
            receipt_ids,
            lock=lock,
        )
        receipt_item_ids = tuple(item.id for item in receipt_items)
        mixed_receipt_ids = {
            item.receipt_id for item in receipt_items if item.variant_id not in variant_ids
        }

        stock_movements = self._rows_by_foreign_ids(
            StockMovement,
            StockMovement.variant_id,
            variant_ids,
            lock=lock,
        )
        receipt_id_set = set(receipt_ids)
        receipt_source_movements: tuple[StockMovement, ...] = ()
        if receipt_ids:
            receipt_movement_statement = select(StockMovement).where(
                StockMovement.source_type == SourceType.RECEIPT,
                StockMovement.source_id.in_(receipt_ids),
            )
            if lock:
                receipt_movement_statement = receipt_movement_statement.with_for_update()
            receipt_source_movements = tuple(
                self._session.scalars(receipt_movement_statement).all()
            )
        mixed_receipt_movements = tuple(
            movement
            for movement in receipt_source_movements
            if movement.variant_id not in variant_ids
        )
        unattributed_stock = tuple(
            movement
            for movement in stock_movements
            if movement.source_type != SourceType.RECEIPT
            or movement.source_id not in receipt_id_set
        )

        rental_assets = self._rows_by_foreign_ids(
            RentalAssetRecord,
            RentalAssetRecord.variant_id,
            variant_ids,
            lock=lock,
        )
        intake_item_id_set = set(intake_item_ids)
        unclassified_assets = tuple(
            asset
            for asset in rental_assets
            if asset.intake_item_id is None or asset.intake_item_id not in intake_item_id_set
        )
        rental_asset_ids = tuple(asset.id for asset in rental_assets)

        publications = self._rows_by_foreign_ids(
            Publication,
            Publication.variant_id,
            variant_ids,
            lock=lock,
        )
        publication_ids = tuple(row.id for row in publications)
        activity_events = self._activity_events(
            intake_session_ids,
            intake_item_ids,
            lock=lock,
        )

        return _TestGraph(
            product=product,
            variant_ids=variant_ids,
            intake_sessions=intake_sessions,
            intake_item_ids=intake_item_ids,
            receipt_rows=receipt_rows,
            receipt_item_ids=receipt_item_ids,
            stock_movement_ids=tuple(row.id for row in stock_movements),
            rental_asset_ids=rental_asset_ids,
            publication_ids=publication_ids,
            activity_event_ids=tuple(row.id for row in activity_events),
            mixed_intake_count=len(mixed_intake_ids),
            mixed_receipt_count=len(mixed_receipt_ids),
            mixed_receipt_movement_count=len(mixed_receipt_movements),
            unattributed_stock_count=len(unattributed_stock),
            unclassified_rental_asset_count=len(unclassified_assets),
            rental_order_item_count=self._count_for_ids(
                RentalOrderItemRecord,
                RentalOrderItemRecord.rental_asset_id,
                rental_asset_ids,
            ),
            rental_maintenance_count=self._count_for_ids(
                RentalMaintenanceRecord,
                RentalMaintenanceRecord.rental_asset_id,
                rental_asset_ids,
            ),
            rental_damage_count=self._count_for_ids(
                RentalDamageRecord,
                RentalDamageRecord.rental_asset_id,
                rental_asset_ids,
            ),
            rental_condition_photo_count=self._count_for_ids(
                RentalConditionPhotoRecord,
                RentalConditionPhotoRecord.rental_asset_id,
                rental_asset_ids,
            ),
            sale_item_count=self._count_for_ids(
                SaleItem,
                SaleItem.variant_id,
                variant_ids,
            ),
        )

    def _build_preflight(self, graph: _TestGraph) -> CatalogTestDataPreflight:
        dependencies = [
            self._dependency("products", "товар", 1),
            self._dependency("variants", "вариант", len(graph.variant_ids)),
            self._dependency(
                "barcodes",
                "штрихкод",
                self._count_for_ids(
                    CatalogVariantBarcode,
                    CatalogVariantBarcode.variant_id,
                    graph.variant_ids,
                ),
            ),
            self._dependency(
                "prices",
                "запись цены",
                self._count_for_ids(Price, Price.variant_id, graph.variant_ids),
            ),
            self._dependency("intakes", "приёмка", len(graph.intake_sessions)),
            self._dependency("intake_items", "позиция приёмки", len(graph.intake_item_ids)),
            self._dependency("receipts", "приход", len(graph.receipt_rows)),
            self._dependency("receipt_items", "позиция прихода", len(graph.receipt_item_ids)),
            self._dependency(
                "stock_movements",
                "складское движение",
                len(graph.stock_movement_ids),
            ),
            self._dependency(
                "rental_assets",
                "предмет аренды RentalAsset",
                len(graph.rental_asset_ids),
            ),
            self._dependency(
                "image_links",
                "связь с фото",
                self._image_link_count(graph.product.id, graph.variant_ids),
            ),
            self._dependency(
                "activity_events",
                "событие Intake",
                len(graph.activity_event_ids),
            ),
            self._dependency(
                "aqsi_publications",
                "локальная публикация AQSI",
                len(graph.publication_ids),
            ),
            self._dependency(
                "aqsi_attempts",
                "локальная попытка публикации AQSI",
                self._count_for_ids(
                    PublicationAttempt,
                    PublicationAttempt.publication_id,
                    graph.publication_ids,
                ),
            ),
        ]
        blockers = [
            self._dependency(
                "mixed_intakes",
                "приёмка с данными другого товара",
                graph.mixed_intake_count,
            ),
            self._dependency(
                "mixed_receipts",
                "приход с данными другого товара",
                graph.mixed_receipt_count,
            ),
            self._dependency(
                "mixed_receipt_movements",
                "движение другого товара в TEST-приходе",
                graph.mixed_receipt_movement_count,
            ),
            self._dependency(
                "unattributed_stock",
                "складское движение вне TEST-прихода",
                graph.unattributed_stock_count,
            ),
            self._dependency(
                "unclassified_rental_assets",
                "RentalAsset вне TEST-приёмки",
                graph.unclassified_rental_asset_count,
            ),
            self._dependency(
                "rental_order_items",
                "позиция заказа аренды",
                graph.rental_order_item_count,
            ),
            self._dependency(
                "rental_maintenance",
                "запись обслуживания аренды",
                graph.rental_maintenance_count,
            ),
            self._dependency(
                "rental_damage",
                "запись повреждения аренды",
                graph.rental_damage_count,
            ),
            self._dependency(
                "rental_condition_photos",
                "фото состояния аренды",
                graph.rental_condition_photo_count,
            ),
            self._dependency(
                "sale_items",
                "позиция продажи",
                graph.sale_item_count,
            ),
        ]
        if graph.product.is_test:
            blockers.extend(
                [
                    self._dependency(
                        "production_intakes",
                        "приёмка без TEST-маркера",
                        sum(not row.is_test for row in graph.intake_sessions),
                    ),
                    self._dependency(
                        "production_receipts",
                        "приход без TEST-маркера",
                        sum(not row.is_test for row in graph.receipt_rows),
                    ),
                ]
            )
        visible_blockers = [item for item in blockers if item is not None]
        warnings = [
            "Файлы Image не удаляются автоматически: удаляются только связи с фото.",
        ]
        if graph.publication_ids:
            warnings.append(
                "Локальная история AQSI будет удалена, но товар не будет удалён с кассы AQSI."
            )
        return CatalogTestDataPreflight(
            product_id=graph.product.id,
            title=graph.product.title,
            is_test=graph.product.is_test,
            can_classify=not graph.product.is_test and not visible_blockers,
            can_purge=graph.product.is_test and not visible_blockers,
            dependencies=[item for item in dependencies if item is not None],
            blockers=visible_blockers,
            warnings=warnings,
        )

    def _delete_graph(self, graph: _TestGraph) -> None:
        """Delete the verified graph in foreign-key order inside the caller transaction."""
        self._delete_by_ids(ActivityEvent, graph.activity_event_ids)
        self._delete_by_foreign_ids(
            PublicationAttempt,
            PublicationAttempt.publication_id,
            graph.publication_ids,
        )
        self._delete_by_ids(Publication, graph.publication_ids)
        self._delete_by_foreign_ids(Price, Price.variant_id, graph.variant_ids)
        self._delete_by_foreign_ids(
            CatalogVariantBarcode,
            CatalogVariantBarcode.variant_id,
            graph.variant_ids,
        )
        self._delete_by_ids(RentalAssetRecord, graph.rental_asset_ids)
        self._delete_by_ids(StockMovement, graph.stock_movement_ids)
        self._delete_by_ids(ReceiptItem, graph.receipt_item_ids)
        self._delete_by_ids(IntakeItemDraft, graph.intake_item_ids)
        self._delete_by_ids(IntakeSession, graph.intake_session_ids)
        self._delete_by_ids(Receipt, graph.receipt_ids)
        self._session.execute(
            delete(ImageLink).where(self._image_condition(graph.product.id, graph.variant_ids))
        )
        self._delete_by_ids(CatalogVariant, graph.variant_ids)
        self._session.execute(delete(CatalogProduct).where(CatalogProduct.id == graph.product.id))
        self._session.flush()

    @staticmethod
    def _intake_reference_condition(
        product_id: UUIDv7,
        variant_ids: tuple[UUIDv7, ...],
    ) -> object:
        return or_(
            IntakeItemDraft.product_id == product_id,
            IntakeItemDraft.variant_id.in_(variant_ids),
        )

    @staticmethod
    def _intake_item_belongs_to_scope(
        item: IntakeItemDraft,
        product_id: UUIDv7,
        variant_ids: tuple[UUIDv7, ...],
    ) -> bool:
        return item.product_id == product_id or item.variant_id in variant_ids

    @staticmethod
    def _image_condition(product_id: UUIDv7, variant_ids: tuple[UUIDv7, ...]) -> object:
        return or_(
            (ImageLink.entity_type == ImageLinkEntityType.CATALOG_PRODUCT)
            & (ImageLink.entity_id == product_id),
            (ImageLink.entity_type == ImageLinkEntityType.CATALOG_VARIANT)
            & ImageLink.entity_id.in_(variant_ids),
        )

    def _image_link_count(
        self,
        product_id: UUIDv7,
        variant_ids: tuple[UUIDv7, ...],
    ) -> int:
        return int(
            self._session.scalar(
                select(func.count(ImageLink.id)).where(
                    self._image_condition(product_id, variant_ids)
                )
            )
            or 0
        )

    def _activity_events(
        self,
        intake_session_ids: tuple[UUIDv7, ...],
        intake_item_ids: tuple[UUIDv7, ...],
        *,
        lock: bool,
    ) -> tuple[ActivityEvent, ...]:
        if not intake_session_ids and not intake_item_ids:
            return ()
        statement = select(ActivityEvent).where(
            or_(
                (ActivityEvent.entity_type == ActivityEntityType.INTAKE_SESSION)
                & ActivityEvent.entity_id.in_(intake_session_ids),
                (ActivityEvent.entity_type == ActivityEntityType.INTAKE_ITEM)
                & ActivityEvent.entity_id.in_(intake_item_ids),
            )
        )
        if lock:
            statement = statement.with_for_update()
        return tuple(self._session.scalars(statement).all())

    def _rows_by_ids(
        self,
        model: type,
        ids: tuple[UUIDv7, ...],
        *,
        lock: bool,
    ) -> tuple:
        if not ids:
            return ()
        statement = select(model).where(model.id.in_(ids))
        if lock:
            statement = statement.with_for_update()
        return tuple(self._session.scalars(statement).all())

    def _rows_by_foreign_ids(
        self,
        model: type,
        column: object,
        ids: tuple[UUIDv7, ...],
        *,
        lock: bool,
    ) -> tuple:
        if not ids:
            return ()
        statement = select(model).where(column.in_(ids))
        if lock:
            statement = statement.with_for_update()
        return tuple(self._session.scalars(statement).all())

    def _count_for_ids(self, model: type, column: object, ids: tuple[UUIDv7, ...]) -> int:
        if not ids:
            return 0
        return int(self._session.scalar(select(func.count(model.id)).where(column.in_(ids))) or 0)

    def _delete_by_ids(self, model: type, ids: tuple[UUIDv7, ...]) -> None:
        if ids:
            self._session.execute(delete(model).where(model.id.in_(ids)))

    def _delete_by_foreign_ids(
        self,
        model: type,
        column: object,
        ids: tuple[UUIDv7, ...],
    ) -> None:
        if ids:
            self._session.execute(delete(model).where(column.in_(ids)))

    @staticmethod
    def _dependency(
        code: str,
        label: str,
        count: int,
    ) -> CatalogDeletionDependency | None:
        if count == 0:
            return None
        return CatalogDeletionDependency(code=code, label=label, count=count)
