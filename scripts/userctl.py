"""Manage application users and location grants through WH_SECURITY_ADMIN.

The CLI generates 256-bit bearer tokens and writes them only to git-ignored
``work/`` files with mode 0600. It never prints a token or accepts one as a
command-line argument. After bootstrap, an active ADMIN token file is required
and its verified identity becomes the database audit actor.
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import DatabaseUnavailable, OracleRepository  # noqa: E402
from app.security import Principal, SecurityRepository, generate_token, token_hash  # noqa: E402


class UserOperationError(RuntimeError):
    pass


def _database() -> OracleRepository:
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise UserOperationError("Install requirements.txt before managing users.") from exc
    load_dotenv(ROOT / ".env", override=False)
    return OracleRepository()


def _token_destination(path: Path) -> Path:
    work_root = ROOT / "work"
    if work_root.is_symlink():
        raise UserOperationError("The git-ignored work/ directory must not be a symlink.")
    work_root.mkdir(mode=0o700, exist_ok=True)
    work_root.chmod(0o700)
    target = path if path.is_absolute() else ROOT / path
    root_resolved = work_root.resolve()
    parent_resolved = target.parent.resolve(strict=False)
    if not parent_resolved.is_relative_to(root_resolved):
        raise UserOperationError("Token output must be inside this repository's git-ignored work/ directory.")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_resolved = target.parent.resolve()
    if not parent_resolved.is_relative_to(root_resolved):
        raise UserOperationError("Token output must stay inside this repository's git-ignored work/ directory.")
    if target.name in {"", ".", ".."}:
        raise UserOperationError("Choose a token file name inside work/.")
    return parent_resolved / target.name


def _write_token(path: Path, token: str) -> Path:
    target = _token_destination(path)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise UserOperationError("Token output already exists; choose a new path.") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        target.chmod(0o600)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def _admin_from_file(path: Path, security: SecurityRepository) -> Principal:
    try:
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise UserOperationError("The admin token file must be private (mode 0600).")
        token = path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise UserOperationError("The admin token file could not be read.") from exc
    principal = security.authenticate_token(token)
    if principal is None or principal.role != "ADMIN":
        raise UserOperationError("An active ADMIN token is required.")
    return principal


def _call(database: OracleRepository, procedure: str, arguments: list[str]) -> None:
    connection = database._connect()
    try:
        with connection.cursor() as cursor:
            cursor.callproc(f"WH_SECURITY_ADMIN.{procedure}", arguments)
        connection.commit()
    except Exception as exc:
        connection.rollback()
        raise UserOperationError(
            "Oracle rejected the access change. Check the IDs, current status, and package privileges."
        ) from exc
    finally:
        connection.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    bootstrap = commands.add_parser("bootstrap", help="Create the first ADMIN user (once per schema)")
    bootstrap.add_argument("--user-id", required=True)
    bootstrap.add_argument("--display-name", required=True)
    bootstrap.add_argument("--token-out", required=True, type=Path)

    def admin_command(name: str, help_text: str):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--admin-token-file", required=True, type=Path)
        return command

    create = admin_command("create-user", "Create a user and deliver a new private token")
    create.add_argument("--user-id", required=True)
    create.add_argument("--display-name", required=True)
    create.add_argument("--role", choices=("ADMIN", "READER"), default="READER")
    create.add_argument("--token-out", required=True, type=Path)

    rotate = admin_command("rotate-token", "Replace a user's token")
    rotate.add_argument("--user-id", required=True)
    rotate.add_argument("--token-out", required=True, type=Path)

    revoke_user = admin_command("revoke-user", "Deactivate a user and their grants")
    revoke_user.add_argument("--user-id", required=True)

    register = admin_command("register-location", "Register one canonical location key")
    register.add_argument("--location-key", required=True)
    register.add_argument("--display-name", required=True)

    status = admin_command("set-location-status", "Activate or deactivate a registered location")
    status.add_argument("--location-key", required=True)
    status.add_argument("--status", required=True, choices=("ACTIVE", "INACTIVE"))

    for name, description in (
        ("grant-location", "Give a user access to one active location"),
        ("revoke-location", "Remove a user's location access"),
    ):
        command = admin_command(name, description)
        command.add_argument("--user-id", required=True)
        command.add_argument("--location-key", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        database = _database()
        security = SecurityRepository(database)
        if not security.security_ready():
            raise UserOperationError("Security tables are missing. Run scripts/install_security.py --apply first.")
        if args.command == "bootstrap":
            token = generate_token()
            output = _write_token(args.token_out, token)
            try:
                _call(database, "BOOTSTRAP_ADMIN", [args.user_id, args.display_name, token_hash(token)])
            except Exception:
                output.unlink(missing_ok=True)
                raise
            print(f"Bootstrap administrator created. Token saved at {output} (mode 0600). Deliver it privately.")
            return 0

        principal = _admin_from_file(args.admin_token_file, security)
        actor = principal.user_id
        if args.command == "create-user":
            token = generate_token()
            output = _write_token(args.token_out, token)
            try:
                _call(database, "CREATE_USER", [args.user_id, args.display_name, token_hash(token), args.role, actor])
            except Exception:
                output.unlink(missing_ok=True)
                raise
            print(f"User created. Token saved at {output} (mode 0600). Deliver it privately.")
        elif args.command == "rotate-token":
            token = generate_token()
            output = _write_token(args.token_out, token)
            try:
                _call(database, "ROTATE_TOKEN", [args.user_id, token_hash(token), actor])
            except Exception:
                output.unlink(missing_ok=True)
                raise
            print(f"Token rotated. New token saved at {output} (mode 0600). Deliver it privately.")
        elif args.command == "revoke-user":
            _call(database, "REVOKE_USER", [args.user_id, actor])
            print(f"Revoked user {args.user_id} and their active location grants.")
        elif args.command == "register-location":
            _call(database, "REGISTER_LOCATION", [args.location_key, args.display_name, actor])
            print(f"Registered location {args.location_key.upper()}.")
        elif args.command == "set-location-status":
            _call(database, "SET_LOCATION_STATUS", [args.location_key, args.status, actor])
            print(f"Set location {args.location_key.upper()} to {args.status}.")
        elif args.command == "grant-location":
            _call(database, "GRANT_LOCATION", [args.user_id, args.location_key, actor])
            print(f"Granted {args.user_id} access to {args.location_key.upper()}.")
        elif args.command == "revoke-location":
            _call(database, "REVOKE_LOCATION", [args.user_id, args.location_key, actor])
            print(f"Revoked {args.user_id} access to {args.location_key.upper()}.")
        else:
            raise UserOperationError("Unknown access command.")
        return 0
    except (DatabaseUnavailable, UserOperationError, OSError, ValueError) as exc:
        print(f"Access operation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
