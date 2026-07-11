from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Sequence

from core.database import SessionLocal
from core.identity.service import (
    FullNameRequiredError,
    IdentityService,
    PasswordValidationError,
    SuperuserAlreadyDisabledError,
    SuperuserAlreadyEnabledError,
    SuperuserReasonRequiredError,
    UserAlreadyExistsError,
    UserNotFoundOrInactiveError,
)


def main(argv: Sequence[str] | None = None) -> int:
    """Run local identity administration commands."""
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        with SessionLocal() as session:
            service = IdentityService(session)
            if arguments.command == "create-admin":
                _create_admin(service)
            elif arguments.command == "enable-superuser":
                service.enable_superuser(
                    arguments.email,
                    arguments.reason,
                    _actor_description(),
                )
            elif arguments.command == "disable-superuser":
                service.disable_superuser(
                    arguments.email,
                    arguments.reason,
                    _actor_description(),
                )
    except (
        FullNameRequiredError,
        PasswordValidationError,
        SuperuserAlreadyDisabledError,
        SuperuserAlreadyEnabledError,
        SuperuserReasonRequiredError,
        UserAlreadyExistsError,
        UserNotFoundOrInactiveError,
    ) as exc:
        print(_error_message(exc), file=sys.stderr)
        return 1

    print("Identity command completed.")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Build the limited local identity command parser."""
    parser = argparse.ArgumentParser(prog="core identity")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("create-admin")
    enable = subparsers.add_parser("enable-superuser")
    enable.add_argument("email")
    enable.add_argument("--reason", required=True)
    disable = subparsers.add_parser("disable-superuser")
    disable.add_argument("email")
    disable.add_argument("--reason")
    return parser


def _create_admin(service: IdentityService) -> None:
    """Prompt locally for administrator details and create the account."""
    email = input("Email: ")
    full_name = input("Full name: ")
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise PasswordValidationError
    service.create_admin(email, full_name, password)


def _actor_description() -> str:
    """Return a local operating-system user description when available."""
    try:
        return f"local:{getpass.getuser()}"
    except (KeyError, OSError):
        return "local:unknown"


def _error_message(error: Exception) -> str:
    """Return a clear human-readable message for expected CLI failures."""
    messages: dict[type[Exception], str] = {
        FullNameRequiredError: "Full name is required.",
        PasswordValidationError: "Password must contain at least 12 non-whitespace characters.",
        SuperuserAlreadyDisabledError: "Superuser access is already disabled.",
        SuperuserAlreadyEnabledError: "Superuser access is already enabled.",
        SuperuserReasonRequiredError: "A non-empty reason is required to enable superuser access.",
        UserAlreadyExistsError: "A user with this email already exists.",
        UserNotFoundOrInactiveError: "The user does not exist or is inactive.",
    }
    return messages[type(error)]
