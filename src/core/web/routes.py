from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(include_in_schema=False)
static_root = Path(__file__).parent / "static"


@router.get("/app", response_class=FileResponse)
def workflow_app() -> FileResponse:
    """Deliver the phone-first Core workflow client."""
    return FileResponse(static_root / "index.html", media_type="text/html")
