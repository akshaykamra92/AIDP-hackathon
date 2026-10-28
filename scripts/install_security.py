"""Install the non-destructive Oracle application security schema and package.

The migration creates no users or location grants. Tokens never appear in this
installer, its output, or repository files. Run ``--dry-run`` before ``--apply``.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.deploy import (  # noqa: E402
    DeploymentError,
    _connect,
    _execute_stage,
    _oracle_code,
    split_sql_script,
)


SECURITY_SQL = ROOT / "sql" / "06_security.sql"
PACKAGE_SPEC = ROOT / "sql" / "07_security.pks"
PACKAGE_BODY = ROOT / "sql" / "07_security.pkb"

TABLES = frozenset({
    "WH_APP_USER", "WH_SEC_LOCATION", "WH_USER_LOCATION", "WH_ACCESS_AUDIT",
})
_CREATE_TABLE = re.compile(
    r"\ACREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+"
    r"(WH_APP_USER|WH_SEC_LOCATION|WH_USER_LOCATION|WH_ACCESS_AUDIT)\s*\(",
    re.IGNORECASE | re.DOTALL,
)
_COMMENT = re.compile(
    r"\ACOMMENT\s+ON\s+(TABLE|COLUMN)\s+"
    r"(WH_APP_USER|WH_SEC_LOCATION|WH_USER_LOCATION|WH_ACCESS_AUDIT)"
    r"(?:\.([A-Z_]+))?\s+IS\s+'(?:''|[^'])*'\Z",
    re.IGNORECASE | re.DOTALL,
)

REQUIRED_COLUMNS = {
    "WH_APP_USER": frozenset({
        "USER_ID", "DISPLAY_NAME", "TOKEN_HASH", "ROLE", "STATUS",
        "CREATED_AT", "CREATED_BY", "TOKEN_ROTATED_AT", "REVOKED_AT",
        "REVOKED_BY",
    }),
    "WH_SEC_LOCATION": frozenset({
        "LOCATION_KEY", "DISPLAY_NAME", "STATUS", "CREATED_AT",
        "CREATED_BY", "STATUS_AT", "STATUS_BY",
    }),
    "WH_USER_LOCATION": frozenset({
        "USER_ID", "LOCATION_KEY", "STATUS", "GRANTED_AT", "GRANTED_BY",
        "REVOKED_AT", "REVOKED_BY",
    }),
    "WH_ACCESS_AUDIT": frozenset({
        "EVENT_ID", "EVENT_AT", "ACTOR_USER_ID", "ACTION",
        "TARGET_USER_ID", "LOCATION_KEY",
    }),
}
REQUIRED_CONSTRAINTS = frozenset({
    "WH_AU_PK", "WH_AU_ID_CK", "WH_AU_TOKEN_UK", "WH_AU_TOKEN_CK", "WH_AU_ROLE_CK",
    "WH_AU_STATUS_CK", "WH_AU_REVOKED_CK", "WH_SL_PK", "WH_SL_KEY_CK",
    "WH_SL_STATUS_CK", "WH_UL_PK", "WH_UL_USER_FK", "WH_UL_LOCATION_FK",
    "WH_UL_STATUS_CK", "WH_UL_REVOKED_CK", "WH_AA_PK", "WH_AA_ACTION_CK",
})


def load_security_statements() -> list[str]:
    """Allow only the four fixed idempotent table DDLs and their comments."""

    statements = split_sql_script(SECURITY_SQL.read_text(encoding="utf-8"))
    if not statements:
        raise DeploymentError("Security migration has no statements.")
    created: set[str] = set()
    for number, statement in enumerate(statements, 1):
        create_match = _CREATE_TABLE.match(statement)
        if create_match:
            table_name = create_match.group(1).upper()
            if table_name in created:
                raise DeploymentError(f"Migration creates {table_name} more than once.")
            created.add(table_name)
            continue
        comment_match = _COMMENT.fullmatch(statement)
        if comment_match:
            kind, _table, column = comment_match.groups()
            if (kind.upper() == "TABLE" and column) or (kind.upper() == "COLUMN" and not column):
                raise DeploymentError(f"Migration comment {number} has an invalid target.")
            continue
        raise DeploymentError(f"Migration statement {number} is not approved non-destructive DDL.")
    if created != TABLES:
        raise DeploymentError("Migration must create all four security tables idempotently.")
    ddl = "\n".join(statement for statement in statements if statement.startswith("CREATE TABLE"))
    missing = {
        name for name in REQUIRED_CONSTRAINTS
        if not re.search(rf"\bCONSTRAINT\s+{name}\b", ddl, flags=re.IGNORECASE)
    }
    if missing:
        raise DeploymentError("Security migration is missing required constraints.")
    return statements


def load_package_sources() -> tuple[str, str]:
    """Read two complete PL/SQL units without splitting at internal semicolons."""

    spec = PACKAGE_SPEC.read_text(encoding="utf-8").strip()
    body = PACKAGE_BODY.read_text(encoding="utf-8").strip()
    expected = (
        (spec, r"\ACREATE\s+OR\s+REPLACE\s+PACKAGE\s+WH_SECURITY_ADMIN\s+AUTHID\s+DEFINER\s+AS\b"),
        (body, r"\ACREATE\s+OR\s+REPLACE\s+PACKAGE\s+BODY\s+WH_SECURITY_ADMIN\s+AS\b"),
    )
    for source, prefix in expected:
        if not re.match(prefix, source, flags=re.IGNORECASE | re.DOTALL):
            raise DeploymentError("Security package source has an unexpected declaration.")
        if not re.search(r"\bEND\s+WH_SECURITY_ADMIN\s*;\s*\Z", source, flags=re.IGNORECASE):
            raise DeploymentError("Security package source is incomplete.")
        if re.search(r"(?m)^\s*/\s*$", source):
            raise DeploymentError("Security package source must omit SQLcl slash delimiters.")
    return spec, body


def install_package(connection, spec: str, body: str) -> None:
    with connection.cursor() as cursor:
        for unit, source in (("specification", spec), ("body", body)):
            try:
                cursor.execute(source)
            except Exception as exc:
                raise DeploymentError(
                    f"Security package {unit} failed ({_oracle_code(exc)})."
                ) from exc
    print("Applied security package specification and body.")


def verify_install(connection) -> None:
    """Reject a missing or incompatible pre-existing security installation."""

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, CHAR_LENGTH "
                "FROM USER_TAB_COLUMNS WHERE TABLE_NAME IN "
                "('WH_APP_USER', 'WH_SEC_LOCATION', 'WH_USER_LOCATION', 'WH_ACCESS_AUDIT')"
            )
            columns = {table: {} for table in TABLES}
            for table_name, column_name, data_type, char_length in cursor.fetchall():
                columns[str(table_name)][str(column_name)] = (str(data_type), char_length)
            if any(not required.issubset(columns[table]) for table, required in REQUIRED_COLUMNS.items()):
                raise DeploymentError("Security tables are missing required columns.")
            token_type, token_length = columns["WH_APP_USER"]["TOKEN_HASH"]
            if token_type != "VARCHAR2" or int(token_length) != 64:
                raise DeploymentError("WH_APP_USER.TOKEN_HASH must be VARCHAR2(64).")

            cursor.execute(
                "SELECT CONSTRAINT_NAME, STATUS, VALIDATED FROM USER_CONSTRAINTS "
                "WHERE TABLE_NAME IN "
                "('WH_APP_USER', 'WH_SEC_LOCATION', 'WH_USER_LOCATION', 'WH_ACCESS_AUDIT')"
            )
            constraints = {
                str(name): (str(status), str(validated))
                for name, status, validated in cursor.fetchall()
            }
            if any(constraints.get(name) != ("ENABLED", "VALIDATED") for name in REQUIRED_CONSTRAINTS):
                raise DeploymentError("Security constraints are missing or disabled.")

            cursor.execute(
                "SELECT OBJECT_TYPE, STATUS FROM USER_OBJECTS "
                "WHERE OBJECT_NAME = 'WH_SECURITY_ADMIN' "
                "AND OBJECT_TYPE IN ('PACKAGE', 'PACKAGE BODY')"
            )
            units = {str(kind): str(status) for kind, status in cursor.fetchall()}
            if units != {"PACKAGE": "VALID", "PACKAGE BODY": "VALID"}:
                raise DeploymentError("Security package did not compile as VALID.")
    except DeploymentError:
        raise
    except Exception as exc:
        raise DeploymentError(f"Security installation verification failed ({_oracle_code(exc)}).") from exc
    print("Security installation verified: four tables, constraints, and VALID package.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Validate reviewed files without connecting")
    mode.add_argument("--apply", action="store_true", help="Install or refresh the non-destructive security schema")
    mode.add_argument("--verify-only", action="store_true", help="Check an existing security schema without writing")
    args = parser.parse_args(argv)

    try:
        statements = load_security_statements()
        spec, body = load_package_sources()
        if args.dry_run:
            print(f"Parsed security schema: {len(statements)} non-destructive DDL statements and 2 package units.")
            return 0

        connection = _connect()
        try:
            if args.apply:
                _execute_stage(connection, "security schema", statements)
                install_package(connection, spec, body)
            verify_install(connection)
        finally:
            connection.close()
        return 0
    except (DeploymentError, OSError) as exc:
        print(f"Security installation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
