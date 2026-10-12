from __future__ import annotations

import html
import re
from io import BytesIO
from pathlib import Path
from typing import Final

import font_roboto
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Table,
    TableStyle,
)

from core.rental.agreement_template import (
    SUPPORTED_AGREEMENT_PLACEHOLDERS,
    SUPPORTED_HANDWRITING_COMPONENTS,
)

PLACEHOLDER_PATTERN = re.compile(r"{{\s*([a-z_][a-z0-9_]*)\s*}}")
HANDWRITING_PATTERN = re.compile(r"^\[\[handwriting:([a-z_]+)\]\]$", re.MULTILINE)
HANDWRITING_FRAGMENT_PATTERN = re.compile(r"\[\[handwriting:([^\]]*)\]\]")
PDF_PAGE_PATTERN = re.compile(rb"/Type\s*/Page(?!s)")


class AgreementTemplateValidationError(ValueError):
    """Raised when Markdown requests unsupported or malformed safe template syntax."""


class AgreementDocumentTooLargeError(ValueError):
    """Raised instead of shrinking a non-standard agreement below readable type."""


class _InvariantCanvas(Canvas):
    def __init__(self, *args: object, **kwargs: object) -> None:
        kwargs["invariant"] = 1
        super().__init__(*args, **kwargs)


