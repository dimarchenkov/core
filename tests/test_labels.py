from __future__ import annotations

import re
import subprocess
from collections.abc import Generator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from reportlab.graphics.barcode import createBarcodeDrawing
from reportlab.lib.units import mm
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.barcodes import BarcodeSource
from core.catalog.models import CatalogProduct, CatalogVariant, CatalogVariantBarcode, Category
from core.config import Settings
from core.database import get_session
from core.identity.models import User
from core.identity.service import IdentityService
from core.labels.printing import (
    CupsPrintingAdapter,
    LabelPrinterUnavailableError,
    LabelPrintFailedError,
    LabelPrintResult,
    VariantLabelPrintService,
)
from core.labels.renderer import (
    LabelProfile,
    VariantLabel58x40Renderer,
    VariantLabelData,
    VariantLabelRenderer,
)
from core.labels.routes import get_variant_label_print_service
from core.labels.service import VariantLabelService
from core.main import create_app
from core.media.enums import ImageLinkEntityType, ImageLinkRole
from core.media.models import Image, ImageLink
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.shared.db import Base


@pytest.fixture
def session() -> Generator[Session]:
    """Provide an in-memory database containing label dependencies."""
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
            Image.__table__,
            ImageLink.__table__,
            Price.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as database_session:
        yield database_session


@pytest.fixture
def variant(session: Session) -> CatalogVariant:
    """Create a photographed and positively priced sellable Variant."""
    category = Category(title="Хранение", slug="storage")
    product = CatalogProduct(
        title="Обувница пластиковая",
        slug="plastic-shoe-rack",
        category=category,
    )
    value = CatalogVariant(
        product=product,
        title="6 ярусов",
        sku="SKU-000001",
        barcode="2000000000015",
        attributes={"color": "бежевая"},
    )
    session.add(value)
    session.flush()
    image = Image(
        source_key="images/source/shoe-rack.jpg",
        original_filename="shoe-rack.jpg",
        mime_type="image/jpeg",
        size_bytes=100,
        width=800,
        height=600,
        checksum="sha256:shoe-rack",
    )
    image.links.append(
        ImageLink(
            entity_type=ImageLinkEntityType.CATALOG_VARIANT,
            entity_id=value.id,
            role=ImageLinkRole.PRIMARY,
        )
    )
    price = Price(
        variant_id=value.id,
        price_type=PriceType.RETAIL,
        amount=Decimal("1299.00"),
        currency="RUB",
        effective_from=value.created_at,
    )
    session.add_all([image, price])
    session.commit()
    return value


@pytest.fixture
def user(session: Session) -> User:
    """Create an account for authenticated label requests."""
    return IdentityService(session).create_admin(
        "label-admin@example.com",
        "Label Admin",
        "long enough password",
    )


