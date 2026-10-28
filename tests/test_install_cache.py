"""Installer gates for the optional, non-destructive Oracle result cache."""

from __future__ import annotations

import re
import unittest
from unittest.mock import patch

from scripts.install_cache import (
    AUDIT_COLUMNS, INDEXES, TABLES, TRIGGER_FILES, load_schema_statements,
    load_units, main, migrate_audit_columns,
)


class MigrationCursor:
    def __init__(self):
        self.columns = set()
        self.constraints = set()
        self.alters = []
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _trace):
        return False

    def execute(self, statement, binds=None):
        if "FROM USER_TAB_COLUMNS" in statement:
            self.result = (int(binds["column_name"] in self.columns),)
        elif "FROM USER_CONSTRAINTS" in statement:
            self.result = (int("WH_RAL_CACHE_STATUS_CK" in self.constraints),)
        elif statement.startswith("ALTER TABLE"):
            self.alters.append(statement)
            if "ADD CONSTRAINT" in statement:
                self.constraints.add("WH_RAL_CACHE_STATUS_CK")
            else:
                match = re.search(r"ADD \(([A-Z_]+) ", statement)
                self.columns.add(match.group(1))
        else:
            raise AssertionError(f"Unexpected installer SQL: {statement}")

    def fetchone(self):
        return self.result


class MigrationConnection:
    def __init__(self):
        self.recorded = MigrationCursor()

    def cursor(self):
        return self.recorded


class CacheInstallerTests(unittest.TestCase):
    def test_schema_contains_only_idempotent_wh_objects(self):
        statements = load_schema_statements()
        ddl = "\n".join(statements)
        self.assertEqual(len([s for s in statements if s.startswith("CREATE TABLE")]), len(TABLES))
        self.assertEqual(len([s for s in statements if s.startswith("CREATE INDEX")]), len(INDEXES))
        self.assertTrue(all(s.startswith((
            "CREATE TABLE IF NOT EXISTS", "CREATE INDEX IF NOT EXISTS", "COMMENT ON",
        )) for s in statements))
        for name in (*TABLES, *INDEXES):
            self.assertIn(name, ddl)
        self.assertIn("ENABLED           CHAR(1) DEFAULT 'N' NOT NULL", ddl)
        self.assertIn("TTL_SECONDS       NUMBER(6) DEFAULT 1800 NOT NULL", ddl)
        self.assertIn("CHECK (TTL_SECONDS BETWEEN 1 AND 86400)", ddl)
        self.assertIn("CONSTRAINT WH_QC_REQUEST_JSON_CK CHECK (CANONICAL_JSON IS JSON)", ddl)
        self.assertNotIn("DROP TABLE", ddl)
        self.assertNotIn("ORACLE_PASSWORD", ddl)

    def test_audit_migration_is_rerunnable(self):
        connection = MigrationConnection()
        migrate_audit_columns(connection)
        migrate_audit_columns(connection)
        self.assertEqual(len(connection.recorded.alters), len(AUDIT_COLUMNS) + 1)
        self.assertTrue(all(s.startswith("ALTER TABLE WH_REGISTERED_ANSWER_LOG ADD") for s in connection.recorded.alters))

    def test_admin_package_and_revision_triggers_are_complete(self):
        units = load_units()
        self.assertEqual(len(units), 2 + len(TRIGGER_FILES))
        spec = units[0][1]
        body = units[1][1]
        self.assertIn("PROCEDURE SET_POLICY", spec)
        self.assertIn("PROCEDURE BUMP_SOURCE_REVISION", spec)
        self.assertIn("PROCEDURE PURGE_EXPIRED", spec)
        self.assertIn("STATUS = 'CERTIFIED'", body)
        self.assertIn("LOCATION_COLUMN IS NOT NULL", body)
        self.assertIsNone(re.search(r"\b(?:COMMIT|ROLLBACK)\b", body, re.I))
        trigger_text = "\n".join(source for _, source in units[2:])
        for table in ("WH_APP_USER", "WH_SEC_LOCATION", "WH_USER_LOCATION"):
            self.assertIn(f"ON {table}", trigger_text)
        self.assertIn("ACCESS_REVISION = ACCESS_REVISION + 1", trigger_text)
        self.assertIn(":NEW.POLICY_REVISION := :OLD.POLICY_REVISION + 1", trigger_text)
        self.assertIn(":NEW.DATA_REVISION := :OLD.DATA_REVISION + 1", trigger_text)

    def test_dry_run_uses_no_database_connection(self):
        with patch("scripts.install_cache._connect", side_effect=AssertionError("Unexpected connection")):
            self.assertEqual(main(["--dry-run"]), 0)


if __name__ == "__main__":
    unittest.main()
