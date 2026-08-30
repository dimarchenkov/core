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

    def render(
        self,
        data: VariantLabelData,
        *,
        profile: LabelProfile = LabelProfile.STANDARD_58X40,
        dpi: int = 203,
        quantity: int = 1,
    ) -> bytes:
        """Return one single-page vector PDF with an exact physical MediaBox."""
        if dpi not in self.supported_dpi:
            raise ValueError("Label printer DPI must be 203 or 300.")
        if not 1 <= quantity <= 500:
            raise ValueError("Label quantity must be between 1 and 500.")
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
        canvas.setTitle(f"{data.sku} {profile.value} label")
        for _ in range(quantity):
            if profile is LabelProfile.COMPACT_40X30:
                self._draw_40x30(canvas, data)
            else:
                self._draw_58x40(canvas, data)
            canvas.showPage()
        canvas.save()
        return output.getvalue()

    def _draw_40x30(self, canvas: Canvas, data: VariantLabelData) -> None:
        """Fit sale identity, price, and a scannable barcode on 40 x 30 mm media."""
        self._draw_wrapped(canvas, self._combined_title(data), 1.5, 26.5, 37, 7.2, 2, 3.2)
        if data.price is not None:
            canvas.setFont(self.bold_font, 8.5)
            canvas.drawCentredString(20 * mm, 18.2 * mm, self._price_text(data.price))
        self._draw_barcode(
            canvas,
            data.barcode,
            page_width=40 * mm,
            width_mm=37,
            height_mm=10.5,
            y_mm=5.2,
        )
        sku = self._truncate_text(data.sku, self.regular_font, 4.8, 37 * mm)
        canvas.setFont(self.regular_font, 4.8)
        canvas.drawCentredString(20 * mm, 1.3 * mm, sku)

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
            width_mm=54,
            height_mm=12,
            y_mm=4,
        )
        sku = self._truncate_text(data.sku, self.regular_font, 5.2, 54 * mm)
        canvas.setFont(self.regular_font, 5.2)
        canvas.drawCentredString(29 * mm, 1.1 * mm, sku)

    def _draw_barcode(
        self,
        canvas: Canvas,
        barcode: str,
        *,
        page_width: float,
        width_mm: float,
        height_mm: float,
        y_mm: float,
    ) -> None:
        """Generate the stored EAN-13 directly at the layout's final vector size."""
        drawing = createBarcodeDrawing(
            "EAN13",
            value=barcode[:12],
            barHeight=height_mm * mm,
            humanReadable=True,
        )
        scale = min(1.0, width_mm * mm / drawing.width)
        left = (page_width - drawing.width * scale) / 2
        canvas.saveState()
        canvas.translate(left, y_mm * mm)
        canvas.scale(scale, 1)
        renderPDF.draw(drawing, canvas, 0, 0)
        canvas.restoreState()

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
        details = data.variant_details.strip()
        if details.casefold() in {"default", "default variant", "основной"}:
            details = ""
        return (
            f"{data.product_title.strip()} - {details}" if details else data.product_title.strip()
        )

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
        return f"{cls._format_price(amount)} руб."

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
