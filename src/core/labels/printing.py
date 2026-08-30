from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from core.config import Settings
from core.labels.renderer import LabelProfile
from core.labels.service import VariantLabelService
from core.shared.db import UUIDv7


class LabelPrinterUnavailableError(Exception):
    """Raised when no configured system print queue can be reached."""


class LabelPrintFailedError(Exception):
    """Raised when the operating-system print command rejects a job."""


@dataclass(frozen=True, slots=True)
class LabelPrintResult:
    """Sanitized acknowledgement returned after a print job is accepted."""

    printer_name: str
    quantity: int
    job_id: str | None = None


class LabelPrinterAdapter(Protocol):
    """Infrastructure boundary for sending an already rendered label to a printer."""

    def print_pdf(
        self,
        content: bytes,
        *,
        printer_name: str,
        profile: LabelProfile,
        quantity: int,
        job_name: str,
    ) -> LabelPrintResult:
        """Submit one PDF label with an explicit copy count."""
        ...


class CupsCommandLabelPrinter:
    """Submit exact-size PDFs through the host's standard CUPS `lp` command."""

    def __init__(self, command: str = "lp") -> None:
        """Configure the system command used to submit CUPS jobs."""
        self._command = command

    def print_pdf(
        self,
        content: bytes,
        *,
        printer_name: str,
        profile: LabelProfile,
        quantity: int,
        job_name: str,
    ) -> LabelPrintResult:
        """Write a temporary PDF and submit it to the named CUPS queue."""
        executable = shutil.which(self._command)
        if executable is None:
            raise LabelPrinterUnavailableError(
                "System print command is unavailable in the Core runtime."
            )
        width, height = profile.value.split("x", maxsplit=1)
        path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
                source.write(content)
                path = Path(source.name)
            completed = subprocess.run(
                [
                    executable,
                    "-d",
                    printer_name,
                    "-n",
                    str(quantity),
                    "-t",
                    job_name,
                    "-o",
                    f"media=Custom.{width}x{height}mm",
                    "-o",
                    "Resolution=203dpi",
                    "-o",
                    "PaperType=LabelGaps",
                    "-o",
                    "fit-to-page=false",
                    str(path),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LabelPrintFailedError("System print command failed.") from exc
        finally:
            if path is not None:
                path.unlink(missing_ok=True)
        if completed.returncode != 0:
            raise LabelPrintFailedError(
                (completed.stderr or "System print queue rejected the label job.").strip()[:500]
            )
        output = completed.stdout.strip()
        job_id = output.rsplit(" ", maxsplit=1)[-1] if output else None
        return LabelPrintResult(printer_name, quantity, job_id)


class VariantLabelPrintService:
    """Render and submit labels without coupling Catalog or Intake to CUPS."""

    def __init__(
        self,
        labels: VariantLabelService,
        settings: Settings,
        printer: LabelPrinterAdapter | None = None,
    ) -> None:
        """Bind authoritative label generation to an infrastructure adapter."""
        self._labels = labels
        self._settings = settings
        self._printer = printer or CupsCommandLabelPrinter(settings.label_printer_command)

    def print(
        self,
        variant_id: UUIDv7,
        *,
        quantity: int,
        profile: LabelProfile = LabelProfile.COMPACT_40X30,
    ) -> LabelPrintResult:
        """Send an exact-size one-label PDF with an explicit CUPS copy count."""
        if not 1 <= quantity <= 500:
            raise ValueError("Label quantity must be between 1 and 500.")
        printer_name = self._settings.label_printer_name
        if printer_name is None:
            raise LabelPrinterUnavailableError("Default label printer is not configured.")
        content = self._labels.generate(variant_id, profile, quantity=1)
        return self._printer.print_pdf(
            content,
            printer_name=printer_name,
            profile=profile,
            quantity=quantity,
            job_name=f"Core {variant_id} {profile.value}",
        )
