from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from core.config import get_settings
from core.rental.agreement_models import (
    RentalAgreementRecord,
    RentalAgreementSettingsRecord,
    RentalAgreementTemplateVersionRecord,
)
from core.rental.agreement_renderer import RentalAgreementRenderer
from core.rental.agreement_schemas import (
    AgreementPreviewRead,
    AgreementSettingsContext,
    AgreementTemplateCreate,
    AgreementTemplateVersionRead,
    RentalAgreementRead,
    RentalAgreementSettingsRead,
    RentalAgreementSettingsWrite,
)
from core.rental.agreement_template import (
    DEFAULT_RENTAL_AGREEMENT_TEMPLATE,
    DEFAULT_RENTAL_AGREEMENT_TEMPLATE_VERSION,
    RENTAL_AGREEMENT_TEMPLATE_V1,
    SUPPORTED_AGREEMENT_PLACEHOLDERS,
)
from core.rental.enums import RentalSealStatus
from core.rental.models import RentalAssetRecord, RentalOrderItemRecord, RentalOrderRecord
from core.rental.order_enums import RentalOrderStatus
from core.shared.db import UUIDv7


class RentalAgreementNotFoundError(Exception):
    """Raised when an immutable agreement does not exist."""


class RentalAgreementOrderInvalidError(Exception):
    """Raised when an agreement cannot be generated for the current order state."""


class RentalAgreementSettingsIncompleteError(Exception):
    """Raised when required lessor facts are missing."""


class RentalAgreementTermsLockedError(Exception):
    """Raised when generated agreement facts would otherwise be changed silently."""


