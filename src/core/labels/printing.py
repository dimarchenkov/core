from __future__ import annotations

import logging
import re
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

logger = logging.getLogger(__name__)
_JOB_ID_PATTERN = re.compile(r"request id is ([^\s]+)")


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

    @property
    def status(self) -> str:
        """Report submission, not unobservable physical printing."""
        return "submitted"


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


class CupsPrintingAdapter:
    """Submit controlled PDF jobs to an explicitly configured remote CUPS server."""

    def __init__(
        self, *, enabled: bool, server: str | None, user: str | None,
        command: str = "lp", ipp_version: str = "1.1",
    ) -> None:
        """Configure infrastructure exclusively from trusted runtime settings."""
        self._enabled = enabled
        self._server = server
        self._user = user
        self._command = command
        self._ipp_version = ipp_version

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
        if not self._enabled:
            raise LabelPrinterUnavailableError("Direct printing is disabled.")
        if not self._server or not self._user or not printer_name:
            raise LabelPrinterUnavailableError("Remote CUPS is not fully configured.")
        if not isinstance(quantity, int) or isinstance(quantity, bool) or not 1 <= quantity <= 500:
            raise ValueError("Label quantity must be between 1 and 500.")
        executable = shutil.which(self._command)
        if executable is None:
            raise LabelPrinterUnavailableError(
                "System print command is unavailable in the Core runtime."
            )
        path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
                source.write(content)
                path = Path(source.name)
            completed = subprocess.run(
                [
                    executable,
                    "-h",
                    f"{self._server}/version={self._ipp_version}",
                    "-U",
                    self._user,
                    "-d",
                    printer_name,
                    "-n",
                    str(quantity),
                    "-t",
                    job_name,
                    str(path),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            logger.warning(
                "CUPS submission timed out server=%s printer=%s",
                self._server,
                printer_name,
            )
            raise LabelPrintFailedError("Print server did not respond in time.") from exc
        except OSError as exc:
            logger.warning("CUPS client execution failed error=%s", type(exc).__name__)
            raise LabelPrintFailedError("System print command failed.") from exc
        finally:
            if path is not None:
                path.unlink(missing_ok=True)
        if completed.returncode != 0:
            logger.warning(
                "CUPS rejected submission server=%s printer=%s returncode=%s stderr=%s",
                self._server, printer_name, completed.returncode,
                (completed.stderr or "").strip()[:500],
            )
            raise LabelPrintFailedError("Print server rejected the label job.")
        output = completed.stdout.strip()
        match = _JOB_ID_PATTERN.search(output)
        job_id = match.group(1) if match else None
        return LabelPrintResult(printer_name, quantity, job_id)


# Compatibility name for existing application/tests; implementation is remote IPP.
CupsCommandLabelPrinter = CupsPrintingAdapter


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
        self._printer = printer or CupsPrintingAdapter(
            enabled=settings.printing_enabled,
            server=settings.cups_server,
            user=settings.cups_user,
            ipp_version=settings.cups_ipp_version,
        )

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
        if not self._settings.printing_enabled:
            raise LabelPrinterUnavailableError("Direct printing is disabled.")
        printer_name = self._settings.cups_printer
        if printer_name is None:
            raise LabelPrinterUnavailableError("Default label printer is not configured.")
        content = self._labels.generate(variant_id, profile)
        return self._printer.print_pdf(
            content,
            printer_name=printer_name,
            profile=profile,
            quantity=quantity,
            job_name=f"Core {variant_id} {profile.value}",
        )
