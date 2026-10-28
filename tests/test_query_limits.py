"""Boundary checks for portable query cost and security readiness."""

from __future__ import annotations

import unittest
from datetime import date

from app.metrics import CompileError
from app.registered_metrics import RegisteredMetric, compile_registered
from app.security import SecurityRepository


def _metric() -> RegisteredMetric:
    return RegisteredMetric.from_manifest(
        {
            "metric_id": "SYNTHETIC_FACTS",
            "name": "Synthetic facts",
            "owner": "Test Analytics",
            "definition_text": "Count of synthetic facts in completed months.",
            "unit": "facts",
            "source_view": "WH_SYNTHETIC_FACTS",
            "date_column": "METRIC_DATE",
            "location_column": "LOCATION_KEY",
            "aggregation": "COUNT",
            "allowed_dims": [],
            "allowed_filters": ["CATEGORY"],
        },
        version=1,
        status="CERTIFIED",
    )


class _SecurityDatabase:
    def __init__(self, *, tables: int = 4, package: str = "VALID", body: str = "VALID"):
        self.tables = tables
        self.package = package
        self.body = body
        self.calls: list[str] = []

    def run_readonly(self, sql, _binds):
        self.calls.append(sql)
        if "FROM USER_TABLES" in sql:
            return [{"object_count": self.tables}]
        if "FROM USER_OBJECTS" in sql:
            return [
                {"object_type": "PACKAGE", "status": self.package},
                {"object_type": "PACKAGE BODY", "status": self.body},
            ]
        raise AssertionError(f"Unexpected SQL: {sql}")


class QueryLimitTests(unittest.TestCase):
    def test_source_digest_is_validated_and_not_published(self):
        row = {
            "metric_id": "SYNTHETIC_FACTS",
            "version": 1,
            "name": "Synthetic facts",
            "owner": "Test Analytics",
            "definition_text": "Count of synthetic facts.",
            "unit": "facts",
            "source_view": "WH_SYNTHETIC_FACTS",
            "date_column": "METRIC_DATE",
            "location_column": "LOCATION_KEY",
            "aggregation": "COUNT",
            "allowed_dims": "[]",
            "allowed_filters": "[]",
            "status": "CERTIFIED",
            "source_ddl_sha256": "A" * 64,
        }
        metric = RegisteredMetric.from_row(row)
        self.assertEqual(metric.source_ddl_sha256, "a" * 64)
        self.assertNotIn("source_ddl_sha256", metric.public_dict())
        row["source_ddl_sha256"] = "not-a-sha256-value"
        with self.assertRaises(CompileError) as raised:
            RegisteredMetric.from_row(row)
        self.assertEqual(raised.exception.code, "INVALID_SOURCE_DIGEST")

    def test_36_month_window_is_allowed_but_37_months_is_rejected(self):
        metric = _metric()
        accepted = compile_registered(
            metric, date(2023, 1, 1), date(2026, 1, 1), (), {},
            scope_user_id="reader-1",
        )
        self.assertEqual(accepted.binds["start_date"], date(2023, 1, 1))
        self.assertEqual(accepted.binds["end_date"], date(2026, 1, 1))

        with self.assertRaises(CompileError) as raised:
            compile_registered(
                metric, date(2023, 1, 1), date(2026, 2, 1), (), {},
                scope_user_id="reader-1",
            )
        self.assertEqual(raised.exception.code, "PERIOD_TOO_LONG")

    def test_text_filter_boundary_is_checked_before_sql_execution(self):
        metric = _metric()
        accepted = compile_registered(
            metric, date(2026, 1, 1), date(2026, 2, 1), (),
            {"CATEGORY": "A" * 256}, scope_user_id="reader-1",
        )
        self.assertEqual(accepted.binds["f0"], "A" * 256)

        with self.assertRaises(CompileError) as raised:
            compile_registered(
                metric, date(2026, 1, 1), date(2026, 2, 1), (),
                {"CATEGORY": "A" * 257}, scope_user_id="reader-1",
            )
        self.assertEqual(raised.exception.code, "FILTER_TOO_LONG")

    def test_security_readiness_requires_both_valid_package_units(self):
        self.assertTrue(SecurityRepository(_SecurityDatabase()).security_ready())
        for database in (
            _SecurityDatabase(tables=3),
            _SecurityDatabase(package="INVALID"),
            _SecurityDatabase(body="INVALID"),
        ):
            with self.subTest(tables=database.tables, package=database.package, body=database.body):
                self.assertFalse(SecurityRepository(database).security_ready())


if __name__ == "__main__":
    unittest.main()