class RentalAgreementTemplateService:
    """Manage append-only Markdown templates and current lessor settings."""

    def __init__(self, session: Session) -> None:
        """Create template/settings workflows in the caller's transaction session."""
        self._session = session
        self._renderer = RentalAgreementRenderer()

    def context(self) -> AgreementSettingsContext:
        """Return current settings, template source, placeholder help, and history."""
        settings = self._ensure_settings()
        current = self._ensure_default_template()
        history = self._session.scalars(
            select(RentalAgreementTemplateVersionRecord).order_by(
                RentalAgreementTemplateVersionRecord.template_version.desc()
            )
        ).all()
        return AgreementSettingsContext(
            settings=self._settings_read(settings),
            current_template=AgreementTemplateVersionRead.model_validate(current),
            history=[AgreementTemplateVersionRead.model_validate(item) for item in history],
            supported_placeholders=sorted(SUPPORTED_AGREEMENT_PLACEHOLDERS),
        )

    def update_settings(
        self, data: RentalAgreementSettingsWrite, *, actor_id: UUIDv7
    ) -> RentalAgreementSettingsRead:
        """Update lessor facts used only by agreements generated in the future."""
        settings = self._ensure_settings(commit=False)
        for key, value in data.model_dump().items():
            setattr(settings, key, value.strip())
        settings.updated_by_id = actor_id
        self._session.commit()
        self._session.refresh(settings)
        return self._settings_read(settings)

    def create_template(
        self, data: AgreementTemplateCreate, *, actor_id: UUIDv7
    ) -> AgreementTemplateVersionRead:
        """Append a meaningful immutable version; never mutate an earlier source."""
        self._renderer.validate(data.markdown_source)
        current = self._ensure_default_template()
        digest = sha256(data.markdown_source.encode()).hexdigest()
        if digest == current.content_hash:
            return AgreementTemplateVersionRead.model_validate(current)
        next_version = (
            int(
                self._session.scalar(
                    select(func.max(RentalAgreementTemplateVersionRecord.template_version))
                )
                or 0
            )
            + 1
        )
        record = RentalAgreementTemplateVersionRecord(
            template_version=next_version,
            markdown_source=data.markdown_source,
            content_hash=digest,
            change_note=data.change_note.strip() if data.change_note else None,
            created_by_id=actor_id,
        )
        self._session.add(record)
        self._session.commit()
        self._session.refresh(record)
        return AgreementTemplateVersionRead.model_validate(record)

    def preview_template(self, markdown_source: str) -> AgreementPreviewRead:
        """Render safe sample content without creating a version or agreement."""
        settings = self._ensure_settings()
        values = self._sample_values(settings)
        rendered = self._renderer.render_markdown(markdown_source, values)
        return AgreementPreviewRead(
            rendered_markdown=rendered,
            rendered_html=self._renderer.render_html(rendered),
        )

    def _ensure_settings(self, *, commit: bool = True) -> RentalAgreementSettingsRecord:
        record = self._session.scalar(
            select(RentalAgreementSettingsRecord).where(
                RentalAgreementSettingsRecord.settings_key == "default"
            )
        )
        if record is None:
            record = RentalAgreementSettingsRecord(settings_key="default", agreement_city="Москва")
            self._session.add(record)
            if commit:
                self._session.commit()
                self._session.refresh(record)
            else:
                self._session.flush()
        return record

    def _ensure_default_template(
        self, *, commit: bool = True
    ) -> RentalAgreementTemplateVersionRecord:
        record = self._session.scalar(
            select(RentalAgreementTemplateVersionRecord).order_by(
                RentalAgreementTemplateVersionRecord.template_version.desc()
            )
        )
        if record is None:
            self._renderer.validate(RENTAL_AGREEMENT_TEMPLATE_V1)
            record = RentalAgreementTemplateVersionRecord(
                template_version=1,
                markdown_source=RENTAL_AGREEMENT_TEMPLATE_V1,
                content_hash=sha256(RENTAL_AGREEMENT_TEMPLATE_V1.encode()).hexdigest(),
                change_note="Initial Sprint 4.17 template",
            )
            self._session.add(record)
            self._session.flush()

        legacy_hash = sha256(RENTAL_AGREEMENT_TEMPLATE_V1.encode()).hexdigest()
        if record.template_version == 1 and record.content_hash == legacy_hash:
            self._renderer.validate(DEFAULT_RENTAL_AGREEMENT_TEMPLATE)
            record = RentalAgreementTemplateVersionRecord(
                template_version=DEFAULT_RENTAL_AGREEMENT_TEMPLATE_VERSION,
                markdown_source=DEFAULT_RENTAL_AGREEMENT_TEMPLATE,
                content_hash=sha256(DEFAULT_RENTAL_AGREEMENT_TEMPLATE.encode()).hexdigest(),
                change_note="Sprint 4.17 PDF layout and handwriting fields",
            )
            self._session.add(record)
            self._session.flush()

        if commit:
            self._session.commit()
            self._session.refresh(record)
        return record

    @staticmethod
    def _settings_read(record: RentalAgreementSettingsRecord) -> RentalAgreementSettingsRead:
        return RentalAgreementSettingsRead.model_validate(record, from_attributes=True)

    @staticmethod
    def _sample_values(settings: RentalAgreementSettingsRecord) -> dict[str, str]:
        return {
            "agreement_number": "R-20261010-0001",
            "agreement_date": "10.10.2026",
            "agreement_city": settings.agreement_city or "Москва",
            "lessor_name": settings.lessor_name or "Арендодатель",
            "lessor_inn": settings.lessor_inn or "____________",
            "lessor_ogrnip": settings.lessor_ogrnip or "_______________",
            "lessor_address": settings.lessor_address or "________________",
            "lessor_phone": settings.lessor_phone or "________________",
            "rental_items": (
                "1. Тестовое оборудование; № RENT-000001; тариф «3 часа»; "
                "стоимость 350 ₽; обеспечительный платёж 2 000 ₽."
            ),
            "tariff_summary": "1. Тариф «3 часа», срок начинается при фактической выдаче.",
            "tariff_terms": (
                "Тариф «3 часа». Продолжительность — до 3 часов с момента фактической "
                "выдачи. Стоимость — 350 ₽. Стоимость фиксированная и при досрочном "
                "возврате не пересчитывается."
            ),
            "rental_total": "350",
            "deposit_total": "2 000",
        }


