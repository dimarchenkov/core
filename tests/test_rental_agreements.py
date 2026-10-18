from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.identity.models import User
from core.intake.models import IntakeItemDraft  # noqa: F401
from core.rental.agreement_models import (
    RentalAgreementRecord,
    RentalAgreementSettingsRecord,
    RentalAgreementTemplateVersionRecord,
)
from core.rental.agreement_renderer import (
    AgreementTemplateValidationError,
    RentalAgreementRenderer,
)
from core.rental.agreement_schemas import (
    AgreementTemplateCreate,
    RentalAgreementSettingsWrite,
)
from core.rental.agreement_service import (
    RentalAgreementService,
    RentalAgreementTemplateService,
    RentalAgreementTermsLockedError,
)
from core.rental.agreement_template import (
    DEFAULT_RENTAL_AGREEMENT_TEMPLATE,
    RENTAL_AGREEMENT_TEMPLATE_V1,
    SUPPORTED_AGREEMENT_PLACEHOLDERS,
    SUPPORTED_HANDWRITING_COMPONENTS,
)
from core.rental.enums import (
    AssetCondition,
    AssetPurpose,
    RentalAvailability,
    RentalSealStatus,
)
from core.rental.models import (
    RentalAssetComponentRecord,
    RentalAssetRecord,
    RentalAssetSealRecord,
    RentalOrderItemRecord,
    RentalOrderRecord,
)
from core.rental.order_enums import (
    RentalOrderItemStatus,
    RentalOrderStatus,
    RentalTimingSource,
)
from core.rental.order_service import RentalAgreementRequiredError, RentalOrderService
from core.shared.db import Base, generate_uuid_v7


@pytest.fixture
def session() -> Generator[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            RentalAssetRecord.__table__,
            RentalAssetComponentRecord.__table__,
            RentalAssetSealRecord.__table__,
            RentalOrderRecord.__table__,
            RentalOrderItemRecord.__table__,
            RentalAgreementSettingsRecord.__table__,
            RentalAgreementTemplateVersionRecord.__table__,
            RentalAgreementRecord.__table__,
        ],
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as database_session:
        yield database_session


def _configure_settings(session: Session) -> None:
    RentalAgreementTemplateService(session).update_settings(
        RentalAgreementSettingsWrite(
            lessor_name="ИП Иванов Иван Иванович",
            lessor_inn="123456789012",
            lessor_ogrnip="123456789012345",
            agreement_city="Москва",
            lessor_address="ул. Тестовая, 1",
            lessor_phone="+7 999 000-00-00",
        ),
        actor_id=generate_uuid_v7(),
    )


def _ready_order(
    session: Session,
    *,
    item_count: int = 1,
    requires_agreement: bool = True,
) -> RentalOrderRecord:
    now = datetime.now(UTC)
    marker = str(generate_uuid_v7())[-6:]
    order = RentalOrderRecord(
        order_number=f"RORD-AG-{item_count}-{int(requires_agreement)}-{marker}",
        customer_id=generate_uuid_v7(),
        customer_name_snapshot="Не используется в договоре",
        customer_phone_snapshot="+79990000000",
        status=RentalOrderStatus.READY,
        planned_start_at=now,
        planned_return_at=now + timedelta(hours=5),
        issued_at=None,
        closed_at=None,
        deposit_amount=Decimal("0.00"),
    )
    for index in range(item_count):
        asset = RentalAssetRecord(
            asset_number=f"RENT-AG-{marker}-{index}",
            variant_id=generate_uuid_v7(),
            purpose=AssetPurpose.RENTAL,
            condition=AssetCondition.NEW,
            availability=RentalAvailability.AVAILABLE,
        )
        session.add(asset)
        session.flush()
        order.items.append(
            RentalOrderItemRecord(
                rental_asset_id=asset.id,
                asset_number_snapshot=asset.asset_number,
                title_snapshot=f"Оборудование {index + 1}",
                agreed_price=Decimal("350.00"),
                discount=Decimal("0.00"),
                rental_tariff_id=generate_uuid_v7(),
                rental_tariff_version=1,
                timing_source=RentalTimingSource.TARIFF,
                tariff_snapshot={
                    "tariff_name": "3 часа",
                    "tariff_type": "duration",
                    "duration_minutes": 180,
                    "grace_minutes": 30,
                    "requires_agreement": requires_agreement,
                    "canonical_tariff_terms": "3 часа · 350 ₽",
                },
                planned_rental_date=date.today(),
                planned_start_at=None,
                issued_at=None,
                due_at=None,
                recommended_deposit=Decimal("2000.00"),
                status=RentalOrderItemStatus.PREPARED,
            )
        )
    session.add(order)
    session.commit()
    return order


