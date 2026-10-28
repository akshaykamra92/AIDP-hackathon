"""Review, certify, and query portable Oracle view metrics.

Catalog changes require an active ADMIN bearer token. Reads use a verified
token and enforce its location grants in the SQL statement. Tokens are read
from private local files, never accepted as command-line values or printed.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from datetime import date
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import DatabaseUnavailable, OracleRepository  # noqa: E402
from app.metrics import CompileError  # noqa: E402
from app.registered_metrics import compile_registered  # noqa: E402
from app.registered_repo import CatalogError, RegisteredMetricRepository  # noqa: E402
from app.security import Principal, SecurityRepository  # noqa: E402


def _repository() -> RegisteredMetricRepository:
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise DatabaseUnavailable("Install requirements.txt before using metricctl.") from exc
    load_dotenv(ROOT / ".env", override=False)
    return RegisteredMetricRepository(OracleRepository())


def _date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use YYYY-MM-DD dates.") from exc


def _filter(text: str) -> tuple[str, Any]:
    key, separator, raw = text.partition("=")
    if not separator or not key or not raw:
        raise argparse.ArgumentTypeError("Use COLUMN=value for a filter.")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw
    return key, value


def _principal(path: Path, security: SecurityRepository, *, admin: bool) -> Principal:
    try:
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise CatalogError("TOKEN_FILE_PERMISSIONS", "The token file must be private (mode 0600).")
        token = path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise CatalogError("TOKEN_FILE_MISSING", "The token file could not be read.") from exc
    principal = security.authenticate_token(token)
    if principal is None:
        raise CatalogError("AUTH_REQUIRED", "An active application token is required.")
    if admin and principal.role != "ADMIN":
        raise CatalogError("ADMIN_REQUIRED", "An active ADMIN token is required for catalog management.")
    return principal


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list", help="List saved metric versions")
    listing.add_argument("metric_id", nargs="?", help="Limit to one metric ID")
    listing.add_argument("--admin-token-file", required=True, type=Path)

    draft = commands.add_parser("draft", help="Save a JSON manifest as a new Oracle DRAFT version")
    draft.add_argument("file", type=Path, help="Metric manifest JSON path")
    draft.add_argument("--admin-token-file", required=True, type=Path)

    review = commands.add_parser("review", help="Validate an owned view and mark its draft REVIEWED")
    review.add_argument("metric_id")
    review.add_argument("version", type=int)
    review.add_argument("--admin-token-file", required=True, type=Path)
    review.add_argument("--note", required=True, help="What was checked in the definition and source view")

    certify = commands.add_parser("certify", help="Confirm a reviewed version for certified answers")
    certify.add_argument("metric_id")
    certify.add_argument("version", type=int)
    certify.add_argument("--admin-token-file", required=True, type=Path, help="Must differ from the reviewer's ADMIN token")

    show = commands.add_parser("show", help="Inspect one saved metric version")
    show.add_argument("metric_id")
    show.add_argument("version", type=int)
    show.add_argument("--admin-token-file", required=True, type=Path)

    for name in ("preview", "query"):
        command = commands.add_parser(
            name,
            help="Run an unapproved read-only preview" if name == "preview" else "Run a certified, audited query",
        )
        command.add_argument("metric_id")
        if name == "preview":
            command.add_argument("version", type=int)
            command.add_argument("--admin-token-file", required=True, type=Path)
        else:
            command.add_argument("--token-file", required=True, type=Path)
        command.add_argument("--start", type=_date, required=True)
        command.add_argument("--end", type=_date, required=True)
        command.add_argument("--dimension", action="append", default=[], help="Approved uppercase view column")
        command.add_argument("--filter", action="append", type=_filter, default=[], help="Approved COLUMN=value")
    return parser


def _filters(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        canonical = key.upper()
        if canonical in result:
            raise CatalogError("DUPLICATE_FILTER", "A filter column was repeated.")
        result[canonical] = value
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        repo = _repository()
        security = SecurityRepository(repo.database)
        if not security.security_ready():
            raise CatalogError("SECURITY_NOT_INSTALLED", "Run scripts/install_security.py --apply before metric operations.")
        principal = _principal(
            args.token_file if args.command == "query" else args.admin_token_file,
            security,
            admin=args.command != "query",
        )
        if args.command == "list":
            versions = repo.list_versions(args.metric_id)
            if not versions:
                print("No registered metric versions found.")
            for metric in versions:
                print(f"{metric.metric_id} v{metric.version} {metric.status} | {metric.name} | {metric.source_view}")
        elif args.command == "draft":
            manifest = json.loads(args.file.read_text(encoding="utf-8"))
            metric = repo.create_draft(manifest)
            print(f"Saved {metric.metric_id} v{metric.version} as DRAFT in Oracle.")
        elif args.command == "review":
            metric = repo.review_draft(args.metric_id, args.version, principal.user_id, args.note)
            print(f"Reviewed {metric.metric_id} v{metric.version}; source view was checked.")
        elif args.command == "certify":
            metric = repo.certify(args.metric_id, args.version, principal.user_id)
            print(f"Certified {metric.metric_id} v{metric.version}; earlier certified version retired.")
        elif args.command == "show":
            metric = repo.get_version(args.metric_id, args.version)
            if metric is None:
                raise CatalogError("METRIC_NOT_FOUND", "That metric version does not exist.")
            print(json.dumps(metric.public_dict(), indent=2))
        elif args.command == "preview":
            metric = repo.get_version(args.metric_id, args.version)
            if metric is None:
                raise CatalogError("METRIC_NOT_FOUND", "That metric version does not exist.")
            if metric.status == "RETIRED":
                raise CatalogError("METRIC_RETIRED", "Retired versions are not available for preview.")
            repo.validate_source(metric)
            compiled = compile_registered(
                metric, args.start, args.end,
                [name.upper() for name in args.dimension], _filters(args.filter),
                require_certified=False, scope_user_id=principal.user_id,
            )
            rows = repo.database.run_readonly(
                compiled.sql, compiled.binds, client_identifier=principal.user_id,
            )
            print("UNAPPROVED PREVIEW — this result is not a certified answer")
            print(json.dumps({"metric": metric.public_dict(), "rows": rows, "sql": compiled.sql, "binds": compiled.binds}, default=str, indent=2))
        else:
            result = repo.query(
                args.metric_id, args.start, args.end,
                [name.upper() for name in args.dimension], _filters(args.filter),
                user_id=principal.user_id,
            )
            print(json.dumps(result, indent=2))
        return 0
    except (CatalogError, CompileError, DatabaseUnavailable, OSError, ValueError) as exc:
        print(f"Metric operation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