class RentalAgreementService:
    """Preview, generate, and reprint immutable whole-order Rental agreements."""

    def __init__(self, session: Session) -> None:
        """Create immutable agreement workflows in the caller's transaction session."""
        self._session = session
        self._renderer = RentalAgreementRenderer()
        self._timezone = ZoneInfo(get_settings().rental_timezone)

    def preview(self, order_id: UUIDv7) -> AgreementPreviewRead:
        """Render current facts without assigning a number or persisting an artifact."""
        order = self._require_ready_order(order_id)
        template = RentalAgreementTemplateService(self._session)._ensure_default_template()
        settings = RentalAgreementTemplateService(self._session)._ensure_settings()
        values, _ = self._values(order, settings, "ПРЕДПРОСМОТР", datetime.now(UTC))
        rendered = self._renderer.render_markdown(template.markdown_source, values)
        pdf = self._renderer.render_pdf(rendered, layout_version=template.template_version)
        return AgreementPreviewRead(
            rendered_markdown=rendered,
            rendered_html=self._renderer.render_html(rendered),
            template_version=template.template_version,
            page_count=self._renderer.page_count(pdf),
        )

    def generate(self, order_id: UUIDv7, *, actor_id: UUIDv7) -> RentalAgreementRead:
        """Generate one stable whole-order agreement without issuing equipment."""
        try:
            existing = self._by_order_record(order_id)
            if existing is not None:
                return self._read(existing)
            order = self._session.scalar(
                select(RentalOrderRecord)
                .where(RentalOrderRecord.id == order_id, RentalOrderRecord.deleted_at.is_(None))
                .with_for_update()
            )
            if order is None or order.status is not RentalOrderStatus.READY:
                raise RentalAgreementOrderInvalidError
            self._session.refresh(order, attribute_names=["items"])
            existing = self._by_order_record(order_id)
            if existing is not None:
                return self._read(existing)
            settings_service = RentalAgreementTemplateService(self._session)
            template = settings_service._ensure_default_template(commit=False)
            settings = settings_service._ensure_settings(commit=False)
            self._validate_settings(settings)
            generated_at = datetime.now(UTC)
            agreement_number = self._next_number(generated_at)
            values, items_snapshot = self._values(order, settings, agreement_number, generated_at)
            rendered = self._renderer.render_markdown(template.markdown_source, values)
            rendered_html = self._renderer.render_html(rendered)
            pdf_bytes = self._renderer.render_pdf(
                rendered, layout_version=template.template_version
            )
            record = RentalAgreementRecord(
                rental_order_id=order.id,
                template_version_id=template.id,
                agreement_number=agreement_number,
                generated_at=generated_at,
                rendered_markdown=rendered,
                rendered_html=rendered_html,
                lessor_snapshot={key: values[key] for key in self._lessor_keys()},
                items_snapshot=items_snapshot,
                pdf_bytes=pdf_bytes,
                pdf_sha256=sha256(pdf_bytes).hexdigest(),
                attestation_method="internal_hash",
                status="generated",
                created_by_id=actor_id,
            )
            self._session.add(record)
            self._session.commit()
            self._session.refresh(record)
            return self._read(record)
        except Exception:
            self._session.rollback()
            raise

    def get_by_order(self, order_id: UUIDv7) -> RentalAgreementRead | None:
        """Return the one generated agreement for an order, if any."""
        record = self._by_order_record(order_id)
        return self._read(record) if record is not None else None

    def get(self, agreement_id: UUIDv7) -> RentalAgreementRead:
        """Return immutable agreement metadata."""
        record = self._session.get(RentalAgreementRecord, agreement_id)
        if record is None:
            raise RentalAgreementNotFoundError
        return self._read(record)

    def pdf(self, agreement_id: UUIDv7) -> tuple[str, bytes, str]:
        """Return exact stored bytes; never re-render from current live facts."""
        record = self._session.get(RentalAgreementRecord, agreement_id)
        if record is None:
            raise RentalAgreementNotFoundError
        if sha256(record.pdf_bytes).hexdigest() != record.pdf_sha256:
            raise RuntimeError("Stored agreement PDF hash mismatch.")
        return record.agreement_number, record.pdf_bytes, record.pdf_sha256

    def has_valid_agreement(self, order_id: UUIDv7) -> bool:
        """Return whether the order has a generated canonical agreement."""
        record = self._session.scalar(
            select(RentalAgreementRecord).where(
                RentalAgreementRecord.rental_order_id == order_id,
                RentalAgreementRecord.status == "generated",
                RentalAgreementRecord.deleted_at.is_(None),
            )
        )
        return bool(
            record is not None and sha256(record.pdf_bytes).hexdigest() == record.pdf_sha256
        )

    def _require_ready_order(self, order_id: UUIDv7) -> RentalOrderRecord:
        order = self._session.get(RentalOrderRecord, order_id)
        if order is None or order.status is not RentalOrderStatus.READY:
            raise RentalAgreementOrderInvalidError
        return order

    def _by_order_record(self, order_id: UUIDv7) -> RentalAgreementRecord | None:
        return self._session.scalar(
            select(RentalAgreementRecord).where(RentalAgreementRecord.rental_order_id == order_id)
        )

    def _next_number(self, generated_at: datetime) -> str:
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            sequence = int(
                self._session.scalar(text("SELECT nextval('rental_agreement_number_seq')"))
            )
        else:
            count = self._session.scalar(select(func.count(RentalAgreementRecord.id))) or 0
            sequence = int(count) + 1
        local_date = generated_at.astimezone(self._timezone).strftime("%Y%m%d")
        return f"R-{local_date}-{sequence:04d}"

    def _values(
        self,
        order: RentalOrderRecord,
        settings: RentalAgreementSettingsRecord,
        agreement_number: str,
        generated_at: datetime,
    ) -> tuple[dict[str, str], list[dict[str, Any]]]:
        items_snapshot: list[dict[str, Any]] = []
        item_lines: list[str] = []
        summaries: list[str] = []
        terms: list[str] = []
        deposit_total = Decimal("0")
        rental_total = Decimal("0")
        for index, item in enumerate(order.items, start=1):
            snapshot = dict(item.tariff_snapshot or {})
            tariff_name = str(snapshot.get("tariff_name") or "Тариф")
            price = Decimal(str(item.agreed_price)) - Decimal(str(item.discount))
            deposit = Decimal(str(item.recommended_deposit or 0))
            rental_total += price
            deposit_total += deposit
            item_term = self._tariff_terms(item, snapshot, price)
            identification = self._agreement_identification(item.rental_asset_id)
            identification_text = self._agreement_identification_text(identification)
            item_lines.append(
                f"{index}. {item.title_snapshot}; № {item.asset_number_snapshot}; "
                f"тариф «{tariff_name}»; стоимость {self._money(price)} ₽; "
                f"обеспечительный платёж {self._money(deposit)} ₽"
                f"{identification_text}."
            )
            summaries.append(f"{index}. {tariff_name}: {self._timing_summary(item, snapshot)}")
            terms.append(f"{index}. {item.title_snapshot}: {item_term}")
            items_snapshot.append(
                {
                    "item_id": str(item.id),
                    "rental_asset_id": str(item.rental_asset_id),
                    "asset_number": item.asset_number_snapshot,
                    "title": item.title_snapshot,
                    "agreed_price": f"{price:.2f}",
                    "recommended_deposit": f"{deposit:.2f}",
                    "asset_identification": identification,
                    "tariff_snapshot": snapshot,
                    "rendered_tariff_terms": item_term,
                }
            )
        values = {
            "agreement_number": agreement_number,
            "agreement_date": generated_at.astimezone(self._timezone).strftime("%d.%m.%Y"),
            "agreement_city": settings.agreement_city,
            "lessor_name": settings.lessor_name,
            "lessor_inn": settings.lessor_inn,
            "lessor_ogrnip": settings.lessor_ogrnip,
            "lessor_address": settings.lessor_address,
            "lessor_phone": settings.lessor_phone,
            "rental_items": "\n\n".join(item_lines),
            "tariff_summary": "\n\n".join(summaries),
            "tariff_terms": "\n\n".join(terms),
            "rental_total": self._money(rental_total),
            "deposit_total": self._money(deposit_total),
        }
        return values, items_snapshot

    def _agreement_identification(self, asset_id: UUIDv7) -> dict[str, Any]:
        """Capture current optional asset identity inside the immutable agreement artifact."""
        asset = self._session.get(RentalAssetRecord, asset_id)
        if asset is None:
            return {"serial_number": None, "seals": [], "components": []}
        active_seals = sorted(
            (seal for seal in asset.seals if seal.status is RentalSealStatus.ACTIVE),
            key=lambda seal: (seal.location.casefold(), seal.seal_number.casefold()),
        )
        return {
            "serial_number": asset.serial_number,
            "seals": [
                {"location": seal.location, "seal_number": seal.seal_number}
                for seal in active_seals
            ],
            "components": [
                {
                    "component_id": str(component.id),
                    "name": component.name,
                    "note": component.note,
                }
                for component in sorted(
                    (value for value in asset.components if value.is_active),
                    key=lambda value: (value.sort_order, value.created_at),
                )
            ],
        }

    @staticmethod
    def _agreement_identification_text(identification: dict[str, Any]) -> str:
        """Render only present serial/seal facts without placeholder dashes."""
        parts: list[str] = []
        serial_number = identification.get("serial_number")
        if serial_number:
            parts.append(f"серийный номер {serial_number}")
        seals = identification.get("seals") or []
        if seals:
            parts.append(
                "пломбы: "
                + ", ".join(
                    f"{seal['location']} — {seal['seal_number']}" for seal in seals
                )
            )
        components = identification.get("components") or []
        if components:
            parts.append(
                "комплект: " + "; ".join(str(component["name"]) for component in components)
            )
        return f"; {'; '.join(parts)}" if parts else ""

    def _tariff_terms(
        self,
        item: RentalOrderItemRecord,
        snapshot: dict[str, Any],
        price: Decimal,
    ) -> str:
        name = str(snapshot.get("tariff_name") or "Тариф")
        grace = int(snapshot.get("grace_minutes") or 0)
        if snapshot.get("tariff_type") == "duration":
            minutes = int(snapshot.get("duration_minutes") or 0)
            duration = f"{minutes // 60} ч" if minutes % 60 == 0 else f"{minutes} мин"
            result = (
                f"Тариф «{name}». Продолжительность — до {duration} с момента фактической выдачи. "
                f"Стоимость — {self._money(price)} ₽. Стоимость тарифа фиксированная и при "
                "досрочном возврате в пределах оплаченного тарифного периода не пересчитывается."
            )
        else:
            start = snapshot.get("slot_start") or "—"
            end = snapshot.get("slot_end") or "—"
            result = (
                f"Тариф «{name}». Фиксированный временной слот {start}–{end}; срок возврата "
                f"не сдвигается при поздней выдаче. Стоимость — {self._money(price)} ₽."
            )
        if grace:
            result += f" Льготный период — {grace} мин; он не изменяет установленный срок возврата."
        return result

    def _timing_summary(self, item: RentalOrderItemRecord, snapshot: dict[str, Any]) -> str:
        if snapshot.get("tariff_type") == "duration":
            return f"до {snapshot.get('duration_minutes')} мин с момента фактической выдачи"
        due = self._local_datetime(item.due_at).strftime("%d.%m.%Y %H:%M") if item.due_at else "—"
        return f"фиксированный возврат до {due}"

    def _local_datetime(self, value: datetime) -> datetime:
        aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
        return aware.astimezone(self._timezone)

    @staticmethod
    def _money(value: Decimal) -> str:
        normalized = value.quantize(Decimal("0.01"))
        whole, fraction = f"{normalized:.2f}".split(".")
        grouped = f"{int(whole):,}".replace(",", " ")
        return grouped if fraction == "00" else f"{grouped},{fraction}"

    @staticmethod
    def _validate_settings(settings: RentalAgreementSettingsRecord) -> None:
        if not all(
            value.strip()
            for value in (
                settings.lessor_name,
                settings.lessor_inn,
                settings.lessor_ogrnip,
                settings.agreement_city,
            )
        ):
            raise RentalAgreementSettingsIncompleteError

    @staticmethod
    def _lessor_keys() -> tuple[str, ...]:
        return (
            "agreement_city",
            "lessor_name",
            "lessor_inn",
            "lessor_ogrnip",
            "lessor_address",
            "lessor_phone",
        )

    def _read(self, record: RentalAgreementRecord) -> RentalAgreementRead:
        template_version = self._session.scalar(
            select(RentalAgreementTemplateVersionRecord.template_version).where(
                RentalAgreementTemplateVersionRecord.id == record.template_version_id
            )
        )
        return RentalAgreementRead(
            id=record.id,
            rental_order_id=record.rental_order_id,
            template_version_id=record.template_version_id,
            template_version=int(template_version or 0),
            agreement_number=record.agreement_number,
            generated_at=record.generated_at,
            pdf_sha256=record.pdf_sha256,
            attestation_method=record.attestation_method,
            status=record.status,
        )
