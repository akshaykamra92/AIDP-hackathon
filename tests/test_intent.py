"""Golden intent and refusal checks that run without Oracle or web dependencies."""

import json
import unittest
from datetime import date
from pathlib import Path

from app.intent import parse_question


LOCATIONS = [
    {"location_id": 1, "name": "Orlando Rehab Center", "city": "Orlando", "state": "FL"},
    {"location_id": 2, "name": "Tampa Rehab Center", "city": "Tampa", "state": "FL"},
    {"location_id": 3, "name": "Jacksonville Rehab Center", "city": "Jacksonville", "state": "FL"},
    {"location_id": 4, "name": "Birmingham Rehab Center", "city": "Birmingham", "state": "AL"},
    {"location_id": 5, "name": "Dallas Rehab Center", "city": "Dallas", "state": "TX"},
    {"location_id": 6, "name": "Atlanta Rehab Center", "city": "Atlanta", "state": "GA"},
]
JOBS = ["NURSING", "THERAPY", "CLINICAL_SUPPORT", "OPERATIONS"]
TODAY = date(2026, 10, 28)


def parse(question):
    return parse_question(question, locations=LOCATIONS, job_families=JOBS, today=TODAY)


class IntentGoldenTests(unittest.TestCase):
    def test_golden_cases(self):
        cases = json.loads((Path(__file__).parent / "golden.json").read_text())
        for case in cases:
            with self.subTest(question=case["question"]):
                result = parse(case["question"])
                if "refusal" in case:
                    self.assertIsNone(result.intent)
                    self.assertEqual(case["refusal"], result.reason_code)
                    continue
                self.assertIsNotNone(result.intent, result)
                intent = result.intent
                self.assertEqual(case["metric_id"], intent.metric_id)
                self.assertEqual(case["filters"], intent.filters)
                self.assertEqual(case["dimensions"], list(intent.dimensions))
                self.assertEqual(case["start"], intent.period.start.isoformat())
                self.assertEqual(case["end"], intent.period.end.isoformat())

    def test_paraphrases_have_identical_normalized_intent(self):
        pairs = [
            ("What was our turnover in Florida last quarter?", "Give me attrition rate for FL Q3 2026"),
            ("Headcount in Florida last month", "What was our employee count in FL September 2026?"),
            ("Median time to fill in Q3 2026", "Days to fill last quarter"),
        ]
        for left, right in pairs:
            with self.subTest(left=left, right=right):
                left_intent = parse(left).intent
                right_intent = parse(right).intent
                self.assertIsNotNone(left_intent)
                self.assertIsNotNone(right_intent)
                self.assertEqual(left_intent.metric_id, right_intent.metric_id)
                self.assertEqual(left_intent.dimensions, right_intent.dimensions)
                self.assertEqual(left_intent.filters, right_intent.filters)
                self.assertEqual(left_intent.period.start, right_intent.period.start)
                self.assertEqual(left_intent.period.end, right_intent.period.end)

    def test_unknown_qualifier_cannot_be_certified(self):
        for qualifier in ("excluding contractors", "including transfers", "annualized", "for night shifts", "only full-time"):
            with self.subTest(qualifier=qualifier):
                result = parse("Turnover in Florida Q3 2026 " + qualifier)
                self.assertIsNone(result.intent)
                self.assertEqual("UNMAPPED_QUALIFIER", result.reason_code)


if __name__ == "__main__":
    unittest.main()
