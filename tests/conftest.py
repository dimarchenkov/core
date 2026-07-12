from __future__ import annotations

import os

os.environ.setdefault("CORE_ENV", "test")
os.environ.setdefault("CORE_JWT_SECRET", "test-only-jwt-secret-at-least-32-bytes")
