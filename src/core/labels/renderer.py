from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from io import BytesIO
from pathlib import Path

import font_roboto
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import createBarcodeDrawing
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

TECHNICAL_VARIANT_NAMES = frozenset(
    {"default", "default variant", "основной", "основной вариант"}
)


def meaningful_variant_name(value: str) -> str:
    """Hide technical single-Variant titles from customer-facing product labels."""
    normalized = value.strip()
    return "" if normalized.casefold() in TECHNICAL_VARIANT_NAMES else normalized


class LabelProfile(StrEnum):
    """Supported fixed physical product-label layouts."""

    COMPACT_40X30 = "40x30"
    STANDARD_58X40 = "58x40"


@dataclass(frozen=True, slots=True)
class VariantLabelData:
    """Resolved business data rendered on one product label."""

    product_title: str
    variant_details: str
    price: Decimal | None
    barcode: str
    sku: str


@dataclass(frozen=True, slots=True)
class RentalAssetLabelData:
    """Stable physical-asset identity rendered without commercial data."""

    product_title: str
    variant_title: str
    asset_number: str
    sku: str
    status_text: str


class VariantLabelRenderer:
    """Render vector product labels in one of two fixed physical profiles."""

    regular_font = "CoreLabelRegular"
    bold_font = "CoreLabelBold"
    sizes = {
        LabelProfile.COMPACT_40X30: (40 * mm, 30 * mm),
        LabelProfile.STANDARD_58X40: (58 * mm, 40 * mm),
    }
    supported_dpi = frozenset({203, 300})
    ean13_x_dimension_mm = 0.300
    ean13_data_modules = 95
    ean13_quiet_zone_modules = 9

    def render(
        self,
        data: VariantLabelData,
        *,
        profile: LabelProfile = LabelProfile.STANDARD_58X40,
        dpi: int = 203,
    ) -> bytes:
        """Return the canonical one-page vector PDF with an exact physical MediaBox."""
        if dpi not in self.supported_dpi:
            raise ValueError("Label printer DPI must be 203 or 300.")
        self._validate_barcode(data.barcode)
        self._register_fonts()
        width, height = self.sizes[profile]
        output = BytesIO()
        canvas = Canvas(
            output,
            pagesize=(width, height),
            pageCompression=1,
            invariant=1,
        )
        canvas.setTitle(f"Core product label {profile.value}")
        if profile is LabelProfile.COMPACT_40X30:
            self._draw_40x30(canvas, data)
        else:
            self._draw_58x40(canvas, data)
        canvas.showPage()
        canvas.save()
        return output.getvalue()

    def _draw_40x30(self, canvas: Canvas, data: VariantLabelData) -> None:
        """Render the calibrated sale identity without compromising barcode geometry."""
        self._draw_label_titles(canvas, data)
        if data.price is not None:
            canvas.setFont(self.bold_font, 10.5)
            canvas.drawCentredString(20 * mm, 16.8 * mm, self._price_text(data.price))
        self._draw_barcode(
            canvas,
            data.barcode,
            page_width=40 * mm,
            height_mm=12,
            y_mm=2.2,
        )

    def _draw_58x40(self, canvas: Canvas, data: VariantLabelData) -> None:
        """Use the full standard label while preserving barcode quiet zones."""
        self._draw_wrapped(canvas, data.product_title, 2, 38, 54, 8, 2, 3.4)
        details = self._truncate_text(data.variant_details, self.regular_font, 6.2, 54 * mm)
        canvas.setFont(self.regular_font, 6.2)
        canvas.drawString(2 * mm, 29.8 * mm, details)
        if data.price is not None:
            canvas.setFont(self.bold_font, 16)
            canvas.drawRightString(56 * mm, 23.2 * mm, self._price_text(data.price))
        self._draw_barcode(
            canvas,
            data.barcode,
            page_width=58 * mm,
            height_mm=12,
            y_mm=4,
        )

    def _draw_barcode(
        self,
        canvas: Canvas,
        barcode: str,
        *,
        page_width: float,
        height_mm: float,
        y_mm: float,
    ) -> None:
        """Generate vector EAN-13 at the physically calibrated module width."""
        drawing = createBarcodeDrawing(
            "EAN13",
            value=barcode[:12],
            barWidth=self.ean13_x_dimension_mm * mm,
            barHeight=height_mm * mm,
            humanReadable=True,
        )
        left = (page_width - drawing.width) / 2
        if left < 0:
            raise ValueError("Calibrated EAN-13 does not fit the selected label profile.")
        canvas.saveState()
        canvas.translate(left, y_mm * mm)
        renderPDF.draw(drawing, canvas, 0, 0)
        canvas.restoreState()

    def _draw_label_titles(self, canvas: Canvas, data: VariantLabelData) -> None:
        """Use at most two readable lines: Product, then meaningful Variant details."""
        width = 37 * mm
        details = self._meaningful_variant_details(data.variant_details)
        if details:
            lines = [
                self._truncate_text(data.product_title.strip(), self.bold_font, 7.2, width),
                self._truncate_text(details, self.regular_font, 6.2, width),
            ]
        else:
            lines = self._wrap_text(
                data.product_title.strip(), self.bold_font, 7.2, width, max_lines=2
            )
        for index, line in enumerate(lines):
            font = self.bold_font if index == 0 else self.regular_font
            font_size = 7.2 if index == 0 else 6.2
            canvas.setFont(font, font_size)
            canvas.drawString(1.5 * mm, (27 - index * 3.2) * mm, line)

    def _draw_wrapped(
        self,
        canvas: Canvas,
        text: str,
        x_mm: float,
        y_mm: float,
        width_mm: float,
        font_size: float,
        lines: int,
        leading_mm: float,
    ) -> None:
        wrapped = self._wrap_text(
            text.strip(), self.bold_font, font_size, width_mm * mm, max_lines=lines
        )
        canvas.setFont(self.bold_font, font_size)
        for index, line in enumerate(wrapped):
            canvas.drawString(x_mm * mm, (y_mm - index * leading_mm) * mm, line)

    @staticmethod
    def _combined_title(data: VariantLabelData) -> str:
        details = VariantLabelRenderer._meaningful_variant_details(data.variant_details)
        return (
            f"{data.product_title.strip()} - {details}" if details else data.product_title.strip()
        )

    @staticmethod
    def _meaningful_variant_details(value: str) -> str:
        return meaningful_variant_name(value)

    @staticmethod
    def _validate_barcode(barcode: str) -> None:
        if len(barcode) != 13 or not barcode.isdigit():
            raise ValueError("Product label requires a 13-digit EAN-13 barcode.")
        weighted = sum(
            int(digit) * (1 if position % 2 == 1 else 3)
            for position, digit in enumerate(barcode[:12], start=1)
        )
        if (10 - weighted % 10) % 10 != int(barcode[-1]):
            raise ValueError("Product label barcode has an invalid EAN-13 check digit.")

    def _wrap_text(
        self,
        text: str,
        font_name: str,
        font_size: float,
        max_width: float,
        *,
        max_lines: int,
    ) -> list[str]:
        words = text.split()
        if not words:
            return [""]
        lines: list[str] = []
        current = words.pop(0)
        while words and len(lines) < max_lines:
            candidate = f"{current} {words[0]}"
            if pdfmetrics.stringWidth(candidate, font_name, font_size) <= max_width:
                current = candidate
                words.pop(0)
            else:
                lines.append(current)
                current = words.pop(0)
        if len(lines) < max_lines:
            lines.append(current)
        if words:
            lines[-1] = self._truncate_text(
                f"{lines[-1]} {' '.join(words)}", font_name, font_size, max_width
            )
        return lines[:max_lines]

    @staticmethod
    def _truncate_text(text: str, font_name: str, font_size: float, max_width: float) -> str:
        if pdfmetrics.stringWidth(text, font_name, font_size) <= max_width:
            return text
        suffix = "..."
        shortened = text
        while (
            shortened
            and pdfmetrics.stringWidth(f"{shortened}{suffix}", font_name, font_size) > max_width
        ):
            shortened = shortened[:-1]
        return f"{shortened.rstrip()}{suffix}"

    @staticmethod
    def _format_price(amount: Decimal) -> str:
        if amount == amount.to_integral_value():
            return f"{int(amount):,}".replace(",", " ")
        return f"{amount:,.2f}".replace(",", " ")

    @classmethod
    def _price_text(cls, amount: Decimal) -> str:
        return f"{cls._format_price(amount)} ₽"

    @classmethod
    def _register_fonts(cls) -> None:
        registered = set(pdfmetrics.getRegisteredFontNames())
        fonts_dir = Path(font_roboto.__file__).parent / "files"
        if cls.regular_font not in registered:
            pdfmetrics.registerFont(TTFont(cls.regular_font, fonts_dir / "Roboto-Regular.ttf"))
        if cls.bold_font not in registered:
            pdfmetrics.registerFont(TTFont(cls.bold_font, fonts_dir / "Roboto-Bold.ttf"))


