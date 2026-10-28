"""Application tokens and location grants stay at the Oracle security boundary."""

from __future__ import annotations

import hashlib
import unittest
from unittest.mock import patch

from app.db import DatabaseUnavailable, OracleRepository
from app.security import Principal, SecurityRepository, generate_token, token_hash


class _SecurityDatabase:
    def __init__(self):
        self.calls = []
        self.active_digest = token_hash("A" * 43)
        self.package_body_status = "VALID"

    def run_readonly(self, sql, binds):
        self.calls.append((sql, binds))
        if "FROM USER_TABLES" in sql:
            return [{"object_count": 4}]
        if "FROM USER_OBJECTS" in sql:
            return [
                {"object_type": "PACKAGE", "status": "VALID"},
                {"object_type": "PACKAGE BODY", "status": self.package_body_status},
            ]
        if "FROM WH_APP_USER" in sql:
            if binds["token_hash"] == self.active_digest:
                return [{"user_id": "reader-1", "display_name": "Reader", "role": "READER"}]
            return []
        if "FROM WH_USER_LOCATION" in sql:
            return [{"location_key": "101", "display_name": "Site 101"}]
        raise AssertionError(f"Unexpected SQL: {sql}")


class _SessionConnection:
    def __init__(self, fail_select=False):
        self.statements = []
        self.description = [("VALUE",)]
        self.fail_select = fail_select
        self.rollbacks = 0
        self.closes = 0

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def execute(self, sql, binds=None):
        self.statements.append((sql, binds or {}))
        if self.fail_select and sql.startswith("SELECT"):
            raise RuntimeError("synthetic read failure")

    def fetchall(self):
        return [(1,)]

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


class SecurityTests(unittest.TestCase):
    def test_new_token_has_256_random_bits_and_only_digest_is_persistable(self):
        token = generate_token()
        self.assertEqual(len(token), 43)
        self.assertRegex(token, r"^[A-Za-z0-9_-]{43}$")
        self.assertEqual(token_hash(token), hashlib.sha256(token.encode("ascii")).hexdigest())
        self.assertEqual(len(token_hash(token)), 64)
        with patch("app.security.secrets.token_urlsafe", return_value="A" * 43) as random_source:
            self.assertEqual(generate_token(), "A" * 43)
        random_source.assert_called_once_with(32)

    def test_invalid_or_revoked_token_cannot_authenticate(self):
        database = _SecurityDatabase()
        security = SecurityRepository(database)
        self.assertTrue(security.security_ready())
        self.assertIsNone(security.authenticate_token("invalid token"))
        self.assertEqual(len(database.calls), 2)  # Invalid shape never reaches Oracle.
        self.assertIsNone(security.authenticate_token("B" * 43))
        self.assertEqual(security.authenticate_token("A" * 43), Principal("reader-1", "Reader", "READER"))
        for sql, binds in database.calls[2:]:
            self.assertIn("STATUS = 'ACTIVE'", sql)
            self.assertNotIn("A" * 43, sql)
            self.assertNotIn("A" * 43, str(binds))
            self.assertEqual(len(binds["token_hash"]), 64)

    def test_readiness_requires_valid_security_package_body(self):
        database = _SecurityDatabase()
        database.package_body_status = "INVALID"
        self.assertFalse(SecurityRepository(database).security_ready())

    def test_location_lookup_rechecks_user_grant_and_location_status(self):
        database = _SecurityDatabase()
        security = SecurityRepository(database)
        self.assertEqual(security.active_locations("reader-1"), [
            {"location_key": "101", "display_name": "Site 101"},
        ])
        sql, binds = database.calls[-1]
        self.assertEqual(binds, {"auth_user_id": "reader-1"})
        self.assertIn("usr.STATUS = 'ACTIVE'", sql)
        self.assertIn("acl.STATUS = 'ACTIVE'", sql)
        self.assertIn("loc.STATUS = 'ACTIVE'", sql)
        self.assertIn("acl.USER_ID = :auth_user_id", sql)

    def test_pooled_oracle_read_sets_and_clears_verified_session_identity(self):
        connection = _SessionConnection()
        database = OracleRepository(user="synthetic", password="synthetic", dsn="synthetic")
        database._connect = lambda: connection
        self.assertEqual(database.run_readonly(
            "SELECT 1 AS VALUE FROM WH_SYNTHETIC_VIEW", {}, client_identifier="reader-1",
        ), [{"value": 1}])
        self.assertEqual(connection.statements[0][0], "SET TRANSACTION READ ONLY")
        self.assertEqual(connection.statements[1], (
            "BEGIN DBMS_SESSION.SET_IDENTIFIER(:auth_user_id); END;",
            {"auth_user_id": "reader-1"},
        ))
        self.assertEqual(connection.statements[2][0], "SELECT 1 AS VALUE FROM WH_SYNTHETIC_VIEW")
        self.assertEqual(connection.statements[3][0], "BEGIN DBMS_SESSION.CLEAR_IDENTIFIER; END;")
        self.assertEqual(connection.closes, 1)

        # A later acquisition of the same pooled session starts with no identity.
        database.run_readonly("SELECT 1 AS VALUE FROM WH_SYNTHETIC_VIEW", {})
        self.assertEqual(connection.statements[5][0], "BEGIN DBMS_SESSION.CLEAR_IDENTIFIER; END;")

    def test_pooled_oracle_read_clears_identity_after_query_failure(self):
        connection = _SessionConnection(fail_select=True)
        database = OracleRepository(user="synthetic", password="synthetic", dsn="synthetic")
        database._connect = lambda: connection
        with self.assertRaises(DatabaseUnavailable):
            database.run_readonly(
                "SELECT 1 AS VALUE FROM WH_SYNTHETIC_VIEW", {}, client_identifier="reader-1",
            )
        self.assertEqual(connection.statements[-1][0], "BEGIN DBMS_SESSION.CLEAR_IDENTIFIER; END;")
        self.assertGreaterEqual(connection.rollbacks, 1)
        self.assertEqual(connection.closes, 1)


if __name__ == "__main__":
    unittest.main()
