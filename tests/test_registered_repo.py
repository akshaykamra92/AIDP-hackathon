"""Guard the database boundary for registered metrics without Oracle access."""

from __future__ import annotations

import unittest
from datetime import date, datetime

from app.registered_repo import CatalogError, RegisteredMetricRepository


class FakeDatabase:
    def __init__(self):
        self.ddl_at = datetime(2026, 10, 1, 12, 0, 0)
        self.view_exists = True
        self.query_calls = 0
        self.date_type = "DATE"

    def run_readonly(self, sql, binds):
        if "FROM USER_TABLES" in sql:
            return [{"object_count": 2}]
        if "FROM WH_REGISTERED_METRIC" in sql:
            return [{
                "metric_id": "FILLED_REQUISITIONS",
                "version": 1,
                "name": "Filled requisitions",
                "owner": "People Analytics",
                "definition_text": "Count of requisitions filled in the selected period.",
                "unit": "requisitions",
                "source_view": "WH_FILLED_REQ_VIEW",
                "date_column": "METRIC_DATE",
                "value_column": None,
                "aggregation": "COUNT",
                "allowed_dims": '["STATE"]',
                "allowed_filters": '["STATE"]',
                "status": "CERTIFIED",
                "source_ddl_at": datetime(2026, 10, 1, 12, 0, 0),
            }]
        if "FROM USER_VIEWS" in sql:
            return [{"view_name": "WH_FILLED_REQ_VIEW"}] if self.view_exists else []
        if "FROM USER_OBJECTS" in sql:
            return [{"last_ddl_time": self.ddl_at, "status": "VALID"}]
        if "FROM USER_TAB_COLUMNS" in sql:
            return [
                {"column_name": "METRIC_DATE", "data_type": self.date_type},
                {"column_name": "STATE", "data_type": "VARCHAR2"},
            ]
        if "FROM WH_FILLED_REQ_VIEW" in sql:
            self.query_calls += 1
            return [{"value": 7}]
        raise AssertionError(f"Unexpected query: {sql}")


class RegisteredRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.database = FakeDatabase()
        self.repo = RegisteredMetricRepository(self.database)
        self.logged = []
        self.repo.log_answer = lambda **data: self.logged.append(data)

    def test_certified_answer_uses_view_and_writes_audit(self):
        answer = self.repo.query(
            "filled_requisitions", date(2026, 7, 1), date(2026, 10, 1), filters={"state": "FL"}
        )
        self.assertEqual(answer["rows"], [{"value": 7}])
        self.assertEqual(answer["metric"]["version"], 1)
        self.assertIn("FROM WH_FILLED_REQ_VIEW", answer["sql"])
        self.assertEqual(answer["binds"]["f0"], "FL")
        self.assertEqual(self.database.query_calls, 1)
        self.assertEqual(len(self.logged), 1)

    def test_view_change_refuses_before_query_or_audit(self):
        self.database.ddl_at = datetime(2026, 10, 2, 12, 0, 0)
        with self.assertRaises(CatalogError) as raised:
            self.repo.query("FILLED_REQUISITIONS", date(2026, 7, 1), date(2026, 10, 1))
        self.assertEqual(raised.exception.code, "SOURCE_CHANGED")
        self.assertEqual(self.database.query_calls, 0)
        self.assertEqual(self.logged, [])

    def test_invalid_or_missing_source_is_rejected(self):
        self.database.view_exists = False
        with self.assertRaises(CatalogError) as raised:
            self.repo.query("FILLED_REQUISITIONS", date(2026, 7, 1), date(2026, 10, 1))
        self.assertEqual(raised.exception.code, "SOURCE_VIEW_MISSING")

        self.database.view_exists = True
        self.database.date_type = "VARCHAR2"
        with self.assertRaises(CatalogError) as raised:
            self.repo.query("FILLED_REQUISITIONS", date(2026, 7, 1), date(2026, 10, 1))
        self.assertEqual(raised.exception.code, "INVALID_DATE_COLUMN")


if __name__ == "__main__":
    unittest.main()
