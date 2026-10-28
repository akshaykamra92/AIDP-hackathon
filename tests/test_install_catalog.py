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
    migrate_ratio_sum_contract,
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


class _RatioMigrationCursor:
    def __init__(self):
        self.aggregation_length = 8
        self.constraints = {"WH_RM_AGG_CK", "WH_RM_VALUE_CK"}
        self.alters = []
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def execute(self, statement):
        if "FROM USER_TABLES" in statement:
            self.result = (1,)
        elif "SELECT DATA_LENGTH FROM USER_TAB_COLUMNS" in statement:
            self.result = (self.aggregation_length,)
        elif "SELECT CONSTRAINT_NAME FROM USER_CONSTRAINTS" in statement:
            self.result = [(name,) for name in sorted(self.constraints)]
        elif "SELECT SEARCH_CONDITION_VC, STATUS, VALIDATED FROM USER_CONSTRAINTS" in statement:
            self.result = (
                "AGGREGATION = 'RATIO_SUM' AND NUMERATOR_COLUMN IS NOT NULL "
                "AND DENOMINATOR_COLUMN IS NOT NULL AND VALUE_COLUMN IS NULL",
                "ENABLED", "VALIDATED",
            )
        elif statement.startswith("ALTER TABLE WH_REGISTERED_METRIC MODIFY"):
            self.alters.append(statement)
            self.aggregation_length = 16
        elif statement.startswith("ALTER TABLE WH_REGISTERED_METRIC ADD CONSTRAINT WH_RM_CALC_CK"):
            self.alters.append(statement)
            self.constraints.add("WH_RM_CALC_CK")
        elif statement.startswith("ALTER TABLE WH_REGISTERED_METRIC DROP CONSTRAINT"):
            self.alters.append(statement)
            self.constraints.remove(statement.split()[-1])
        else:
            raise AssertionError(f"Unexpected migration SQL: {statement}")

    def fetchone(self):
        return self.result

    def fetchall(self):
        return self.result


class _RatioMigrationConnection:
    def __init__(self):
        self.recorded_cursor = _RatioMigrationCursor()

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
        self.assertIn("NUMERATOR_COLUMN VARCHAR2(128)", statements[0])
        self.assertIn("DENOMINATOR_COLUMN VARCHAR2(128)", statements[0])
        self.assertIn("AGGREGATION     VARCHAR2(16) NOT NULL", statements[0])
        self.assertIn("CONSTRAINT WH_RM_CALC_CK", statements[0])
        self.assertIn("AGGREGATION = 'RATIO_SUM'", statements[0])
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
            "ALTER TABLE WH_REGISTERED_METRIC ADD (NUMERATOR_COLUMN VARCHAR2(128))",
            "ALTER TABLE WH_REGISTERED_METRIC ADD (DENOMINATOR_COLUMN VARCHAR2(128))",
            "ALTER TABLE WH_REGISTERED_ANSWER_LOG ADD (ASKED_BY VARCHAR2(128))",
        ])

    def test_ratio_migration_widens_and_replaces_checks_before_dropping_old_ones(self):
        connection = _RatioMigrationConnection()
        migrate_ratio_sum_contract(connection)
        first_run = list(connection.recorded_cursor.alters)
        migrate_ratio_sum_contract(connection)
        self.assertEqual(connection.recorded_cursor.alters, first_run)
        self.assertEqual(connection.recorded_cursor.constraints, {"WH_RM_CALC_CK"})
        self.assertEqual(connection.recorded_cursor.aggregation_length, 16)
        self.assertTrue(first_run[0].startswith("ALTER TABLE WH_REGISTERED_METRIC MODIFY"))
        self.assertIn("ADD CONSTRAINT WH_RM_CALC_CK", first_run[1])
        self.assertIn("ENABLE VALIDATE", first_run[1])
        self.assertEqual(first_run[2:], [
            "ALTER TABLE WH_REGISTERED_METRIC DROP CONSTRAINT WH_RM_AGG_CK",
            "ALTER TABLE WH_REGISTERED_METRIC DROP CONSTRAINT WH_RM_VALUE_CK",
        ])
        self.assertTrue(all(statement.startswith("ALTER TABLE WH_REGISTERED_METRIC") for statement in first_run))

    def test_governance_package_is_whole_and_has_no_internal_transaction(self):
        spec, body = load_package_sources()
        self.assertIn("PROCEDURE CREATE_DRAFT", spec)
        self.assertIn("PROCEDURE REVIEW_DRAFT", spec)
        self.assertIn("PROCEDURE CERTIFY", spec)
        self.assertIn("JSON_TABLE", body)
        self.assertIn("USER_OBJECTS", body)
        self.assertIn("USER_TAB_COLUMNS", body)
        self.assertIn("SOURCE_DDL_SHA256 = v_source_hash", body)
        self.assertIn("p_numerator_column IN VARCHAR2", spec)
        self.assertIn("p_denominator_column IN VARCHAR2", spec)
        self.assertIn("v_aggregation NOT IN ('COUNT', 'SUM', 'AVG', 'RATIO_SUM')", body)
        self.assertIn("Numerator column must be numeric", body)
        self.assertIn("Denominator column must be numeric", body)
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

    def test_ratio_example_has_a_reviewable_multi_table_grain(self):
        example_dir = Path(ROOT) / "examples"
        manifest = json.loads((example_dir / "on_time_fulfillment.json").read_text(encoding="utf-8"))
        metric = RegisteredMetric.from_manifest(manifest, version=1)
        view_sql = (example_dir / "on_time_fulfillment.view.sql").read_text(encoding="utf-8")
        sources_sql = (example_dir / "on_time_fulfillment.sources.sql").read_text(encoding="utf-8")
        self.assertEqual(metric.aggregation, "RATIO_SUM")
        self.assertEqual(metric.numerator_column, "ON_TIME_NUM")
        self.assertEqual(metric.denominator_column, "ELIGIBLE_DEN")
        self.assertIn("WITH line_totals AS", view_sql)
        self.assertIn("GROUP BY ORDER_ID", view_sql)
        self.assertIn("JOIN WH_EXAMPLE_ORDER", view_sql)
        self.assertIn("JOIN WH_EXAMPLE_SITE", view_sql)
        self.assertIn("s.SITE_CODE_AT_FULFILLMENT AS LOCATION_KEY", view_sql)
        self.assertIn("SYS_CONTEXT('USERENV', 'CLIENT_IDENTIFIER')", view_sql)
        self.assertEqual(sources_sql.count("CREATE TABLE IF NOT EXISTS WH_EXAMPLE_"), 4)


if __name__ == "__main__":
    unittest.main()
