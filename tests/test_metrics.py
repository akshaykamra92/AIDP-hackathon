"""Compiler invariants: approved formula, allowlisted identifiers, bound values."""

import unittest
from datetime import date

from app.intent import Intent, Period
from app.metrics import CompileError, MetricDefinition, compile_certified


ALLOWED = '["state","location","job_family","month","quarter"]'
FILTERS = '["state","location","job_family","month","quarter","period"]'


def catalog(metric_id, **overrides):
    row = {
        "metric_id": metric_id,
        "name": metric_id.replace("_", " ").title(),
        "version": 1,
        "owner": "Synthetic Analytics",
        "status": "CERTIFIED",
        "definition_text": "Approved synthetic metric definition.",
        "sql_template": metric_id,
        "allowed_dims": ALLOWED,
        "allowed_filters": FILTERS,
        "grain": "PERIOD",
    }
    row.update(overrides)
    return MetricDefinition.from_row(row)


def intent(metric_id, dimensions=(), filters=None):
    return Intent(
        metric_id,
        tuple(dimensions),
        filters or {},
        Period(date(2026, 7, 1), date(2026, 10, 1), "Q3 2026"),
    )


class CompilerTests(unittest.TestCase):
    def test_turnover_sums_headcount_by_month_before_averaging(self):
        query = compile_certified(intent("TURNOVER_RATE", filters={"state": "FL"}), catalog("TURNOVER_RATE"))
        self.assertIn("SUM(h.headcount) AS monthly_count", query.sql)
        self.assertIn("AVG(monthly_count) AS avg_headcount", query.sql)
        self.assertIn("t.term_type <> 'INTERNAL_TRANSFER'", query.sql)
        self.assertIn("ROUND(100 * NVL(n.terminations, 0) / d.avg_headcount, 4)", query.sql)
        self.assertEqual(3, query.binds["expected_months"])
        self.assertEqual("FL", query.binds["state"])
        self.assertEqual(("value",), query.columns)

    def test_voluntary_uses_only_voluntary_events(self):
        query = compile_certified(intent("VOLUNTARY_TURNOVER_RATE"), catalog("VOLUNTARY_TURNOVER_RATE"))
        self.assertIn("t.term_type = 'VOLUNTARY'", query.sql)
        self.assertNotIn("t.term_type <> 'INTERNAL_TRANSFER'", query.sql)

    def test_headcount_uses_latest_month_end_in_selected_period(self):
        query = compile_certified(intent("HEADCOUNT", filters={"state": "FL"}), catalog("HEADCOUNT"))
        self.assertIn("ROW_NUMBER() OVER", query.sql)
        self.assertIn("month_start = :last_month_start", query.sql)
        self.assertEqual(date(2026, 9, 1), query.binds["last_month_start"])
        self.assertNotIn("expected_months", query.binds)

    def test_grouped_headcount_does_not_send_unused_bind(self):
        query = compile_certified(intent("HEADCOUNT", dimensions=("month",)), catalog("HEADCOUNT"))
        self.assertNotIn("last_month_start", query.binds)
        self.assertEqual(("month", "value"), query.columns)

    def test_requisition_period_uses_fill_date(self):
        for metric_id in ("TIME_TO_FILL_DAYS", "INTERNAL_FILL_RATE"):
            with self.subTest(metric=metric_id):
                query = compile_certified(intent(metric_id), catalog(metric_id))
                self.assertIn("r.fill_date >= :start_date", query.sql)
                self.assertIn("r.fill_date < :end_date", query.sql)
                self.assertNotIn("r.open_date >= :start_date", query.sql)

    def test_user_value_can_only_be_a_bind(self):
        hostile = "FL' OR 1=1 --"
        query = compile_certified(intent("TURNOVER_RATE", filters={"state": hostile}), catalog("TURNOVER_RATE"))
        self.assertNotIn(hostile, query.sql)
        self.assertEqual(hostile, query.binds["state"])

    def test_unapproved_identifier_and_changed_template_fail_closed(self):
        with self.assertRaises(CompileError):
            compile_certified(intent("TURNOVER_RATE", dimensions=("state; DELETE FROM WH_METRIC",)), catalog("TURNOVER_RATE"))
        with self.assertRaises(CompileError):
            compile_certified(intent("TURNOVER_RATE"), catalog("TURNOVER_RATE", sql_template="SELECT * FROM WH_EMPLOYEE"))
        with self.assertRaises(CompileError):
            compile_certified(intent("TURNOVER_RATE"), catalog("TURNOVER_RATE", status="DRAFT"))


if __name__ == "__main__":
    unittest.main()
