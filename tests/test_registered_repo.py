"""Guard the database boundary for registered metrics without Oracle access."""

from __future__ import annotations

import hashlib
import unittest
from datetime import date, datetime

from app.registered_repo import CatalogError, RegisteredMetricRepository


class FakeDatabase:
    def __init__(self):
        self.ddl_at = datetime(2026, 10, 1, 12, 0, 0)
        self.view_ddl = "CREATE OR REPLACE VIEW WH_FILLED_REQ_VIEW AS SELECT * FROM WH_SYNTHETIC_SOURCE"
        self.view_exists = True
        self.query_calls = 0
        self.date_type = "DATE"
        self.location_type = "VARCHAR2"
        self.location_column = "LOCATION_KEY"
        self.last_answer_sql = None
        self.last_answer_binds = None
        self.last_client_identifier = None
        self.answer_rows = [{"value": 7}]

    def run_readonly(self, sql, binds, *, client_identifier=None):
        if "FROM USER_TABLES" in sql:
            return [{"object_count": 2}]
        if "FROM WH_REGISTERED_METRIC" in sql:
            if "LOCATION_COLUMN IS NOT NULL" in sql and self.location_column is None:
                return []
            return [{
                "metric_id": "FILLED_REQUISITIONS",
                "version": 1,
                "name": "Filled requisitions",
                "owner": "People Analytics",
                "definition_text": "Count of requisitions filled in the selected period.",
                "unit": "requisitions",
                "source_view": "WH_FILLED_REQ_VIEW",
                "date_column": "METRIC_DATE",
                "location_column": self.location_column,
                "value_column": None,
                "aggregation": "COUNT",
                "allowed_dims": '["STATE"]',
                "allowed_filters": '["STATE"]',
                "status": "CERTIFIED",
                "source_ddl_at": datetime(2026, 10, 1, 12, 0, 0),
                "source_ddl_sha256": hashlib.sha256(self.view_ddl.encode("utf-8")).hexdigest(),
            }]
        if "FROM USER_VIEWS" in sql:
            return [{"view_name": "WH_FILLED_REQ_VIEW"}] if self.view_exists else []
        if "FROM USER_OBJECTS" in sql:
            return [{"last_ddl_time": self.ddl_at, "status": "VALID"}]
        if "FROM USER_TAB_COLUMNS" in sql:
            return [
                {"column_name": "METRIC_DATE", "data_type": self.date_type},
                {"column_name": "STATE", "data_type": "VARCHAR2"},
                {"column_name": "LOCATION_KEY", "data_type": self.location_type},
            ]
        if "DBMS_METADATA.GET_DDL" in sql:
            self.last_ddl_binds = binds
            return [{"source_ddl": self.view_ddl}]
        if "FROM WH_FILLED_REQ_VIEW" in sql:
            self.query_calls += 1
            self.last_answer_sql = sql
            self.last_answer_binds = binds
            self.last_client_identifier = client_identifier
            return self.answer_rows
        raise AssertionError(f"Unexpected query: {sql}")


class RegisteredRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.database = FakeDatabase()
        self.repo = RegisteredMetricRepository(self.database)
        self.logged = []
        self.repo.log_answer = lambda **data: self.logged.append(data)

    def test_certified_answer_uses_view_and_writes_audit(self):
        answer = self.repo.query(
            "filled_requisitions", date(2026, 7, 1), date(2026, 10, 1),
            filters={"state": "FL"}, user_id="reader-1",
        )
        self.assertEqual(answer["rows"], [{"value": 7}])
        self.assertEqual(answer["metric"]["version"], 1)
        self.assertIn("FROM WH_FILLED_REQ_VIEW", answer["sql"])
        self.assertEqual(answer["binds"]["f0"], "FL")
        self.assertEqual(answer["binds"]["auth_user_id"], "reader-1")
        self.assertEqual(self.database.query_calls, 1)
        self.assertEqual(self.database.last_client_identifier, "reader-1")
        self.assertIn("acl.LOCATION_KEY = src.LOCATION_KEY", self.database.last_answer_sql)
        self.assertEqual(answer["access"], {"user_id": "reader-1", "location_scoped": True})
        self.assertEqual(self.logged[0]["asked_by"], "reader-1")
        self.assertEqual(len(self.logged), 1)

    def test_query_without_identity_is_denied_before_database_access(self):
        with self.assertRaises(CatalogError) as raised:
            self.repo.query("FILLED_REQUISITIONS", date(2026, 7, 1), date(2026, 10, 1))
        self.assertEqual("AUTH_REQUIRED", raised.exception.code)
        self.assertEqual(self.database.query_calls, 0)
        self.assertEqual(self.logged, [])

    def test_old_certified_metric_is_hidden_and_cannot_run_scoped(self):
        self.database.location_column = None
        self.assertEqual([], self.repo.list_certified(scoped_only=True))
        with self.assertRaises(CatalogError) as raised:
            self.repo.query(
                "FILLED_REQUISITIONS", date(2026, 7, 1), date(2026, 10, 1),
                user_id="reader-1",
            )
        self.assertEqual("LOCATION_SCOPE_REQUIRED", raised.exception.code)
        self.assertEqual(self.database.query_calls, 0)
        self.assertEqual(self.logged, [])

    def test_view_change_refuses_before_query_or_audit(self):
        self.database.ddl_at = datetime(2026, 10, 2, 12, 0, 0)
        with self.assertRaises(CatalogError) as raised:
            self.repo.query("FILLED_REQUISITIONS", date(2026, 7, 1), date(2026, 10, 1), user_id="reader-1")
        self.assertEqual(raised.exception.code, "SOURCE_CHANGED")
        self.assertEqual(self.database.query_calls, 0)
        self.assertEqual(self.logged, [])

    def test_invalid_or_missing_source_is_rejected(self):
        self.database.view_exists = False
        with self.assertRaises(CatalogError) as raised:
            self.repo.query("FILLED_REQUISITIONS", date(2026, 7, 1), date(2026, 10, 1), user_id="reader-1")
        self.assertEqual(raised.exception.code, "SOURCE_VIEW_MISSING")

        self.database.view_exists = True
        self.database.date_type = "VARCHAR2"
        with self.assertRaises(CatalogError) as raised:
            self.repo.query("FILLED_REQUISITIONS", date(2026, 7, 1), date(2026, 10, 1), user_id="reader-1")
        self.assertEqual(raised.exception.code, "INVALID_DATE_COLUMN")

        self.database.date_type = "DATE"
        self.database.location_type = "NUMBER"
        with self.assertRaises(CatalogError) as raised:
            self.repo.query("FILLED_REQUISITIONS", date(2026, 7, 1), date(2026, 10, 1), user_id="reader-1")
        self.assertEqual(raised.exception.code, "INVALID_LOCATION_COLUMN")

    def test_oversize_grouped_answer_is_refused_without_audit(self):
        self.database.answer_rows = [
            {"state": f"S{i}", "value": 1} for i in range(501)
        ]
        with self.assertRaises(CatalogError) as raised:
            self.repo.query(
                "FILLED_REQUISITIONS", date(2026, 7, 1), date(2026, 10, 1),
                dimensions=["STATE"], user_id="reader-1",
            )
        self.assertEqual(raised.exception.code, "RESULT_TOO_LARGE")
        self.assertIn("FETCH FIRST 501 ROWS ONLY", self.database.last_answer_sql)
        self.assertEqual(self.logged, [])


if __name__ == "__main__":
    unittest.main()
