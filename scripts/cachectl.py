"""Manage opt-in certified result caching through WH_CACHE_ADMIN.

Use an active administrator token stored in a private local file. Setting a
policy requires an explicit certified metric version. To guarantee immediate
freshness after source changes, call BUMP_SOURCE_REVISION within the *same*
database transaction as ingestion; this CLI provides manual invalidation only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import DatabaseUnavailable, OracleRepository  # noqa: E402
from app.security import SecurityRepository  # noqa: E402
from scripts.userctl import UserOperationError, _admin_from_file, _database  # noqa: E402


class CacheOperationError(RuntimeError):
    pass


def _call(database: OracleRepository, procedure: str, arguments: list[object]) -> int | None:
    connection = database._connect()
    try:
        with connection.cursor() as cursor:
            if procedure == "PURGE_EXPIRED":
                count = cursor.var(int)
                cursor.callproc("WH_CACHE_ADMIN.PURGE_EXPIRED", [*arguments, count])
                result = count.getvalue()
            else:
                cursor.callproc(f"WH_CACHE_ADMIN.{procedure}", arguments)
                result = None
        connection.commit()
        return int(result) if result is not None else None
    except Exception as exc:
        connection.rollback()
        raise CacheOperationError(
            "Oracle rejected the cache change. Check metric certification, values, and package privileges."
        ) from exc
    finally:
        connection.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python scripts/install_cache.py --apply\n"
            "  python scripts/cachectl.py set-policy --metric-id FILLED_REQUISITIONS "
            "--version 3 --enabled Y --ttl-seconds 1800 "
            "--admin-token-file work/tokens/admin.token\n"
            "  Set RESULT_CACHE_ENABLED=true on the portable app to serve hits.\n"
            "  After source updates, bump its revision in the ingestion transaction."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    policy = commands.add_parser("set-policy", help="Enable or disable caching for one certified metric version")
    policy.add_argument("--metric-id", required=True, help="Certified WH_REGISTERED_METRIC identifier")
    policy.add_argument("--version", required=True, type=int, help="Exact certified metric version")
    policy.add_argument("--enabled", required=True, choices=("Y", "N"), help="Y opts in; N turns caching off")
    policy.add_argument("--ttl-seconds", required=True, type=int, help="Maximum result age, 1 to 86400 seconds")
    policy.add_argument("--admin-token-file", required=True, type=Path, help="Private file containing an active ADMIN bearer token")

    bump = commands.add_parser("bump-source", help="Manually invalidate cached results for one WH_ adapter view")
    bump.add_argument("--source-view", required=True, help="Owned WH_ adapter view whose data changed")
    bump.add_argument("--admin-token-file", required=True, type=Path, help="Private file containing an active ADMIN bearer token")

    purge = commands.add_parser("purge-expired", help="Delete expired cache entries")
    purge.add_argument("--admin-token-file", required=True, type=Path, help="Private file containing an active ADMIN bearer token")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        database = _database()
        principal = _admin_from_file(args.admin_token_file, SecurityRepository(database))
        actor = principal.user_id
        if args.command == "set-policy":
            if args.version < 1 or not 1 <= args.ttl_seconds <= 86400:
                raise CacheOperationError("Version must be positive and TTL must be 1–86400 seconds.")
            _call(database, "SET_POLICY", [
                args.metric_id.upper(), args.version, args.enabled,
                args.ttl_seconds, actor,
            ])
            print(f"Cache policy for {args.metric_id.upper()} v{args.version}: {args.enabled}, TTL {args.ttl_seconds}s.")
        elif args.command == "bump-source":
            _call(database, "BUMP_SOURCE_REVISION", [args.source_view.upper(), actor])
            print(f"Invalidated cache for source {args.source_view.upper()}.")
        elif args.command == "purge-expired":
            count = _call(database, "PURGE_EXPIRED", [actor])
            print(f"Purged {count} expired cache entries.")
        else:
            raise CacheOperationError("Unknown cache command.")
        return 0
    except (CacheOperationError, DatabaseUnavailable, UserOperationError, OSError, ValueError) as exc:
        print(f"Cache operation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