def test_template_versions_are_append_only_and_unknown_placeholders_fail(
    session: Session,
) -> None:
    service = RentalAgreementTemplateService(session)
    context = service.context()

    assert context.current_template.template_version == 2
    assert [item.template_version for item in context.history] == [2, 1]
    assert "именуемая далее **«Арендодатель»**" in context.current_template.markdown_source
    assert "именуемый далее **«Арендодатель»**" in context.history[-1].markdown_source
    assert "passport_number" not in context.supported_placeholders
    assert "customer_passport" not in SUPPORTED_AGREEMENT_PLACEHOLDERS
    with pytest.raises(AgreementTemplateValidationError):
        service.create_template(
            AgreementTemplateCreate(markdown_source="# {{ passport_number }}"),
            actor_id=generate_uuid_v7(),
        )
    with pytest.raises(AgreementTemplateValidationError):
        service.create_template(
            AgreementTemplateCreate(markdown_source="[[handwriting:passport_scan]]"),
            actor_id=generate_uuid_v7(),
        )

    source_v2 = context.current_template.markdown_source
    version_v3 = service.create_template(
        AgreementTemplateCreate(
            markdown_source=source_v2.replace(
                "Замечания при выдаче:", "Замечания при выдаче оборудования:", 1
            ),
            change_note="Test change",
        ),
        actor_id=generate_uuid_v7(),
    )

    assert version_v3.template_version == 3
    history = service.context().history
    assert [item.template_version for item in history] == [3, 2, 1]
    assert history[1].markdown_source == source_v2
    assert history[2].markdown_source == RENTAL_AGREEMENT_TEMPLATE_V1
    preview = service.preview_template("№ {{ agreement_number }} · {{ rental_total }} ₽")
    assert preview.rendered_markdown == "№ R-20261010-0001 · 350 ₽"


def test_new_agreement_uses_latest_template_and_gets_a_unique_number(
    session: Session,
) -> None:
    _configure_settings(session)
    templates = RentalAgreementTemplateService(session)
    first_order = _ready_order(session)
    agreements = RentalAgreementService(session)
    first = agreements.generate(first_order.id, actor_id=generate_uuid_v7())
    first_pdf = agreements.pdf(first.id)[1]

    current = templates.context().current_template
    second_template = templates.create_template(
        AgreementTemplateCreate(
            markdown_source=current.markdown_source.replace(
                "Замечания при выдаче:", "Замечания при выдаче оборудования:", 1
            ),
            change_note="New agreement version",
        ),
        actor_id=generate_uuid_v7(),
    )
    second_order = _ready_order(session)
    second = agreements.generate(second_order.id, actor_id=generate_uuid_v7())

    assert first.agreement_number != second.agreement_number
    assert first.template_version == 2
    assert second.template_version == second_template.template_version == 3
    assert agreements.pdf(first.id)[1] == first_pdf


