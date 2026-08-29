from __future__ import annotations

import re
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.models import CatalogProduct, CatalogVariant, CatalogVariantBarcode, Category
from core.database import get_session
from core.identity.models import User
from core.identity.service import IdentityService
from core.labels.renderer import (
    LabelProfile,
    RentalAssetLabelData,
    RentalAssetLabelRenderer,
)
from core.labels.service import RentalAssetLabelService
from core.main import create_app
from core.pricing.models import Price
from core.rental.enums import AssetCondition, AssetPurpose, RentalAvailability
from core.rental.models import RentalAssetRecord, RentalOrderItemRecord, RentalOrderRecord
from core.rental.repository import RentalAssetRepository
from core.shared.db import Base


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
            Category.__table__,
            CatalogProduct.__table__,
            CatalogVariant.__table__,
            CatalogVariantBarcode.__table__,
            Price.__table__,
            RentalAssetRecord.__table__,
            RentalOrderRecord.__table__,
            RentalOrderItemRecord.__table__,
        ],
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as database_session:
        yield database_session


@pytest.fixture
def asset(session: Session) -> RentalAssetRecord:
    category = Category(title="Инструменты", slug="tools-rent-label")
    product = CatalogProduct(
        title="Очень длинное название аккумуляторного шуруповёрта",
        slug="cordless-driver-rent-label",
        category=category,
    )
    variant = CatalogVariant(
        product=product,
        title="Bosch 221 с дополнительным аккумулятором",
        sku="SKU-000001",
        barcode="2000000000015",
        attributes={},
    )
    session.add(variant)
    session.flush()
    record = RentalAssetRecord(
        asset_number="RENT-000001",
        variant_id=variant.id,
        purpose=AssetPurpose.RENTAL,
        condition=AssetCondition.GOOD,
        availability=RentalAvailability.AVAILABLE,
    )
    session.add(record)
    session.commit()
    return record


@pytest.fixture
def user(session: Session) -> User:
    employee = IdentityService(session).create_admin(
        "rental-label@example.com",
        "Rental employee",
        "long enough password",
    )
    employee.is_admin = False
    session.commit()
    return employee


@pytest.fixture
def client(session: Session) -> Generator[TestClient]:
    app = create_app()

    def override_get_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def auth(client: TestClient, user: User) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        data={"username": user.email, "password": "long enough password"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.parametrize(
    ("profile", "expected_mm"),
    [
        (LabelProfile.COMPACT_40X30, (40, 30)),
        (LabelProfile.STANDARD_58X40, (58, 40)),
    ],
)
@pytest.mark.parametrize("dpi", [203, 300])
def test_rental_asset_profiles_are_exact_single_page_code128_labels(
    profile: LabelProfile,
    expected_mm: tuple[int, int],
    dpi: int,
) -> None:
    data = RentalAssetLabelData(
        product_title="Очень длинное название аккумуляторного шуруповёрта",
        variant_title="Bosch 221 с дополнительным аккумулятором",
        asset_number="RENT-000001",
        sku="SKU-000001",
        status_text="Доступен",
    )
    renderer = RentalAssetLabelRenderer()

    content = renderer.render(data, profile=profile, dpi=dpi)
    repeated = renderer.render(data, profile=profile, dpi=dpi)

    media_box = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", content)
    assert media_box is not None
    assert float(media_box.group(1)) * 25.4 / 72 == pytest.approx(expected_mm[0], abs=0.02)
    assert float(media_box.group(2)) * 25.4 / 72 == pytest.approx(expected_mm[1], abs=0.02)
    assert content.count(b"/Type /Page\n") == 1
    assert content == repeated
    assert b"2010shop" not in content


def test_rental_asset_label_needs_no_prices_deposit_or_aqsi(
    session: Session,
    asset: RentalAssetRecord,
) -> None:
    """The inventory label resolves identity even when Pricing has no rows."""
    content = RentalAssetLabelService(session).generate(
        asset.id,
        LabelProfile.COMPACT_40X30,
    )

    assert content.startswith(b"%PDF-")
    assert session.query(Price).count() == 0


def test_rent_number_is_exact_code128_value_and_rejects_product_ean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.labels import renderer as renderer_module

    calls: list[tuple[str, str]] = []
    original = renderer_module.createBarcodeDrawing

    def record_barcode(kind: str, **kwargs: object) -> object:
        calls.append((kind, str(kwargs["value"])))
        return original(kind, **kwargs)

    monkeypatch.setattr(renderer_module, "createBarcodeDrawing", record_barcode)
    RentalAssetLabelRenderer().render(
        RentalAssetLabelData(
            product_title="Товар",
            variant_title="Вариант",
            asset_number="RENT-000001",
            sku="SKU-1",
            status_text="Доступен",
        )
    )

    assert calls == [("Code128", "RENT-000001")]
    RentalAssetLabelRenderer._validate_asset_number("RENT-000001")

    with pytest.raises(ValueError, match="canonical RENT"):
        RentalAssetLabelRenderer._validate_asset_number("2000000000015")


def test_employee_can_print_but_anonymous_user_cannot_read_asset(
    client: TestClient,
    user: User,
    asset: RentalAssetRecord,
) -> None:
    path = f"/api/labels/rental-assets/{asset.id}/40x30.pdf"

    assert client.get(path).status_code == 401
    response = client.get(path, headers=auth(client, user))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"


def test_exact_case_insensitive_scan_finds_asset_without_mutating_it(
    client: TestClient,
    user: User,
    asset: RentalAssetRecord,
    session: Session,
) -> None:
    before = (asset.purpose, asset.condition, asset.availability)
    response = client.get("/api/rental/assets/by-number/rent-000001", headers=auth(client, user))
    session.refresh(asset)

    assert response.status_code == 200
    assert response.json()["id"] == str(asset.id)
    assert response.json()["asset_number"] == "RENT-000001"
    assert (asset.purpose, asset.condition, asset.availability) == before


def test_historical_asset_remains_searchable_and_printable(
    session: Session,
    asset: RentalAssetRecord,
) -> None:
    asset.purpose = AssetPurpose.SALE
    session.commit()

    row = RentalAssetRepository(session).get_by_asset_number("rent-000001")
    content = RentalAssetLabelService(session).generate(
        asset.id,
        LabelProfile.STANDARD_58X40,
    )

    assert row is not None
    assert row[0].id == asset.id
    assert content.startswith(b"%PDF-")
