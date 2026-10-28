"""Source-contract checks and governance argument order for a governed ratio."""

from __future__ import annotations

import unittest
from datetime import date

from app.registered_metrics import RegisteredMetric
from app.registered_repo import CatalogError, RegisteredMetricRepository
from tests.test_registered_repo import FakeDatabase


class RatioDatabase(FakeDatabase):
    def __init__(self):
        super().__init__()
        self.numerator_type = "NUMBER"
        self.denominator_type = "NUMBER"
        self.omit_denominator = False
        self.answer_rows = None

    def run_readonly(self, sql, binds, *, client_identifier=None):
        rows = super().run_readonly(sql, binds, client_identifier=client_identifier)
        if "FROM WH_REGISTERED_METRIC" in sql:
            rows[0].update({
                "aggregation": "RATIO_SUM",
                "numerator_column": "NUMERATOR_VALUE",
                "denominator_column": "DENOMINATOR_VALUE",
            })
        if "FROM USER_TAB_COLUMNS" in sql:
            rows.append({"column_name": "NUMERATOR_VALUE", "data_type": self.numerator_type})
            if not self.omit_denominator:
                rows.append({"column_name": "DENOMINATOR_VALUE", "data_type": self.denominator_type})
        if "FROM WH_FILLED_REQ_VIEW src" in sql and self.answer_rows is not None:
            return self.answer_rows
        return rows


def ratio_manifest():
    return {
        "metric_id": "WH_TEST_RATIO",
        "name": "Synthetic joined ratio",
        "owner": "Synthetic Analytics",
        "definition_text": "Sum numerator divided by sum denominator over the approved fact grain.",
        "unit": "ratio",
        "source_view": "WH_FILLED_REQ_VIEW",
        "date_column": "METRIC_DATE",
        "location_column": "LOCATION_KEY",
        "value_column": None,
        "numerator_column": "NUMERATOR_VALUE",
        "denominator_column": "DENOMINATOR_VALUE",
        "aggregation": "RATIO_SUM",
        "allowed_dims": ["STATE"],
        "allowed_filters": ["STATE"],
    }


class RatioRepositoryTests(unittest.TestCase):
    def test_invalid_group_rejects_the_whole_answer(self):
        database = RatioDatabase()
        database.answer_rows = [
            {"state": "FL", "value": 0.5},
            {"state": "AL", "value": None},
        ]
        repo = RegisteredMetricRepository(database)
        audited = []
        repo.log_answer = lambda **data: audited.append(data)

        with self.assertRaises(CatalogError) as raised:
            repo.query(
                "FILLED_REQUISITIONS", date(2026, 7, 1), date(2026, 10, 1),
                dimensions=["STATE"], user_id="reader-1",
            )
        self.assertEqual(raised.exception.code, "NO_DATA")
        self.assertEqual(database.query_calls, 1)
        self.assertEqual(audited, [])

    def test_ratio_components_must_both_exist_and_be_numeric(self):
        database = RatioDatabase()
        repo = RegisteredMetricRepository(database)
        metric = repo.get_certified("FILLED_REQUISITIONS")
        self.assertIsNotNone(metric)
        self.assertEqual(len(repo.validate_source(metric)[1]), 64)

        database.numerator_type = "VARCHAR2"
        with self.assertRaises(CatalogError) as raised:
            repo.validate_source(metric)
        self.assertEqual(raised.exception.code, "INVALID_NUMERATOR_COLUMN")
        database.numerator_type = "NUMBER"
        database.denominator_type = "VARCHAR2"
        with self.assertRaises(CatalogError) as raised:
            repo.validate_source(metric)
        self.assertEqual(raised.exception.code, "INVALID_DENOMINATOR_COLUMN")
        database.denominator_type = "NUMBER"
        database.omit_denominator = True
        with self.assertRaises(CatalogError) as raised:
            repo.validate_source(metric)
        self.assertEqual(raised.exception.code, "SOURCE_COLUMN_MISSING")

    def test_governance_receives_ratio_components_in_package_order(self):
        repo = RegisteredMetricRepository(FakeDatabase())
        captured = []

        def call(procedure, arguments, out_version=False):
            captured.append((procedure, arguments, out_version))
            return 2

        repo._call_governance = call
        repo.get_version = lambda _metric_id, version: RegisteredMetric.from_manifest(ratio_manifest(), version)
        saved = repo.create_draft(ratio_manifest())
        self.assertEqual(saved.version, 2)
        self.assertEqual(captured[0][0], "CREATE_DRAFT")
        self.assertTrue(captured[0][2])
        self.assertEqual(captured[0][1][8:12], [
            None, "NUMERATOR_VALUE", "DENOMINATOR_VALUE", "RATIO_SUM",
        ])


if __name__ == "__main__":
    unittest.main()
