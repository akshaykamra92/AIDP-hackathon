"""Safety checks for the rerunnable, credential-free security migration."""

from __future__ import annotations

import re
import unittest
from unittest.mock import patch

from scripts.install_security import (
    TABLES,
    install_package,
    load_package_sources,
    load_security_statements,
    main,
)


class _Cursor:
    def __init__(self):
        self.statements: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        return False

    def execute(self, statement: str):
        self.statements.append(statement)


class _Connection:
    def __init__(self):
        self.recorded_cursor = _Cursor()

    def cursor(self):
        return self.recorded_cursor


class SecurityInstallTests(unittest.TestCase):
    def test_migration_is_additive_and_creates_no_identity(self):
        statements = load_security_statements()
        created = {
            re.search(r"\bWH_(?:APP_USER|SEC_LOCATION|USER_LOCATION|ACCESS_AUDIT)\b", statement).group(0)
            for statement in statements
            if statement.startswith("CREATE TABLE")
        }
        self.assertEqual(created, TABLES)
        self.assertTrue(all(statement.startswith(("CREATE TABLE IF NOT EXISTS", "COMMENT ON"))
                            for statement in statements))
        self.assertFalse(any(re.search(r"\b(?:DROP|DELETE|TRUNCATE|INSERT|UPDATE|MERGE|GRANT)\b", statement)
                             for statement in statements))
        user_ddl = next(s for s in statements if s.startswith("CREATE TABLE IF NOT EXISTS WH_APP_USER"))
        self.assertIn("TOKEN_HASH", user_ddl)
        self.assertIn("WH_AU_TOKEN_UK UNIQUE (TOKEN_HASH)", user_ddl)
        self.assertIn("WH_AU_TOKEN_CK", user_ddl)

    def test_package_has_admin_guard_and_no_internal_commit(self):
        spec, body = load_package_sources()
        for action in (
            "BOOTSTRAP_ADMIN", "CREATE_USER", "ROTATE_TOKEN", "REVOKE_USER",
            "REGISTER_LOCATION", "SET_LOCATION_STATUS", "GRANT_LOCATION", "REVOKE_LOCATION",
        ):
            self.assertIn(f"PROCEDURE {action}", spec)
        self.assertIn("LOCK TABLE WH_APP_USER IN EXCLUSIVE MODE", body)
        self.assertIn("FUNCTION CHECK_ADMIN", body)
        self.assertIn("Cannot revoke the last active administrator", body)
        self.assertNotIn("p_admin_token_hash", body.lower())
        self.assertIsNone(re.search(r"\b(?:COMMIT|ROLLBACK)\b", body, re.IGNORECASE))

    def test_installer_executes_each_package_unit_whole(self):
        spec, body = load_package_sources()
        connection = _Connection()
        install_package(connection, spec, body)
        self.assertEqual(connection.recorded_cursor.statements, [spec, body])

    def test_dry_run_does_not_connect(self):
        with patch("scripts.install_security._connect", side_effect=AssertionError("unexpected connection")):
            self.assertEqual(main(["--dry-run"]), 0)


if __name__ == "__main__":
    unittest.main()