class VariantLabel58x40Renderer(VariantLabelRenderer):
    """Backward-compatible renderer facade for the established 58 x 40 API."""

    width = 58 * mm
    height = 40 * mm

    def render(self, data: VariantLabelData, *, dpi: int = 203) -> bytes:
        """Render the standard profile for existing callers."""
        return super().render(data, profile=LabelProfile.STANDARD_58X40, dpi=dpi)


class RentalAssetLabelRenderer(VariantLabelRenderer):
    """Render an internal Code 128 label for one physical RentalAsset."""

    def render(
        self,
        data: RentalAssetLabelData,
        *,
        profile: LabelProfile = LabelProfile.COMPACT_40X30,
        dpi: int = 203,
    ) -> bytes:
        """Return one exact-size PDF whose scanned value is the RENT number."""
        if dpi not in self.supported_dpi:
            raise ValueError("Label printer DPI must be 203 or 300.")
        self._validate_asset_number(data.asset_number)
        self._register_fonts()
        width, height = self.sizes[profile]
        output = BytesIO()
        canvas = Canvas(
            output,
            pagesize=(width, height),
            pageCompression=1,
            invariant=1,
        )
        canvas.setTitle(f"{data.asset_number} inventory label")
        if profile is LabelProfile.COMPACT_40X30:
            self._draw_asset_40x30(canvas, data)
        else:
            self._draw_asset_58x40(canvas, data)
        canvas.showPage()
        canvas.save()
        return output.getvalue()

    def _draw_asset_40x30(self, canvas: Canvas, data: RentalAssetLabelData) -> None:
        canvas.setFont(self.bold_font, 6.5)
        canvas.drawString(1.5 * mm, 27.2 * mm, "АРЕНДА")
        self._draw_wrapped(canvas, data.product_title, 1.5, 23.9, 37, 6.2, 1, 2.6)
        variant = self._truncate_text(data.variant_title, self.regular_font, 5.5, 37 * mm)
        canvas.setFont(self.regular_font, 5.5)
        canvas.drawString(1.5 * mm, 20.7 * mm, variant)
        self._draw_code128(canvas, data.asset_number, 40 * mm, 37, 9.7, 8.5)
        canvas.setFont(self.bold_font, 7.2)
        canvas.drawCentredString(20 * mm, 5.1 * mm, data.asset_number)
        if data.status_text:
            status = self._truncate_text(data.status_text, self.regular_font, 4.2, 37 * mm)
            canvas.setFont(self.regular_font, 4.2)
            canvas.drawCentredString(20 * mm, 2.8 * mm, status)

    def _draw_asset_58x40(self, canvas: Canvas, data: RentalAssetLabelData) -> None:
        canvas.setFont(self.bold_font, 8)
        canvas.drawString(2 * mm, 36.3 * mm, "АРЕНДА")
        self._draw_wrapped(canvas, data.product_title, 2, 32.2, 54, 8.2, 2, 3.4)
        variant = self._truncate_text(data.variant_title, self.regular_font, 6.3, 54 * mm)
        canvas.setFont(self.regular_font, 6.3)
        canvas.drawString(2 * mm, 24.7 * mm, variant)
        self._draw_code128(canvas, data.asset_number, 58 * mm, 54, 13.8, 9.4)
        canvas.setFont(self.bold_font, 9)
        canvas.drawCentredString(29 * mm, 6 * mm, data.asset_number)
        footer = " · ".join(value for value in (data.sku, data.status_text) if value)
        footer = self._truncate_text(footer, self.regular_font, 4.7, 54 * mm)
        canvas.setFont(self.regular_font, 4.7)
        canvas.drawCentredString(29 * mm, 3 * mm, footer)

    @staticmethod
    def _draw_code128(
        canvas: Canvas,
        value: str,
        page_width: float,
        width_mm: float,
        height_mm: float,
        y_mm: float,
    ) -> None:
        drawing = createBarcodeDrawing(
            "Code128",
            value=value,
            barHeight=height_mm * mm,
            humanReadable=False,
            quiet=True,
        )
        scale = width_mm * mm / drawing.width
        left = (page_width - drawing.width * scale) / 2
        canvas.saveState()
        canvas.translate(left, y_mm * mm)
        canvas.scale(scale, 1)
        renderPDF.draw(drawing, canvas, 0, 0)
        canvas.restoreState()

    @staticmethod
    def _validate_asset_number(asset_number: str) -> None:
        prefix, separator, digits = asset_number.partition("-")
        if prefix != "RENT" or separator != "-" or len(digits) < 6 or not digits.isdigit():
            raise ValueError("RentalAsset label requires a canonical RENT number.")
