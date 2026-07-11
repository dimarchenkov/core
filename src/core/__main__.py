from __future__ import annotations

import sys
from collections.abc import Sequence

from core.identity.cli import main as identity_main


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch supported local Core command-line modules."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] != "identity":
        print("Usage: python -m core identity <command>", file=sys.stderr)
        return 2
    return identity_main(arguments[1:])


if __name__ == "__main__":
    raise SystemExit(main())