class RentalAgreementRenderer:
    """Safely render allowlisted Markdown into preview HTML and a canonical two-page PDF."""

    regular_font = "CoreAgreementRegular"
    bold_font = "CoreAgreementBold"

    page_margin_mm: Final[float] = 12
    body_font_pt: Final[float] = 9.8
    body_leading_pt: Final[float] = 11.3
    heading_font_pt: Final[float] = 10.7
    heading_leading_pt: Final[float] = 12.3
    title_font_pt: Final[float] = 11
    title_leading_pt: Final[float] = 12.6
    identity_field_height_mm: Final[float] = 8
    remarks_field_height_mm: Final[float] = 10
    time_field_height_mm: Final[float] = 9
    signature_field_height_mm: Final[float] = 10
    deposit_field_height_mm: Final[float] = 10

    def validate(self, markdown_source: str) -> set[str]:
        """Return referenced placeholders after rejecting unsupported safe syntax."""
        found = set(PLACEHOLDER_PATTERN.findall(markdown_source))
        unknown = found - SUPPORTED_AGREEMENT_PLACEHOLDERS
        stripped = PLACEHOLDER_PATTERN.sub("", markdown_source)
        if unknown:
            raise AgreementTemplateValidationError(
                f"Unsupported placeholders: {', '.join(sorted(unknown))}"
            )
        if "{{" in stripped or "}}" in stripped:
            raise AgreementTemplateValidationError("Malformed agreement placeholder.")

        components = set(HANDWRITING_FRAGMENT_PATTERN.findall(markdown_source))
        unknown_components = components - SUPPORTED_HANDWRITING_COMPONENTS
        component_stripped = HANDWRITING_FRAGMENT_PATTERN.sub("", markdown_source)
        if unknown_components:
            raise AgreementTemplateValidationError(
                "Unsupported handwriting components: " + ", ".join(sorted(unknown_components))
            )
        if "[[handwriting:" in component_stripped:
            raise AgreementTemplateValidationError("Malformed handwriting component.")
        for raw_line in markdown_source.splitlines():
            if "[[handwriting:" in raw_line and not HANDWRITING_PATTERN.fullmatch(raw_line.strip()):
                raise AgreementTemplateValidationError(
                    "Handwriting components must occupy a whole Markdown line."
                )
        return found

    def render_markdown(self, markdown_source: str, values: dict[str, str]) -> str:
        """Substitute only explicit allowlisted values and never execute template code."""
        self.validate(markdown_source)

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in values:
                raise AgreementTemplateValidationError(f"Missing placeholder value: {name}")
            return values[name]

        return PLACEHOLDER_PATTERN.sub(replace, markdown_source)

    def render_html(self, rendered_markdown: str) -> str:
        """Render a deliberately small safe Markdown subset with raw HTML escaped."""
        blocks: list[str] = []
        paragraph: list[str] = []

        def flush() -> None:
            if paragraph:
                blocks.append(f"<p>{self._inline_html(' '.join(paragraph))}</p>")
                paragraph.clear()

        for raw_line in rendered_markdown.splitlines():
            line = raw_line.rstrip()
            component_match = HANDWRITING_PATTERN.fullmatch(line.strip())
            if component_match:
                flush()
                blocks.append(self._handwriting_html(component_match.group(1)))
            elif not line:
                flush()
            elif line.startswith("# "):
                flush()
                blocks.append(f"<h1>{self._inline_html(line[2:])}</h1>")
            elif line.startswith("## "):
                flush()
                blocks.append(f"<h2>{self._inline_html(line[3:])}</h2>")
            elif line.startswith("[ ]") or re.match(r"^\d+\.\s", line):
                flush()
                blocks.append(f"<p>{self._inline_html(line)}</p>")
            else:
                paragraph.append(line.removesuffix("  "))
        flush()
        return '<article class="agreement-document">' + "".join(blocks) + "</article>"

    def render_pdf(self, rendered_markdown: str, *, layout_version: int | None = None) -> bytes:
        """Return canonical A4 bytes using the layout attached to the template version."""
        if layout_version is not None:
            if layout_version >= 2:
                return self._render_layout_v2_pdf(rendered_markdown)
            return self._render_legacy_pdf(rendered_markdown)
        if HANDWRITING_PATTERN.search(rendered_markdown):
            return self._render_layout_v2_pdf(rendered_markdown)
        return self._render_legacy_pdf(rendered_markdown)

    def _render_layout_v2_pdf(self, rendered_markdown: str) -> bytes:
        """Render the readable v2 flow layout without an artificial section break."""
        self._register_fonts()
        output = BytesIO()
        document = SimpleDocTemplate(
            output,
            pagesize=A4,
            leftMargin=self.page_margin_mm * mm,
            rightMargin=self.page_margin_mm * mm,
            topMargin=12 * mm,
            bottomMargin=12 * mm,
            title="Договор проката оборудования",
            author="Core",
        )
        body = ParagraphStyle(
            "AgreementBodyV2",
            fontName=self.regular_font,
            fontSize=self.body_font_pt,
            leading=self.body_leading_pt,
            spaceAfter=0.1,
        )
        heading = ParagraphStyle(
            "AgreementHeadingV2",
            parent=body,
            fontName=self.bold_font,
            fontSize=self.heading_font_pt,
            leading=self.heading_leading_pt,
            spaceBefore=1.5,
            spaceAfter=0.4,
            keepWithNext=True,
        )
        title = ParagraphStyle(
            "AgreementTitleV2",
            parent=heading,
            fontSize=self.title_font_pt,
            leading=self.title_leading_pt,
            alignment=TA_CENTER,
            spaceAfter=3,
        )
        story: list[object] = []
        for raw_line in rendered_markdown.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            component_match = HANDWRITING_PATTERN.fullmatch(line)
            if component_match:
                story.append(self._handwriting_pdf(component_match.group(1), document.width, body))
            elif line.startswith("# "):
                story.append(Paragraph(self._inline_pdf(line[2:]), title))
            elif line.startswith("## "):
                story.append(Paragraph(self._inline_pdf(line[3:]), heading))
            else:
                story.append(Paragraph(self._inline_pdf(line), body))
        document.build(
            story,
            onFirstPage=self._footer,
            onLaterPages=self._footer,
            canvasmaker=_InvariantCanvas,
        )
        return self._require_two_pages(output.getvalue())

    def _render_legacy_pdf(self, rendered_markdown: str) -> bytes:
        """Keep Sprint 4.17 v1 rendering deterministic for historical compatibility."""
        self._register_fonts()
        output = BytesIO()
        document = SimpleDocTemplate(
            output,
            pagesize=A4,
            leftMargin=10 * mm,
            rightMargin=10 * mm,
            topMargin=10 * mm,
            bottomMargin=11 * mm,
            title="Договор проката оборудования",
            author="Core",
        )
        body = ParagraphStyle(
            "AgreementBody",
            fontName=self.regular_font,
            fontSize=8.2,
            leading=9.2,
            spaceAfter=1.4,
        )
        heading = ParagraphStyle(
            "AgreementHeading",
            parent=body,
            fontName=self.bold_font,
            fontSize=9.2,
            leading=10.2,
            spaceBefore=2.3,
            spaceAfter=1.4,
            keepWithNext=True,
        )
        title = ParagraphStyle(
            "AgreementTitle",
            parent=heading,
            fontSize=10.2,
            leading=11.2,
            alignment=TA_CENTER,
            spaceAfter=3,
        )
        story: list[object] = []
        page_break_added = False
        for raw_line in rendered_markdown.splitlines():
            line = raw_line.strip()
            if line.startswith("## 3.") and not page_break_added:
                story.append(PageBreak())
                page_break_added = True
            if not line:
                continue
            if line.startswith("# "):
                story.append(Paragraph(self._inline_pdf(line[2:]), title))
            elif line.startswith("## "):
                story.append(Paragraph(self._inline_pdf(line[3:]), heading))
            else:
                story.append(Paragraph(self._inline_pdf(line), body))
        if not page_break_added:
            story.append(PageBreak())
            story.append(Paragraph(" ", body))
        document.build(
            story,
            onFirstPage=self._legacy_footer,
            onLaterPages=self._legacy_footer,
            canvasmaker=_InvariantCanvas,
        )
        return self._require_two_pages(output.getvalue())

    def _handwriting_pdf(
        self,
        component: str,
        width: float,
        body: ParagraphStyle,
    ) -> KeepTogether:
        """Create fixed-height, pen-friendly fields with real bottom borders."""

        def label(value: str) -> Paragraph:
            return Paragraph(self._inline_pdf(value), body)

        line_style = TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), self.regular_font),
                ("FONTSIZE", (0, 0), (-1, -1), self.body_font_pt),
                ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 1.5 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.2 * mm),
            ]
        )

        def single_row(title_text: str, height_mm: float, label_width_mm: float) -> Table:
            table = Table(
                [[label(title_text), ""]],
                colWidths=[label_width_mm * mm, width - label_width_mm * mm],
                rowHeights=[height_mm * mm],
                splitByRow=0,
            )
            table.setStyle(line_style)
            table.setStyle(TableStyle([("LINEBELOW", (1, 0), (1, 0), 0.65, "#222222")]))
            return table

        if component == "tenant_name":
            parts = [single_row("Арендатор (Ф. И. О.):", self.identity_field_height_mm, 39)]
        elif component == "passport":
            table = Table(
                [[label("Паспорт: серия"), "", label("№"), ""]],
                colWidths=[28 * mm, 31 * mm, 7 * mm, width - 66 * mm],
                rowHeights=[self.identity_field_height_mm * mm],
                splitByRow=0,
            )
            table.setStyle(line_style)
            table.setStyle(
                TableStyle(
                    [
                        ("LINEBELOW", (1, 0), (1, 0), 0.65, "#222222"),
                        ("LINEBELOW", (3, 0), (3, 0), 0.65, "#222222"),
                    ]
                )
            )
            parts = [table]
        elif component == "passport_authority":
            parts = [single_row("Кем выдан:", self.identity_field_height_mm, 22)]
        elif component == "passport_date":
            parts = [single_row("Дата выдачи:", self.identity_field_height_mm, 25)]
        elif component == "passport_date_phone":
            table = Table(
                [[label("Дата выдачи:"), "", label("Телефон:"), ""]],
                colWidths=[25 * mm, 52 * mm, 18 * mm, width - 95 * mm],
                rowHeights=[self.identity_field_height_mm * mm],
                splitByRow=0,
            )
            table.setStyle(line_style)
            table.setStyle(
                TableStyle(
                    [
                        ("LINEBELOW", (1, 0), (1, 0), 0.65, "#222222"),
                        ("LINEBELOW", (3, 0), (3, 0), 0.65, "#222222"),
                    ]
                )
            )
            parts = [table]
        elif component == "tenant_phone":
            parts = [single_row("Телефон:", self.identity_field_height_mm, 18)]
        elif component in {"issue_remarks", "return_remarks"}:
            table = Table(
                [[""]],
                colWidths=[width],
                rowHeights=[self.remarks_field_height_mm * mm],
                splitByRow=0,
            )
            table.setStyle(line_style)
            table.setStyle(TableStyle([("LINEBELOW", (0, 0), (0, -1), 0.65, "#222222")]))
            parts = [table]
        elif component in {"issue_time", "return_time"}:
            action = "выдачи" if component == "issue_time" else "возврата"
            parts = [
                single_row(
                    f"Дата и время фактического {action}:",
                    self.time_field_height_mm,
                    61,
                )
            ]
        elif component in {"issue_signatures", "return_signatures"}:
            data = [
                [
                    label("Арендодатель:"),
                    "",
                    label("/"),
                    "",
                    "",
                    label("Арендатор:"),
                    "",
                    label("/"),
                    "",
                ]
            ]
            table = Table(
                data,
                colWidths=[
                    27 * mm,
                    23 * mm,
                    4 * mm,
                    29 * mm,
                    4 * mm,
                    22 * mm,
                    23 * mm,
                    4 * mm,
                    width - 136 * mm,
                ],
                rowHeights=[self.signature_field_height_mm * mm],
                splitByRow=0,
            )
            table.setStyle(line_style)
            table.setStyle(
                TableStyle(
                    [
                        ("LINEBELOW", (1, 0), (1, 0), 0.65, "#222222"),
                        ("LINEBELOW", (3, 0), (3, 0), 0.65, "#222222"),
                        ("LINEBELOW", (6, 0), (6, 0), 0.65, "#222222"),
                        ("LINEBELOW", (8, 0), (8, 0), 0.65, "#222222"),
                    ]
                )
            )
            parts = [table]
        elif component == "deposit":
            table = Table(
                [
                    [
                        label("[ ] Возвращён полностью"),
                        label("[ ] Удержана сумма"),
                        "",
                        label("₽"),
                    ]
                ],
                colWidths=[50 * mm, 40 * mm, width - 98 * mm, 8 * mm],
                rowHeights=[self.deposit_field_height_mm * mm],
                splitByRow=0,
            )
            table.setStyle(line_style)
            table.setStyle(
                TableStyle(
                    [
                        ("LINEBELOW", (2, 0), (2, 0), 0.65, "#222222"),
                    ]
                )
            )
            parts = [table]
        else:
            raise AgreementTemplateValidationError(
                f"Unsupported handwriting component: {component}"
            )
        for part in parts:
            part.spaceAfter = 0.5
        return KeepTogether(parts)

    @staticmethod
    def page_count(pdf_bytes: bytes) -> int:
        """Count concrete page dictionaries in deterministic ReportLab output."""
        return len(PDF_PAGE_PATTERN.findall(pdf_bytes))

    def _require_two_pages(self, content: bytes) -> bytes:
        if self.page_count(content) != 2:
            raise AgreementDocumentTooLargeError(
                "Agreement does not fit the supported two-page layout (maximum 3 ordinary items)."
            )
        return content

    @classmethod
    def _handwriting_html(cls, component: str) -> str:
        field = '<span class="agreement-write-line" aria-hidden="true"></span>'
        if component == "tenant_name":
            return f'<div class="agreement-write-field"><b>Арендатор (Ф. И. О.):</b>{field}</div>'
        if component == "passport":
            return (
                '<div class="agreement-write-field"><span>Паспорт: серия</span>'
                f"{field}<span>№</span>{field}</div>"
            )
        if component == "passport_authority":
            return f'<div class="agreement-write-field"><span>Кем выдан:</span>{field}</div>'
        if component == "passport_date":
            return f'<div class="agreement-write-field"><span>Дата выдачи:</span>{field}</div>'
        if component == "passport_date_phone":
            return (
                '<div class="agreement-write-field agreement-write-field-pair">'
                f"<span>Дата выдачи:</span>{field}<span>Телефон:</span>{field}</div>"
            )
        if component == "tenant_phone":
            return f'<div class="agreement-write-field"><span>Телефон:</span>{field}</div>'
        if component in {"issue_remarks", "return_remarks"}:
            return '<div class="agreement-write-area"><span></span><span></span></div>'
        if component in {"issue_time", "return_time"}:
            action = "выдачи" if component == "issue_time" else "возврата"
            return (
                '<div class="agreement-write-field"><span>Дата и время фактического '
                f"{action}:</span>{field}</div>"
            )
        if component in {"issue_signatures", "return_signatures"}:
            return (
                '<div class="agreement-signatures">'
                f"<div><span>Арендодатель:</span>{field}<span>/</span>{field}</div>"
                f"<div><span>Арендатор:</span>{field}<span>/</span>{field}</div>"
                "</div>"
            )
        if component == "deposit":
            return (
                '<div class="agreement-deposit"><div><span>[ ] Возвращён полностью</span>'
                f"<span>[ ] Удержана сумма</span>{field}<span>₽</span></div></div>"
            )
        raise AgreementTemplateValidationError(f"Unsupported handwriting component: {component}")

    @staticmethod
    def _inline_html(value: str) -> str:
        escaped = html.escape(value, quote=True)
        return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)

    @staticmethod
    def _inline_pdf(value: str) -> str:
        escaped = html.escape(value, quote=True)
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)

    @classmethod
    def _register_fonts(cls) -> None:
        registered = set(pdfmetrics.getRegisteredFontNames())
        fonts_dir = Path(font_roboto.__file__).parent / "files"
        if cls.regular_font not in registered:
            pdfmetrics.registerFont(TTFont(cls.regular_font, fonts_dir / "Roboto-Regular.ttf"))
        if cls.bold_font not in registered:
            pdfmetrics.registerFont(TTFont(cls.bold_font, fonts_dir / "Roboto-Bold.ttf"))

    @classmethod
    def _footer(cls, canvas: Canvas, document: SimpleDocTemplate) -> None:
        canvas.saveState()
        canvas.setFont(cls.regular_font, 7)
        canvas.drawCentredString(A4[0] / 2, 6 * mm, f"Страница {document.page}")
        canvas.restoreState()

    @classmethod
    def _legacy_footer(cls, canvas: Canvas, document: SimpleDocTemplate) -> None:
        canvas.saveState()
        canvas.setFont(cls.regular_font, 6.5)
        canvas.drawCentredString(A4[0] / 2, 5.5 * mm, f"Страница {document.page}")
        canvas.restoreState()
