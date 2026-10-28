"""Service tests with a fake database: no Oracle client or credentials required."""

import unittest
from datetime import date

from app.service import WarehouseService
from test_intent import JOBS, LOCATIONS


def months():
    values = []
    year, month = 2025, 4
    for _ in range(18):
        values.append(date(year, month, 1))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return values


class FakeRepository:
    def __init__(self):
        self.executed = []
        self.logged = []
        self.requested = []
        self.metric_marker = None
        self.result_rows = [{"value": 1.8854}]

    def list_locations(self):
        return LOCATIONS

    def list_job_families(self):
        return JOBS

    def list_available_months(self):
        return months()

    def get_metric(self, metric_id):
        return {
            "metric_id": metric_id,
            "name": "Turnover rate" if metric_id == "TURNOVER_RATE" else metric_id,
            "version": 1,
            "owner": "People Analytics",
            "status": "CERTIFIED",
            "definition_text": "Approved synthetic metric definition.",
            "sql_template": self.metric_marker or metric_id,
            "allowed_dims": '["state","location","job_family","month","quarter"]',
            "allowed_filters": '["state","location","job_family","month","quarter","period"]',
            "grain": "PERIOD",
        }

    def list_certified_metrics(self):
        return [self.get_metric(metric_id) for metric_id in (
            "TURNOVER_RATE", "HEADCOUNT", "TIME_TO_FILL_DAYS", "INTERNAL_FILL_RATE"
        )]

    def run_readonly(self, sql, binds):
        self.executed.append((sql, dict(binds)))
        if "ad_hoc_value" in sql:
            return [{
                "avg_headcount": 901.6667,
                "all_separations": 21,
                "certified_separations": 17,
                "internal_transfers": 4,
                "ad_hoc_value": 2.3290,
            }]
        return self.result_rows

    def log_answer(self, **kwargs):
        self.logged.append(kwargs)

    def create_request(self, question):
        self.requested.append(question)
        return 42


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.repository = FakeRepository()
        self.service = WarehouseService(self.repository, today=date(2026, 10, 28))

    def test_certified_answer_is_compiled_and_audited_after_read(self):
        result = self.service.ask("What was our turnover in Florida last quarter?")
        self.assertEqual("CERTIFIED", result["tier"])
        self.assertEqual(1.8854, result["rows"][0]["value"])
        self.assertEqual("percent", result["metric"]["unit"])
        self.assertEqual("2026-10-01", result["intent"]["period"]["end"])
        self.assertEqual(1, len(self.repository.executed))
        self.assertEqual(1, len(self.repository.logged))
        self.assertEqual("TURNOVER_RATE", self.repository.logged[0]["metric_id"])
        self.assertNotIn("Florida", self.repository.executed[0][0])
        self.assertEqual("FL", self.repository.executed[0][1]["state"])

    def test_material_qualifier_refuses_without_query_or_log(self):
        result = self.service.ask("Turnover in Florida Q3 2026 excluding contractors")
        self.assertEqual("EXPLORATORY", result["tier"])
        self.assertEqual("UNMAPPED_QUALIFIER", result["reason_code"])
        self.assertIn("excluding contractors", result["unmapped_terms"])
        self.assertIsNone(result["exploratory_answer"])
        self.assertEqual([], self.repository.executed)
        self.assertEqual([], self.repository.logged)

    def test_unfinished_period_refuses_without_query_or_log(self):
        result = self.service.ask("Turnover in Florida Q4 2026")
        self.assertEqual("DATA_COVERAGE", result["reason_code"])
        self.assertEqual([], self.repository.executed)
        self.assertEqual([], self.repository.logged)

    def test_catalog_template_change_refuses(self):
        self.repository.metric_marker = "SELECT * FROM WH_EMPLOYEE"
        result = self.service.ask("Turnover in Florida Q3 2026")
        self.assertEqual("CATALOG_TEMPLATE_MISMATCH", result["reason_code"])
        self.assertEqual([], self.repository.executed)

    def test_no_result_is_not_certified(self):
        self.repository.result_rows = []
        result = self.service.ask("Turnover in Florida Q3 2026")
        self.assertEqual("NO_DATA", result["reason_code"])
        self.assertEqual([], self.repository.logged)

    def test_certification_request_is_persisted(self):
        result = self.service.certification_request("What was our burnout rate?")
        self.assertEqual({"request_id": 42, "status": "OPEN"}, result)
        self.assertEqual(["What was our burnout rate?"], self.repository.requested)

    def test_demo_comparison_audits_its_certified_answer(self):
        result = self.service.demo_turnover_comparison()
        self.assertEqual(17, result["certified"]["separations"])
        self.assertEqual(21, result["ad_hoc"]["separations"])
        self.assertGreater(result["ad_hoc"]["value"], result["certified"]["value"])
        self.assertEqual(2, len(self.repository.executed))
        self.assertEqual(1, len(self.repository.logged))
        audit = self.repository.logged[0]
        self.assertEqual("CERTIFIED", audit["tier"])
        self.assertEqual("TURNOVER_RATE", audit["metric_id"])
        self.assertEqual(1, audit["metric_version"])
        self.assertEqual(1, audit["row_count"])
        self.assertEqual(self.repository.executed[0][0], audit["compiled_sql"])
        self.assertEqual(self.repository.executed[0][1], audit["binds"])


if __name__ == "__main__":
    unittest.main()
