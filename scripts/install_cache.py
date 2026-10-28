"""Install the optional Oracle result cache without changing source data.

Install after scripts/install_catalog.py and scripts/install_security.py. The
cache is disabled for every metric until an administrator opts in to a specific
certified version. This installer never prints connection settings or tokens.
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
    DeploymentError, _connect, _execute_stage, _oracle_code, split_sql_script,
)


SCHEMA_SQL = ROOT / "sql" / "08_result_cache.sql"
PACKAGE_SPEC = ROOT / "sql" / "09_cache_admin.pks"
PACKAGE_BODY = ROOT / "sql" / "09_cache_admin.pkb"
TRIGGER_FILES = (
    ("WH_CACHE_USER_REV_TRG", ROOT / "sql" / "10_cache_user_revision.trg"),
    ("WH_CACHE_LOCATION_REV_TRG", ROOT / "sql" / "10_cache_location_revision.trg"),
    ("WH_CACHE_GRANT_REV_TRG", ROOT / "sql" / "10_cache_grant_revision.trg"),
    ("WH_CACHE_POLICY_REV_TRG", ROOT / "sql" / "10_cache_policy_revision.trg"),
    ("WH_CACHE_SOURCE_REV_TRG", ROOT / "sql" / "10_cache_source_revision.trg"),
)
TABLES = frozenset({
    "WH_CACHE_STATE", "WH_CACHE_SOURCE", "WH_CACHE_POLICY", "WH_QUERY_CACHE",
})
INDEXES = frozenset({"WH_QC_EXPIRY_IX", "WH_QC_USER_METRIC_IX"})
AUDIT_COLUMNS = {
    "CACHE_STATUS": "VARCHAR2(8)",
    "CACHE_KEY": "VARCHAR2(64)",
    "RESULT_GENERATED_AT": "TIMESTAMP(6) WITH TIME ZONE",
}
REQUIRED_CONSTRAINTS = frozenset({
    "WH_CS_PK", "WH_CS_ID_CK", "WH_CS_REV_CK",
    "WH_CSO_PK", "WH_CSO_VIEW_CK", "WH_CSO_REV_CK",
    "WH_CP_PK", "WH_CP_METRIC_FK", "WH_CP_ENABLED_CK", "WH_CP_TTL_CK", "WH_CP_REV_CK",
    "WH_QC_PK", "WH_QC_USER_FK", "WH_QC_POLICY_FK", "WH_QC_SOURCE_FK",
    "WH_QC_KEY_CK", "WH_QC_DDL_CK", "WH_QC_REVISIONS_CK",
    "WH_QC_REQUEST_JSON_CK", "WH_QC_RESULT_JSON_CK", "WH_QC_ROWS_CK", "WH_QC_EXPIRY_CK",
    "WH_RAL_CACHE_STATUS_CK",
})


def load_schema_statements() -> list[str]:
    statements = split_sql_script(SCHEMA_SQL.read_text(encoding="utf-8"))
    created: set[str] = set()
    indexes: set[str] = set()
    for number, statement in enumerate(statements, 1):
        table = re.match(r"\ACREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(WH_[A-Z0-9_]+)\s*\(", statement, re.I | re.S)
        index = re.match(r"\ACREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+(WH_[A-Z0-9_]+)\s+ON\s+WH_QUERY_CACHE\s*\(", statement, re.I | re.S)
        comment = re.fullmatch(
            r"COMMENT\s+ON\s+(?:TABLE\s+WH_[A-Z0-9_]+|COLUMN\s+WH_[A-Z0-9_]+\.[A-Z0-9_]+)\s+IS\s+'(?:''|[^'])*'",
            statement, re.I | re.S,
        )
        if table:
            name = table.group(1).upper()
            if name not in TABLES or name in created:
                raise DeploymentError(f"Unexpected or duplicate cache table in statement {number}.")
            created.add(name)
        elif index:
            name = index.group(1).upper()
            if name not in INDEXES or name in indexes:
                raise DeploymentError(f"Unexpected or duplicate cache index in statement {number}.")
            indexes.add(name)
        elif not comment:
            raise DeploymentError(f"Cache schema statement {number} is not approved non-destructive DDL.")
    if created != TABLES or indexes != INDEXES:
        raise DeploymentError("Cache migration must contain all four tables and two indexes.")
    ddl = "\n".join(statement for statement in statements if statement.upper().startswith("CREATE TABLE"))
    if any(not re.search(rf"\bCONSTRAINT\s+{name}\b", ddl, re.I) for name in REQUIRED_CONSTRAINTS - {"WH_RAL_CACHE_STATUS_CK"}):
        raise DeploymentError("Cache schema is missing a required constraint.")
    return statements


def load_units() -> list[tuple[str, str]]:
    spec = PACKAGE_SPEC.read_text(encoding="utf-8").strip()
    body = PACKAGE_BODY.read_text(encoding="utf-8").strip()
    units = [("WH_CACHE_ADMIN package", spec), ("WH_CACHE_ADMIN package body", body)]
    for name, path in TRIGGER_FILES:
        units.append((name, path.read_text(encoding="utf-8").strip()))
    for name, source in units:
        if name == "WH_CACHE_ADMIN package":
            prefix = r"\ACREATE\s+OR\s+REPLACE\s+PACKAGE\s+WH_CACHE_ADMIN\s+AUTHID\s+DEFINER\s+AS\b"
        elif name == "WH_CACHE_ADMIN package body":
            prefix = r"\ACREATE\s+OR\s+REPLACE\s+PACKAGE\s+BODY\s+WH_CACHE_ADMIN\s+AS\b"
        else:
            prefix = rf"\ACREATE\s+OR\s+REPLACE\s+TRIGGER\s+{name}\b"
        if not re.match(prefix, source, re.I | re.S) or not source.endswith("END;") and not source.endswith("END WH_CACHE_ADMIN;"):
            raise DeploymentError(f"{name} has an unexpected declaration or ending.")
        if re.search(r"(?m)^\s*/\s*$", source):
            raise DeploymentError(f"{name} must omit SQLcl slash delimiters.")
    return units


def verify_prerequisites(connection) -> None:
    required = {
        "WH_REGISTERED_METRIC", "WH_REGISTERED_ANSWER_LOG",
        "WH_APP_USER", "WH_SEC_LOCATION", "WH_USER_LOCATION",
    }
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT TABLE_NAME FROM USER_TABLES WHERE TABLE_NAME IN "
            "('WH_REGISTERED_METRIC', 'WH_REGISTERED_ANSWER_LOG', "
            "'WH_APP_USER', 'WH_SEC_LOCATION', 'WH_USER_LOCATION')"
        )
        found = {str(row[0]) for row in cursor.fetchall()}
    if found != required:
        raise DeploymentError("Install the portable catalog and security schema before the cache.")


def migrate_audit_columns(connection) -> None:
    with connection.cursor() as cursor:
        for column, data_type in AUDIT_COLUMNS.items():
            cursor.execute(
                "SELECT COUNT(*) FROM USER_TAB_COLUMNS WHERE TABLE_NAME = 'WH_REGISTERED_ANSWER_LOG' "
                "AND COLUMN_NAME = :column_name",
                {"column_name": column},
            )
            if int(cursor.fetchone()[0]) == 0:
                cursor.execute(f"ALTER TABLE WH_REGISTERED_ANSWER_LOG ADD ({column} {data_type})")
        cursor.execute(
            "SELECT COUNT(*) FROM USER_CONSTRAINTS WHERE TABLE_NAME = 'WH_REGISTERED_ANSWER_LOG' "
            "AND CONSTRAINT_NAME = 'WH_RAL_CACHE_STATUS_CK'"
        )
        if int(cursor.fetchone()[0]) == 0:
            cursor.execute(
                "ALTER TABLE WH_REGISTERED_ANSWER_LOG ADD CONSTRAINT WH_RAL_CACHE_STATUS_CK "
                "CHECK (CACHE_STATUS IN ('MISS', 'HIT'))"
            )


def ensure_state_row(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM WH_CACHE_STATE WHERE STATE_ID = 1")
        if int(cursor.fetchone()[0]) == 0:
            cursor.execute("INSERT INTO WH_CACHE_STATE (STATE_ID, ACCESS_REVISION) VALUES (1, 0)")
            connection.commit()


def install_units(connection, units: list[tuple[str, str]]) -> None:
    with connection.cursor() as cursor:
        for name, source in units:
            try:
                cursor.execute(source)
            except Exception as exc:
                raise DeploymentError(f"{name} failed ({_oracle_code(exc)}).") from exc
    print("Applied cache administration package and access/revision triggers.")


def verify_install(connection) -> None:
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT TABLE_NAME FROM USER_TABLES WHERE TABLE_NAME IN "
                "('WH_CACHE_STATE', 'WH_CACHE_SOURCE', 'WH_CACHE_POLICY', 'WH_QUERY_CACHE')"
            )
            if {str(row[0]) for row in cursor.fetchall()} != TABLES:
                raise DeploymentError("Cache tables are missing.")
            cursor.execute(
                "SELECT COUNT(*) FROM WH_CACHE_STATE WHERE STATE_ID = 1 AND ACCESS_REVISION >= 0"
            )
            if int(cursor.fetchone()[0]) != 1:
                raise DeploymentError("Cache access revision row is missing.")
            cursor.execute(
                "SELECT COLUMN_NAME FROM USER_TAB_COLUMNS "
                "WHERE TABLE_NAME = 'WH_REGISTERED_ANSWER_LOG'"
            )
            if not set(AUDIT_COLUMNS).issubset({str(row[0]) for row in cursor.fetchall()}):
                raise DeploymentError("Cache audit columns are missing.")
            cursor.execute(
                "SELECT CONSTRAINT_NAME, STATUS, VALIDATED FROM USER_CONSTRAINTS "
                "WHERE TABLE_NAME IN ('WH_CACHE_STATE', 'WH_CACHE_SOURCE', 'WH_CACHE_POLICY', "
                "'WH_QUERY_CACHE', 'WH_REGISTERED_ANSWER_LOG')"
            )
            constraints = {
                str(name): (str(status), str(validated))
                for name, status, validated in cursor.fetchall()
            }
            if any(constraints.get(name) != ("ENABLED", "VALIDATED") for name in REQUIRED_CONSTRAINTS):
                raise DeploymentError("Cache constraints are missing or disabled.")
            cursor.execute(
                "SELECT INDEX_NAME, STATUS FROM USER_INDEXES WHERE TABLE_NAME = 'WH_QUERY_CACHE' "
                "AND INDEX_NAME IN ('WH_QC_EXPIRY_IX', 'WH_QC_USER_METRIC_IX')"
            )
            if {str(name): str(status) for name, status in cursor.fetchall()} != {name: "VALID" for name in INDEXES}:
                raise DeploymentError("Cache indexes are missing or invalid.")
            cursor.execute(
                "SELECT OBJECT_NAME, OBJECT_TYPE, STATUS FROM USER_OBJECTS "
                "WHERE OBJECT_NAME IN ('WH_CACHE_ADMIN', 'WH_CACHE_USER_REV_TRG', "
                "'WH_CACHE_LOCATION_REV_TRG', 'WH_CACHE_GRANT_REV_TRG', "
                "'WH_CACHE_POLICY_REV_TRG', 'WH_CACHE_SOURCE_REV_TRG')"
            )
            objects = {(str(name), str(kind)): str(status) for name, kind, status in cursor.fetchall()}
            expected = {
                ("WH_CACHE_ADMIN", "PACKAGE"), ("WH_CACHE_ADMIN", "PACKAGE BODY"),
                *((name, "TRIGGER") for name, _ in TRIGGER_FILES),
            }
            if any(objects.get(key) != "VALID" for key in expected):
                raise DeploymentError("Cache package or triggers did not compile as VALID.")
            cursor.execute(
                "SELECT TRIGGER_NAME, STATUS FROM USER_TRIGGERS WHERE TRIGGER_NAME IN "
                "('WH_CACHE_USER_REV_TRG', 'WH_CACHE_LOCATION_REV_TRG', "
                "'WH_CACHE_GRANT_REV_TRG', 'WH_CACHE_POLICY_REV_TRG', "
                "'WH_CACHE_SOURCE_REV_TRG')"
            )
            trigger_status = {str(name): str(status) for name, status in cursor.fetchall()}
            if trigger_status != {name: "ENABLED" for name, _ in TRIGGER_FILES}:
                raise DeploymentError("Cache revision triggers are missing or disabled.")
    except DeploymentError:
        raise
    except Exception as exc:
        raise DeploymentError(f"Cache verification failed ({_oracle_code(exc)}).") from exc
    print("Cache verified: tables, constraints, indexes, revision row, and VALID PL/SQL.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Validate fixed files without connecting")
    mode.add_argument("--apply", action="store_true", help="Install or refresh the non-destructive cache")
    mode.add_argument("--verify-only", action="store_true", help="Read-only verification of installed cache")
    args = parser.parse_args(argv)
    try:
        statements = load_schema_statements()
        units = load_units()
        if args.dry_run:
            print(f"Parsed optional cache: {len(statements)} non-destructive DDL statements and {len(units)} PL/SQL units.")
            return 0
        connection = _connect()
        try:
            verify_prerequisites(connection)
            if args.apply:
                migrate_audit_columns(connection)
                _execute_stage(connection, "result cache", statements)
                ensure_state_row(connection)
                install_units(connection, units)
            verify_install(connection)
        finally:
            connection.close()
        return 0
    except (DeploymentError, OSError) as exc:
        print(f"Result cache installation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
