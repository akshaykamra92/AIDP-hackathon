"""Safety gates for the non-destructive Oracle catalog installer."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from app.registered_metrics import RegisteredMetric
from scripts.install_catalog import (
    ROOT,
    install_package,
    load_catalog_statements,
    load_package_sources,
    main,
    migrate_existing_columns,
)


class _RecordingCursor:
    def __init__(self):
        self.statements: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def execute(self, statement: str):
        self.statements.append(statement)


class _RecordingConnection:
    def __init__(self):
        self.recorded_cursor = _RecordingCursor()

    def cursor(self):
        return self.recorded_cursor


class _MigrationCursor:
    def __init__(self):
        self.columns = {
            "WH_REGISTERED_METRIC": set(),
            "WH_REGISTERED_ANSWER_LOG": set(),
        }
        self.alters = []
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def execute(self, statement, binds=None):
        binds = binds or {}
        if "FROM USER_TABLES" in statement:
            self.result = (1,)
        elif "FROM USER_TAB_COLUMNS" in statement:
            self.result = (int(binds["column_name"] in self.columns[binds["table_name"]]),)
        elif statement.startswith("ALTER TABLE"):
            self.alters.append(statement)
            _, _, table, _, definition = statement.split(" ", 4)
            self.columns[table].add(definition.split(" ", 1)[0].lstrip("("))
        else:
            raise AssertionError(f"Unexpected statement: {statement}")

    def fetchone(self):
        return self.result


class _MigrationConnection:
    def __init__(self):
        self.recorded_cursor = _MigrationCursor()

    def cursor(self):
        return self.recorded_cursor


class PortableCatalogInstallTests(unittest.TestCase):
    def test_migration_is_rerunnable_without_reset_or_data_changes(self):
        statements = load_catalog_statements()
        created = [
            re.search(r"\bWH_REGISTERED_(?:METRIC|ANSWER_LOG)\b", statement, re.IGNORECASE).group(0)
            for statement in statements
            if statement.lstrip().upper().startswith("CREATE TABLE")
        ]
        self.assertCountEqual(created, ["WH_REGISTERED_METRIC", "WH_REGISTERED_ANSWER_LOG"])
        self.assertTrue(all(
            statement.lstrip().upper().startswith((
                "CREATE TABLE IF NOT EXISTS", "CREATE UNIQUE INDEX IF NOT EXISTS", "COMMENT ON",
            ))
            for statement in statements
        ))
        self.assertIn("SOURCE_DDL_AT   DATE", statements[0])
        self.assertIn("SOURCE_DDL_SHA256 VARCHAR2(64)", statements[0])
        self.assertIn("LOCATION_COLUMN VARCHAR2(128) NOT NULL", statements[0])
        self.assertIn("ASKED_BY       VARCHAR2(128)", statements[1])
        self.assertIn("STATUS IN ('DRAFT', 'REVIEWED', 'CERTIFIED', 'RETIRED')", statements[0])
        self.assertIn("FOREIGN KEY (METRIC_ID, METRIC_VERSION)", statements[1])
        self.assertEqual(sum("CREATE UNIQUE INDEX IF NOT EXISTS WH_RM_ONE_CERTIFIED_UX" in s for s in statements), 1)

    def test_pre_security_catalog_migration_adds_nullable_columns_once(self):
        connection = _MigrationConnection()
        migrate_existing_columns(connection)
        migrate_existing_columns(connection)
        self.assertEqual(connection.recorded_cursor.alters, [
            "ALTER TABLE WH_REGISTERED_METRIC ADD (LOCATION_COLUMN VARCHAR2(128))",
            "ALTER TABLE WH_REGISTERED_METRIC ADD (SOURCE_DDL_SHA256 VARCHAR2(64))",
            "ALTER TABLE WH_REGISTERED_ANSWER_LOG ADD (ASKED_BY VARCHAR2(128))",
        ])

    def test_governance_package_is_whole_and_has_no_internal_transaction(self):
        spec, body = load_package_sources()
        self.assertIn("PROCEDURE CREATE_DRAFT", spec)
        self.assertIn("PROCEDURE REVIEW_DRAFT", spec)
        self.assertIn("PROCEDURE CERTIFY", spec)
        self.assertIn("JSON_TABLE", body)
        self.assertIn("USER_OBJECTS", body)
        self.assertIn("USER_TAB_COLUMNS", body)
        self.assertIn("SOURCE_DDL_SHA256 = v_source_hash", body)
        self.assertIn("v_metric.SOURCE_DDL_SHA256 IS NULL", body)
        self.assertIsNone(re.search(r"\b(?:COMMIT|ROLLBACK)\b", body, re.IGNORECASE))
        self.assertEqual(body.rstrip().splitlines()[-1], "END WH_METRIC_GOVERNANCE;")

    def test_installer_sends_each_package_unit_as_one_statement(self):
        spec, body = load_package_sources()
        connection = _RecordingConnection()
        install_package(connection, spec, body)
        self.assertEqual(connection.recorded_cursor.statements, [spec, body])

    def test_dry_run_does_not_open_oracle_connection(self):
        with patch("scripts.install_catalog._connect", side_effect=AssertionError("unexpected connection")):
            self.assertEqual(main(["--dry-run"]), 0)

    def test_example_manifest_matches_the_count_adapter(self):
        example_dir = Path(ROOT) / "examples"
        manifest = json.loads((example_dir / "filled_requisitions.json").read_text(encoding="utf-8"))
        metric = RegisteredMetric.from_manifest(manifest, version=1)
        view_sql = (example_dir / "filled_requisitions.view.sql").read_text(encoding="utf-8")
        self.assertEqual(metric.aggregation, "COUNT")
        self.assertIsNone(metric.value_column)
        self.assertEqual(metric.location_column, "LOCATION_KEY")
        self.assertIn("CREATE OR REPLACE VIEW WH_EXAMPLE_FILLED_REQUISITIONS", view_sql)
        self.assertIn("r.FILL_DATE IS NOT NULL", view_sql)
        self.assertIn("SYS_CONTEXT('USERENV', 'CLIENT_IDENTIFIER')", view_sql)
        self.assertIn("acl.LOCATION_KEY = TRIM(TO_CHAR(r.LOCATION_ID))", view_sql)
        self.assertNotIn("version", manifest)
        self.assertNotIn("status", manifest)


if __name__ == "__main__":
    unittest.main()