def test_v2_layout_contract_uses_real_pen_fields_without_changing_legal_clauses() -> None:
    renderer = RentalAgreementRenderer()

    assert 9.5 <= renderer.body_font_pt <= 10.5
    assert 1.15 <= renderer.body_leading_pt / renderer.body_font_pt <= 1.25
    assert 10.5 <= renderer.heading_font_pt <= 11
    assert 12 <= renderer.page_margin_mm <= 15
    assert renderer.identity_field_height_mm >= 8
    assert renderer.remarks_field_height_mm >= 8
    assert renderer.signature_field_height_mm >= 10
    assert renderer.deposit_field_height_mm >= 10
    assert "_" * 10 not in DEFAULT_RENTAL_AGREEMENT_TEMPLATE
    assert {
        "tenant_name",
        "passport",
        "passport_authority",
        "passport_date_phone",
        "issue_time",
        "issue_remarks",
        "issue_signatures",
        "return_time",
        "return_remarks",
        "deposit",
        "return_signatures",
    } <= SUPPORTED_HANDWRITING_COMPONENTS

    legacy_clauses = {
        line
        for line in RENTAL_AGREEMENT_TEMPLATE_V1.splitlines()
        if line[:1].isdigit() and "." in line
    }
    assert legacy_clauses <= set(DEFAULT_RENTAL_AGREEMENT_TEMPLATE.splitlines())


@pytest.mark.parametrize("item_count", [1, 2, 3])
def test_agreement_is_one_immutable_two_page_pdf_for_multi_item_order(
    session: Session, item_count: int
) -> None:
    _configure_settings(session)
    order = _ready_order(session, item_count=item_count)
    service = RentalAgreementService(session)

    preview = service.preview(order.id)
    agreement = service.generate(order.id, actor_id=generate_uuid_v7())
    repeated = service.generate(order.id, actor_id=generate_uuid_v7())
    number, pdf_bytes, digest = service.pdf(agreement.id)

    assert preview.page_count == 2
    assert agreement.id == repeated.id
    assert agreement.agreement_number == repeated.agreement_number == number
    assert pdf_bytes.startswith(b"%PDF-")
    assert digest == agreement.pdf_sha256 == sha256(pdf_bytes).hexdigest()
    assert service.pdf(agreement.id)[1] == pdf_bytes
    record = session.get(RentalAgreementRecord, agreement.id)
    assert record is not None
    assert len(record.items_snapshot) == item_count
    assert all(
        f"Оборудование {index}" in record.rendered_markdown for index in range(1, item_count + 1)
    )
    assert order.status is RentalOrderStatus.READY
    assert order.issued_at is None


def test_historical_pdf_ignores_later_template_settings_and_order_snapshot_changes(
    session: Session,
) -> None:
    _configure_settings(session)
    order = _ready_order(session)
    agreements = RentalAgreementService(session)
    original = agreements.generate(order.id, actor_id=generate_uuid_v7())
    _, original_pdf, original_digest = agreements.pdf(original.id)

    templates = RentalAgreementTemplateService(session)
    current = templates.context().current_template
    templates.create_template(
        AgreementTemplateCreate(markdown_source=f"{current.markdown_source}\n\nНовая версия."),
        actor_id=generate_uuid_v7(),
    )
    templates.update_settings(
        RentalAgreementSettingsWrite(
            lessor_name="Другой арендодатель",
            lessor_inn="000000000000",
            lessor_ogrnip="000000000000000",
            agreement_city="Тверь",
        ),
        actor_id=generate_uuid_v7(),
    )
    order.items[0].title_snapshot = "Переименовано после договора"
    order.items[0].tariff_snapshot = {**order.items[0].tariff_snapshot, "tariff_name": "Новый"}
    session.commit()

    _, stored_pdf, stored_digest = agreements.pdf(original.id)
    assert stored_pdf == original_pdf
    assert stored_digest == original_digest == sha256(original_pdf).hexdigest()
    record = session.get(RentalAgreementRecord, original.id)
    assert record is not None
    assert "Другой арендодатель" not in record.rendered_markdown
    assert "Переименовано после договора" not in record.rendered_markdown


