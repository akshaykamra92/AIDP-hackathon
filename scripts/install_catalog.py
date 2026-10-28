"""Install the non-destructive, portable Oracle metric catalog and governance package.

Only the repository-owned migration and package files are accepted. This command
never runs the destructive synthetic-demo deployment script and never prints
connection settings. Run ``--dry-run`` before ``--apply``.
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

from scripts.deploy import DeploymentError, _connect, _execute_stage, _oracle_code, split_sql_script  # noqa: E402


CATALOG_SQL = ROOT / "sql" / "04_portable_catalog.sql"
PACKAGE_SPEC = ROOT / "sql" / "05_metric_governance.pks"
PACKAGE_BODY = ROOT / "sql" / "05_metric_governance.pkb"
TABLES = frozenset({"WH_REGISTERED_METRIC", "WH_REGISTERED_ANSWER_LOG"})

_CREATE_TABLE = re.compile(
    r"\ACREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(WH_REGISTERED_METRIC|WH_REGISTERED_ANSWER_LOG)\s*\(",
    re.IGNORECASE | re.DOTALL,
)
_CREATE_INDEX = re.compile(
    r"\ACREATE\s+UNIQUE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+WH_RM_ONE_CERTIFIED_UX\s+"
    r"ON\s+WH_REGISTERED_METRIC\s*\(\s*CASE\s+WHEN\s+STATUS\s*=\s*'CERTIFIED'\s+"
    r"THEN\s+METRIC_ID\s+END\s*\)\Z",
    re.IGNORECASE | re.DOTALL,
)
_COMMENT = re.compile(
    r"\ACOMMENT\s+ON\s+(TABLE|COLUMN)\s+"
    r"(WH_REGISTERED_METRIC|WH_REGISTERED_ANSWER_LOG)(?:\.([A-Z_]+))?\s+"
    r"IS\s+'(?:''|[^'])*'\Z",
    re.IGNORECASE | re.DOTALL,
)

REQUIRED_COLUMNS = {
    "WH_REGISTERED_METRIC": frozenset({
        "METRIC_ID", "VERSION", "NAME", "OWNER", "DEFINITION_TEXT", "UNIT",
        "SOURCE_VIEW", "DATE_COLUMN", "VALUE_COLUMN", "AGGREGATION",
        "ALLOWED_DIMS", "ALLOWED_FILTERS", "STATUS", "CREATED_AT",
        "REVIEWED_AT", "REVIEWED_BY", "REVIEW_NOTE", "CERTIFIED_AT",
        "CERTIFIED_BY", "SOURCE_DDL_AT",
    }),
    "WH_REGISTERED_ANSWER_LOG": frozenset({
        "ID", "ASKED_AT", "METRIC_ID", "METRIC_VERSION", "QUERY_JSON",
        "COMPILED_SQL", "BIND_JSON", "ROW_COUNT",
    }),
}
REQUIRED_CONSTRAINTS = frozenset({
    "WH_RM_PK", "WH_RM_VERSION_CK", "WH_RM_AGG_CK", "WH_RM_STATUS_CK",
    "WH_RM_VALUE_CK", "WH_RM_DIMS_JSON_CK", "WH_RM_FILTERS_JSON_CK",
    "WH_RM_REVIEWED_CK", "WH_RM_CERTIFIED_CK", "WH_RAL_PK",
    "WH_RAL_METRIC_FK", "WH_RAL_QUERY_JSON_CK", "WH_RAL_BIND_JSON_CK",
    "WH_RAL_ROW_COUNT_CK",
})


def load_catalog_statements() -> list[str]:
    """Read the fixed migration and reject any statement outside its safe shape."""

    statements = split_sql_script(CATALOG_SQL.read_text(encoding="utf-8"))
    if not statements:
        raise DeploymentError("Portable catalog migration has no statements.")
    created: set[str] = set()
    index_count = 0
    for number, statement in enumerate(statements, 1):
        create_match = _CREATE_TABLE.match(statement)
        if create_match:
            table_name = create_match.group(1).upper()
            if table_name in created:
                raise DeploymentError(f"Migration creates {table_name} more than once.")
            created.add(table_name)
            continue
        if _CREATE_INDEX.fullmatch(statement):
            index_count += 1
            continue
        comment_match = _COMMENT.fullmatch(statement)
        if comment_match:
            kind, _table_name, column_name = comment_match.groups()
            if (kind.upper() == "TABLE" and column_name) or (kind.upper() == "COLUMN" and not column_name):
                raise DeploymentError(f"Migration comment {number} has an invalid target.")
            continue
        raise DeploymentError(f"Migration statement {number} is not an approved non-destructive DDL statement.")
    if created != TABLES:
        raise DeploymentError("Migration must create the two registered catalog tables idempotently.")
    if index_count != 1:
        raise DeploymentError("Migration must create one current-certified uniqueness index idempotently.")
    return statements


def load_package_sources() -> tuple[str, str]:
    """Read the two complete PL/SQL units; do not semicolon-split PL/SQL."""

    spec = PACKAGE_SPEC.read_text(encoding="utf-8").strip()
    body = PACKAGE_BODY.read_text(encoding="utf-8").strip()
    expected = (
        (spec, r"\ACREATE\s+OR\s+REPLACE\s+PACKAGE\s+WH_METRIC_GOVERNANCE\s+AUTHID\s+DEFINER\s+AS\b"),
        (body, r"\ACREATE\s+OR\s+REPLACE\s+PACKAGE\s+BODY\s+WH_METRIC_GOVERNANCE\s+AS\b"),
    )
    for source, prefix in expected:
        if not re.match(prefix, source, flags=re.IGNORECASE | re.DOTALL):
            raise DeploymentError("Governance package source has an unexpected declaration.")
        if not re.search(r"\bEND\s+WH_METRIC_GOVERNANCE\s*;\s*\Z", source, flags=re.IGNORECASE):
            raise DeploymentError("Governance package source is incomplete.")
        if re.search(r"(?m)^\s*/\s*$", source):
            raise DeploymentError("Governance package files must omit SQLcl slash delimiters.")
    return spec, body


def install_package(connection, spec: str, body: str) -> None:
    with connection.cursor() as cursor:
        for unit, source in (("specification", spec), ("body", body)):
            try:
                cursor.execute(source)
            except Exception as exc:
                raise DeploymentError(
                    f"Governance package {unit} failed ({_oracle_code(exc)})."
                ) from exc
    print("Applied governance package specification and body.")


def verify_install(connection) -> None:
    """Catch an incompatible pre-existing table or an invalid compiled package."""

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT TABLE_NAME, COLUMN_NAME FROM USER_TAB_COLUMNS "
                "WHERE TABLE_NAME IN ('WH_REGISTERED_METRIC', 'WH_REGISTERED_ANSWER_LOG')"
            )
            columns = {table: set() for table in TABLES}
            for table_name, column_name in cursor.fetchall():
                columns[str(table_name)].add(str(column_name))
            if any(not required.issubset(columns[table]) for table, required in REQUIRED_COLUMNS.items()):
                raise DeploymentError("Portable catalog tables are missing required columns.")

            cursor.execute(
                "SELECT CONSTRAINT_NAME, STATUS, VALIDATED FROM USER_CONSTRAINTS "
                "WHERE TABLE_NAME IN ('WH_REGISTERED_METRIC', 'WH_REGISTERED_ANSWER_LOG')"
            )
            constraints = {
                str(name): (str(status), str(validated))
                for name, status, validated in cursor.fetchall()
            }
            if any(constraints.get(name) != ("ENABLED", "VALIDATED") for name in REQUIRED_CONSTRAINTS):
                raise DeploymentError("Portable catalog constraints are missing or disabled.")

            cursor.execute(
                "SELECT UNIQUENESS, STATUS FROM USER_INDEXES "
                "WHERE INDEX_NAME = 'WH_RM_ONE_CERTIFIED_UX' "
                "AND TABLE_NAME = 'WH_REGISTERED_METRIC'"
            )
            if cursor.fetchall() != [("UNIQUE", "VALID")]:
                raise DeploymentError("Current-certified uniqueness index is missing or invalid.")

            cursor.execute(
                "SELECT OBJECT_TYPE, STATUS FROM USER_OBJECTS "
                "WHERE OBJECT_NAME = 'WH_METRIC_GOVERNANCE' "
                "AND OBJECT_TYPE IN ('PACKAGE', 'PACKAGE BODY')"
            )
            package_units = {str(kind): str(status) for kind, status in cursor.fetchall()}
            if package_units != {"PACKAGE": "VALID", "PACKAGE BODY": "VALID"}:
                raise DeploymentError("Governance package did not compile as VALID.")
    except DeploymentError:
        raise
    except Exception as exc:
        raise DeploymentError(f"Portable catalog verification failed ({_oracle_code(exc)}).") from exc
    print("Portable catalog verified: tables, constraints, and VALID governance package.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Validate reviewed files without connecting")
    mode.add_argument("--apply", action="store_true", help="Install or refresh the non-destructive catalog")
    mode.add_argument("--verify-only", action="store_true", help="Check an existing catalog without writing")
    args = parser.parse_args(argv)

    try:
        statements = load_catalog_statements()
        spec, body = load_package_sources()
        if args.dry_run:
            print(f"Parsed portable catalog: {len(statements)} non-destructive DDL statements and 2 package units.")
            return 0

        connection = _connect()
        try:
            if args.apply:
                _execute_stage(connection, "portable catalog", statements)
                install_package(connection, spec, body)
            verify_install(connection)
        finally:
            connection.close()
        return 0
    except (DeploymentError, OSError) as exc:
        print(f"Portable catalog installation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
