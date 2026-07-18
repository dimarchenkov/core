from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import font_roboto
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import createBarcodeDrawing
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas


@dataclass(frozen=True, slots=True)
class VariantLabelData:
    """Resolved business data rendered on one product label."""

    product_title: str
    variant_details: str
    price: Decimal
    barcode: str
    sku: str
    store_name: str = "2010shop"


class VariantLabel58x40Renderer:
    """Render a compact printer-independent 58 x 40 mm product label."""

    width = 58 * mm
    height = 40 * mm
    regular_font = "CoreLabelRegular"
    bold_font = "CoreLabelBold"

    def render(self, data: VariantLabelData) -> bytes:
        """Return one complete single-page PDF label."""
        self._register_fonts()
        output = BytesIO()
        canvas = Canvas(
            output,
            pagesize=(self.width, self.height),
            pageCompression=1,
            invariant=1,
        )
        canvas.setTitle(f"{data.sku} label")
        self._draw_barcode(canvas, data.barcode)
        self._draw_title(canvas, data.product_title)
        self._draw_variant_details(canvas, data.variant_details)
        self._draw_price(canvas, data.price)
        self._draw_footer(canvas, data.sku, data.store_name)
        canvas.showPage()
        canvas.save()
        return output.getvalue()

    def _draw_title(self, canvas: Canvas, title: str) -> None:
        """Draw at most two readable product-title lines."""
        lines = self._wrap_text(title.strip(), self.bold_font, 8, 54 * mm, max_lines=2)
        canvas.setFont(self.bold_font, 8)
        for index, line in enumerate(lines):
            canvas.drawString(2 * mm, (37 - index * 3.5) * mm, line)

    def _draw_variant_details(self, canvas: Canvas, details: str) -> None:
        """Draw one compact line describing color, size, or configuration."""
        text = self._truncate_text(details.strip(), self.regular_font, 6.5, 54 * mm)
        canvas.setFont(self.regular_font, 6.5)
        canvas.drawString(2 * mm, 28.5 * mm, text)

    def _draw_price(self, canvas: Canvas, amount: Decimal) -> None:
        """Draw the retail price as the label's strongest visual element."""
        text = f"{self._format_price(amount)} руб."
        canvas.setFont(self.bold_font, 15)
        canvas.drawRightString(56 * mm, 21.5 * mm, text)

    def _draw_barcode(self, canvas: Canvas, barcode: str) -> None:
        """Draw generated internal EAN-13 or a compatible legacy numeric barcode."""
        if len(barcode) == 13:
            drawing = createBarcodeDrawing(
                "EAN13",
                value=barcode[:12],
                barHeight=7 * mm,
                humanReadable=True,
            )
        else:
            drawing = createBarcodeDrawing(
                "Code128",
                value=barcode,
                barHeight=7 * mm,
                barWidth=0.3 * mm,
                humanReadable=True,
            )

        max_width = 50 * mm
        scale = min(1.0, max_width / drawing.width)
        left = (self.width - drawing.width * scale) / 2
        canvas.saveState()
        canvas.translate(left, 4.2 * mm)
        canvas.scale(scale, 1)
        renderPDF.draw(drawing, canvas, 0, 0)
        canvas.restoreState()

    def _draw_footer(self, canvas: Canvas, sku: str, store_name: str) -> None:
        """Draw stable human-readable identifiers below the barcode."""
        canvas.setFont(self.regular_font, 5.5)
        canvas.drawString(2 * mm, 1.5 * mm, sku)
        canvas.drawRightString(56 * mm, 1.5 * mm, store_name)

    def _wrap_text(
        self,
        text: str,
        font_name: str,
        font_size: float,
        max_width: float,
        *,
        max_lines: int,
    ) -> list[str]:
        """Wrap text to a fixed number of physical label lines."""
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
                f"{lines[-1]} {' '.join(words)}",
                font_name,
                font_size,
                max_width,
            )
        return lines

    @staticmethod
    def _truncate_text(text: str, font_name: str, font_size: float, max_width: float) -> str:
        """Truncate one line with an ellipsis until it fits the label width."""
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
        """Format rubles with grouped thousands and optional kopecks."""
        if amount == amount.to_integral_value():
            return f"{int(amount):,}".replace(",", " ")
        return f"{amount:,.2f}".replace(",", " ")

    @classmethod
    def _register_fonts(cls) -> None:
        """Register ReportLab's bundled Unicode fonts once per process."""
        registered = set(pdfmetrics.getRegisteredFontNames())
        fonts_dir = Path(font_roboto.__file__).parent / "files"
        if cls.regular_font not in registered:
            pdfmetrics.registerFont(TTFont(cls.regular_font, fonts_dir / "Roboto-Regular.ttf"))
        if cls.bold_font not in registered:
            pdfmetrics.registerFont(TTFont(cls.bold_font, fonts_dir / "Roboto-Bold.ttf"))