@pytest.fixture
def client(session: Session) -> Generator[TestClient]:
    """Provide a client backed by the label test database."""
    app = create_app()

    def override_get_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def authorization_header(client: TestClient, user: User) -> dict[str, str]:
    """Log in the supplied user and return its bearer authorization header."""
    response = client.post(
        "/api/auth/login",
        data={"username": user.email, "password": "long enough password"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_renderer_creates_single_pdf_label_with_cyrillic_data() -> None:
    """The 58 x 40 renderer accepts real Russian catalog text and produces PDF bytes."""
    content = VariantLabel58x40Renderer().render(
        VariantLabelData(
            product_title="Обувница пластиковая шестиъярусная",
            variant_details="6 ярусов - бежевая",
            price=Decimal("1299.00"),
            barcode="2000000000015",
            sku="SKU-000001",
        )
    )

    assert content.startswith(b"%PDF-")
    assert len(content) > 5_000


@pytest.mark.parametrize(
    ("profile", "expected_mm"),
    [
        (LabelProfile.COMPACT_40X30, (40, 30)),
        (LabelProfile.STANDARD_58X40, (58, 40)),
    ],
)
@pytest.mark.parametrize("dpi", [203, 300])
def test_label_profiles_have_exact_single_page_media_box(
    profile: LabelProfile,
    expected_mm: tuple[int, int],
    dpi: int,
) -> None:
    """Both printer modes keep exact physical dimensions at supported DPI settings."""
    content = VariantLabelRenderer().render(
        VariantLabelData(
            product_title="Очень длинное название небольшого демонстрационного товара",
            variant_details="Красный - максимальная комплектация",
            price=Decimal("999999.99"),
            barcode="2000000000015",
            sku="SKU-000001",
        ),
        profile=profile,
        dpi=dpi,
    )
    media_box = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", content)
    assert media_box is not None
    width = float(media_box.group(1)) * 25.4 / 72
    height = float(media_box.group(2)) * 25.4 / 72
    assert width == pytest.approx(expected_mm[0], abs=0.02)
    assert height == pytest.approx(expected_mm[1], abs=0.02)
    assert content.count(b"/Type /Page\n") == 1
    assert b"2010shop" not in content
    assert b"QR" not in content


def test_compact_product_label_is_one_canonical_document() -> None:
    content = VariantLabelRenderer().render(
        VariantLabelData(
            product_title="Нидл Nice Can",
            variant_details="Dr Pepper",
            price=Decimal("250"),
            barcode="2000000000015",
            sku="SKU-000011",
        ),
        profile=LabelProfile.COMPACT_40X30,
    )

    assert content.count(b"/Type /Page\n") == 1


def test_compact_ean13_uses_calibrated_module_and_quiet_zones() -> None:
    renderer = VariantLabelRenderer()
    drawing = createBarcodeDrawing(
        "EAN13",
        value="200000000001",
        barWidth=renderer.ean13_x_dimension_mm * mm,
        barHeight=12 * mm,
        humanReadable=True,
    )

    assert renderer.ean13_x_dimension_mm == pytest.approx(0.300)
    assert renderer.ean13_data_modules * renderer.ean13_x_dimension_mm == pytest.approx(28.5)
    assert drawing.contents[0].barWidth / mm == pytest.approx(0.300)
    assert drawing.contents[0]._lquiet == renderer.ean13_quiet_zone_modules
    assert drawing.width / mm == pytest.approx(33.9)
    assert (40 - drawing.width / mm) / 2 == pytest.approx(3.05)


def test_default_variant_name_is_not_rendered_as_artificial_label_text() -> None:
    data = VariantLabelData(
        product_title="Клей-карандаш",
        variant_details="Default",
        price=Decimal("0"),
        barcode="2000000000015",
        sku="SKU-000001",
    )

    assert VariantLabelRenderer._combined_title(data) == "Клей-карандаш"


def test_meaningful_variant_name_is_preserved_on_compact_label() -> None:
    data = VariantLabelData(
        product_title="Нидл Nice Can",
        variant_details="Dr Pepper",
        price=Decimal("0"),
        barcode="2000000000015",
        sku="SKU-000011",
    )

    assert VariantLabelRenderer._combined_title(data) == "Нидл Nice Can - Dr Pepper"


def test_compact_label_draws_product_variant_price_but_not_sku(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drawn: list[str] = []

    class RecordingCanvas:
        def setFont(self, *_: object) -> None:
            pass

        def drawString(self, _x: float, _y: float, text: str) -> None:
            drawn.append(text)

        def drawCentredString(self, x: float, _y: float, text: str) -> None:
            assert x == pytest.approx(20 * mm)
            drawn.append(text)

    renderer = VariantLabelRenderer()
    renderer._register_fonts()
    monkeypatch.setattr(renderer, "_draw_barcode", lambda *_args, **_kwargs: None)
    renderer._draw_40x30(
        RecordingCanvas(),  # type: ignore[arg-type]
        VariantLabelData(
            product_title="Сыр с пенкой",
            variant_details="Жёлтый, маленький",
            price=Decimal("1290"),
            barcode="2000000000015",
            sku="SKU-SECRET",
        ),
    )

    assert drawn == ["Сыр с пенкой", "Жёлтый, маленький", "1 290 ₽"]
    assert all("SKU" not in value for value in drawn)


def test_label_rejects_invalid_ean_check_digit() -> None:
    """A visually plausible but invalid EAN-13 is never printed."""
    with pytest.raises(ValueError, match="check digit"):
        VariantLabelRenderer().render(
            VariantLabelData(
                product_title="Товар",
                variant_details="Вариант",
                price=Decimal("200"),
                barcode="2000000000014",
                sku="SKU-000001",
            ),
            profile=LabelProfile.COMPACT_40X30,
        )


def test_label_service_uses_authoritative_variant_and_price(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """Label generation resolves catalog and current price data instead of accepting a payload."""
    content = VariantLabelService(session).generate_58x40(variant.id)

    assert content.startswith(b"%PDF-")


def test_label_uses_current_external_barcode(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """Labels use the same sole operational barcode as every other consumer."""
    captured: list[VariantLabelData] = []

    class CapturingRenderer:
        def render(self, data: VariantLabelData, **_: object) -> bytes:
            captured.append(data)
            return b"%PDF-label"

    variant.barcode = "4601234567893"
    variant.barcode_source = BarcodeSource.MANUFACTURER
    session.commit()

    VariantLabelService(session, renderer=CapturingRenderer()).generate(  # type: ignore[arg-type]
        variant.id,
        LabelProfile.COMPACT_40X30,
    )

    assert captured[0].barcode == "4601234567893"


def test_label_service_does_not_require_ready_for_sale(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """Label identity is printable without a price, photo, Inventory, or AQSI state."""
    session.query(Price).delete()
    session.query(ImageLink).delete()
    session.query(Image).delete()
    session.commit()

    content = VariantLabelService(session).generate(
        variant.id,
        LabelProfile.COMPACT_40X30,
    )

    assert content.startswith(b"%PDF-")


def test_direct_print_uses_configured_queue_and_quantity(
    session: Session,
    variant: CatalogVariant,
) -> None:
    calls: list[dict[str, object]] = []

    class CapturingPrinter:
        def print_pdf(self, content: bytes, **kwargs: object) -> LabelPrintResult:
            calls.append({"content": content, **kwargs})
            return LabelPrintResult(str(kwargs["printer_name"]), int(kwargs["quantity"]), "job-7")

    settings = Settings(
        jwt_secret="test-secret", PRINTING_ENABLED=True,
        CUPS_SERVER="print-server.local:631", CUPS_USER="operator",
        CUPS_PRINTER="Xprinter_XP_365B", _env_file=None,
    )
    labels = VariantLabelService(session)
    canonical = labels.generate(variant.id, LabelProfile.COMPACT_40X30)
    result = VariantLabelPrintService(labels, settings, CapturingPrinter()).print(
        variant.id, quantity=11
    )

    assert result.quantity == 11
    assert calls[0]["printer_name"] == "Xprinter_XP_365B"
    assert calls[0]["profile"] is LabelProfile.COMPACT_40X30
    assert calls[0]["content"] == canonical
    assert bytes(calls[0]["content"]).count(b"/Type /Page\n") == 1


def test_direct_print_requires_configured_printer(
    session: Session,
    variant: CatalogVariant,
) -> None:
    settings = Settings(jwt_secret="test-secret", PRINTING_ENABLED=False, _env_file=None)
    with pytest.raises(LabelPrinterUnavailableError):
        VariantLabelPrintService(VariantLabelService(session), settings).print(
            variant.id, quantity=1
        )


def test_label_api_is_authenticated_and_returns_inline_pdf(
    client: TestClient,
    variant: CatalogVariant,
    user: User,
) -> None:
    """The label endpoint is protected and returns a directly printable PDF."""
    path = f"/api/labels/variants/{variant.id}/58x40.pdf"

    assert client.get(path).status_code == 401

    response = client.get(path, headers=authorization_header(client, user))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.content.startswith(b"%PDF-")


def test_label_api_quantity_cannot_change_canonical_document(
    client: TestClient,
    variant: CatalogVariant,
    user: User,
) -> None:
    response = client.get(
        f"/api/labels/variants/{variant.id}/40x30.pdf?quantity=3",
        headers=authorization_header(client, user),
    )

    assert response.status_code == 200
    assert response.content.count(b"/Type /Page\n") == 1


def test_print_capability_reports_runtime_boundary(
    client: TestClient,
    user: User,
) -> None:
    """UI can select direct print or PDF fallback without provoking a known 503."""
    response = client.get(
        "/api/labels/variants/print-capability",
        headers=authorization_header(client, user),
    )

    assert response.status_code == 200
    assert set(response.json()) == {
        "available",
        "enabled",
        "command_available",
        "printer_configured",
        "printer_name",
        "server_configured",
        "user_configured",
        "fallback",
    }
    assert response.json()["fallback"] == "pdf"


def test_direct_print_api_returns_adapter_acknowledgement(
    client: TestClient,
    variant: CatalogVariant,
    user: User,
) -> None:
    class SuccessfulPrintService:
        def print(self, *_: object, **__: object) -> LabelPrintResult:
            return LabelPrintResult("Xprinter_XP_365B", 7, "Xprinter_XP_365B-42")

    app = client.app
    app.dependency_overrides[get_variant_label_print_service] = lambda: SuccessfulPrintService()
    response = client.post(
        f"/api/labels/variants/{variant.id}/40x30/print?quantity=7",
        headers=authorization_header(client, user),
    )
    app.dependency_overrides.pop(get_variant_label_print_service, None)

    assert response.status_code == 200
    assert response.json() == {
        "status": "submitted",
        "printer_name": "Xprinter_XP_365B",
        "quantity": 7,
        "external_job_id": "Xprinter_XP_365B-42",
        "job_id": "Xprinter_XP_365B-42",
    }


def test_cups_adapter_uses_safe_remote_ipp_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr("core.labels.printing.shutil.which", lambda _: "/usr/bin/lp")

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(argv=argv, kwargs=kwargs)
        assert open(argv[-1], "rb").read() == b"%PDF-controlled"
        return subprocess.CompletedProcess(
            argv, 0, "request id is Xprinter_XP_365B-1184 (3 file(s))\n", ""
        )

    monkeypatch.setattr("core.labels.printing.subprocess.run", run)
    result = CupsPrintingAdapter(
        enabled=True, server="print-server.local:631", user="operator",
        ipp_version="1.1",
    ).print_pdf(
        b"%PDF-controlled", printer_name="Xprinter_XP_365B",
        profile=LabelProfile.COMPACT_40X30, quantity=3, job_name="Core label",
    )
    argv = captured["argv"]
    assert argv[:-1] == [
        "/usr/bin/lp", "-h", "print-server.local:631/version=1.1",
        "-U", "operator", "-d", "Xprinter_XP_365B", "-n", "3",
        "-t", "Core label",
    ]
    assert captured["kwargs"] == {
        "check": False, "capture_output": True, "text": True, "timeout": 30,
    }
    assert result.status == "submitted"
    assert result.job_id == "Xprinter_XP_365B-1184"


@pytest.mark.parametrize(
    ("adapter", "quantity", "exception"),
    [
        (CupsPrintingAdapter(enabled=False, server="s", user="u"), 1, LabelPrinterUnavailableError),
        (CupsPrintingAdapter(enabled=True, server=None, user="u"), 1, LabelPrinterUnavailableError),
        (CupsPrintingAdapter(enabled=True, server="s", user=None), 1, LabelPrinterUnavailableError),
        (CupsPrintingAdapter(enabled=True, server="s", user="u"), 0, ValueError),
        (CupsPrintingAdapter(enabled=True, server="s", user="u"), 501, ValueError),
    ],
)
def test_cups_adapter_rejects_unavailable_or_invalid_input(
    adapter: CupsPrintingAdapter, quantity: int, exception: type[Exception],
) -> None:
    with pytest.raises(exception):
        adapter.print_pdf(
            b"%PDF", printer_name="queue", profile=LabelProfile.COMPACT_40X30,
            quantity=quantity, job_name="Core",
        )


def test_cups_adapter_handles_missing_client_timeout_and_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = CupsPrintingAdapter(enabled=True, server="server:631", user="operator")
    def call() -> LabelPrintResult:
        return adapter.print_pdf(
            b"%PDF", printer_name="queue", profile=LabelProfile.COMPACT_40X30,
            quantity=1, job_name="Core",
        )
    monkeypatch.setattr("core.labels.printing.shutil.which", lambda _: None)
    with pytest.raises(LabelPrinterUnavailableError):
        call()
    monkeypatch.setattr("core.labels.printing.shutil.which", lambda _: "/usr/bin/lp")
    monkeypatch.setattr(
        "core.labels.printing.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("lp", 30)),
    )
    with pytest.raises(LabelPrintFailedError, match="did not respond"):
        call()
    monkeypatch.setattr(
        "core.labels.printing.subprocess.run",
        lambda argv, **_: subprocess.CompletedProcess(argv, 1, "", "bad credentials: secret"),
    )
    with pytest.raises(LabelPrintFailedError, match="rejected") as error:
        call()
    assert "secret" not in str(error.value)