def test_agreement_omits_empty_identification_and_includes_present_serial_and_seals(
    session: Session,
) -> None:
    """Agreement item text stays compact and freezes only identification that exists."""
    _configure_settings(session)
    empty_order = _ready_order(session)
    service = RentalAgreementService(session)
    empty = service.generate(empty_order.id, actor_id=generate_uuid_v7())
    empty_record = session.get(RentalAgreementRecord, empty.id)
    assert empty_record is not None
    assert "серийный номер" not in empty_record.rendered_markdown.lower()
    assert "пломбы:" not in empty_record.rendered_markdown.lower()
    assert empty_record.items_snapshot[0]["asset_identification"] == {
        "serial_number": None,
        "seals": [],
        "components": [],
    }

    identified_order = _ready_order(session)
    item = identified_order.items[0]
    asset = session.get(RentalAssetRecord, item.rental_asset_id)
    assert asset is not None
    asset.serial_number = "SN-PUZZI-42"
    session.add_all(
        [
            RentalAssetComponentRecord(
                rental_asset_id=asset.id,
                name="Бак",
                note=None,
                sort_order=0,
                is_active=True,
            ),
            RentalAssetComponentRecord(
                rental_asset_id=asset.id,
                name="Старая насадка",
                note=None,
                sort_order=1,
                is_active=False,
            ),
            RentalAssetSealRecord(
                rental_asset_id=asset.id,
                location="Корпус",
                seal_number="001842",
                status=RentalSealStatus.ACTIVE,
                installed_at=datetime.now(UTC),
            ),
            RentalAssetSealRecord(
                rental_asset_id=asset.id,
                location="Старая точка",
                seal_number="000111",
                status=RentalSealStatus.REMOVED,
                installed_at=datetime.now(UTC) - timedelta(days=1),
                removed_at=datetime.now(UTC),
            ),
        ]
    )
    session.commit()
    identified = service.generate(identified_order.id, actor_id=generate_uuid_v7())
    identified_record = session.get(RentalAgreementRecord, identified.id)

    assert identified_record is not None
    assert "серийный номер SN-PUZZI-42" in identified_record.rendered_markdown
    assert "пломбы: Корпус — 001842" in identified_record.rendered_markdown
    assert "комплект: Бак" in identified_record.rendered_markdown
    assert "Старая насадка" not in identified_record.rendered_markdown
    assert "000111" not in identified_record.rendered_markdown
    assert "Серийный номер: —" not in identified_record.rendered_markdown
    assert "Пломбы: —" not in identified_record.rendered_markdown

    _, original_pdf, original_digest = service.pdf(identified.id)
    asset.serial_number = "SN-CHANGED-LATER"
    asset.components[0].name = "Бак нового образца"
    session.commit()
    _, stored_pdf, stored_digest = service.pdf(identified.id)
    assert stored_pdf == original_pdf
    assert stored_digest == original_digest


def test_required_agreement_gates_issue_then_allows_existing_atomic_command(
    session: Session,
) -> None:
    _configure_settings(session)
    order = _ready_order(session, requires_agreement=True)
    orders = RentalOrderService(session)

    with pytest.raises(RentalAgreementRequiredError):
        orders.issue(order.id)

    agreement = RentalAgreementService(session).generate(order.id, actor_id=generate_uuid_v7())
    issued = orders.issue(order.id)

    assert agreement.rental_order_id == order.id
    assert issued.status is RentalOrderStatus.ISSUED
    assert issued.items[0].due_at == issued.items[0].issued_at + timedelta(minutes=180)


def test_no_agreement_order_issues_and_generated_terms_are_locked(session: Session) -> None:
    optional = _ready_order(session, requires_agreement=False)
    issued = RentalOrderService(session).issue(optional.id)
    assert issued.status is RentalOrderStatus.ISSUED

    _configure_settings(session)
    required = _ready_order(session, item_count=2, requires_agreement=True)
    RentalAgreementService(session).generate(required.id, actor_id=generate_uuid_v7())
    with pytest.raises(RentalAgreementTermsLockedError):
        RentalOrderService(session).remove_item(required.id, required.items[0].id)


def test_passport_data_is_not_persisted_in_agreement_models() -> None:
    columns = {
        column.name
        for table in (
            RentalAgreementRecord.__table__,
            RentalAgreementSettingsRecord.__table__,
            RentalAgreementTemplateVersionRecord.__table__,
        )
        for column in table.columns
    }
    assert (
        not {
            "passport_series",
            "passport_number",
            "passport_issued_by",
            "passport_issue_date",
            "passport_scan",
            "passport_url",
        }
        & columns
    )
