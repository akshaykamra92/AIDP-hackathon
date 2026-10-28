"""Checks for the fixed SQL deployment script parser."""

import unittest

from scripts.deploy import DeploymentError, _check_table_counts, load_statements, split_sql_script


class DeployScriptTests(unittest.TestCase):
    def test_repo_scripts_parse_to_expected_statement_counts(self):
        statements = load_statements()
        self.assertEqual({stage: len(items) for stage, items in statements.items()}, {
            "schema": 74,
            "seed": 20,
            "verify": 10,
        })

    def test_semicolons_inside_literals_and_comments_do_not_split(self):
        statements = split_sql_script(
            "-- ignored;\nSELECT 'one;two', 'it''s;fine' FROM DUAL;"
            "/* ignored; */ SELECT \"A;B\" FROM DUAL;"
        )
        self.assertEqual(len(statements), 2)
        self.assertIn("'one;two'", statements[0])
        self.assertIn('"A;B"', statements[1])

    def test_unterminated_statement_is_rejected(self):
        with self.assertRaises(DeploymentError):
            split_sql_script("SELECT 1 FROM DUAL")
        with self.assertRaises(DeploymentError):
            split_sql_script("SELECT 'unfinished FROM DUAL;")

    def test_verify_only_allows_audit_and_request_activity(self):
        counts = [
            ("WH_LOCATION", 6),
            ("WH_EMPLOYEE", 2000),
            ("WH_TERMINATION", 240),
            ("WH_HEADCOUNT_MONTH", 432),
            ("WH_REQUISITION", 360),
            ("WH_METRIC", 5),
            ("WH_METRIC_REQUEST", 2),
            ("WH_ANSWER_LOG", 3),
        ]
        _check_table_counts(counts, fresh_seed=False)
        with self.assertRaises(DeploymentError):
            _check_table_counts(counts, fresh_seed=True)

    def test_verify_only_still_requires_exact_seed_counts(self):
        counts = [
            ("WH_LOCATION", 6),
            ("WH_EMPLOYEE", 1999),
            ("WH_TERMINATION", 240),
            ("WH_HEADCOUNT_MONTH", 432),
            ("WH_REQUISITION", 360),
            ("WH_METRIC", 5),
            ("WH_METRIC_REQUEST", 0),
            ("WH_ANSWER_LOG", 0),
        ]
        with self.assertRaises(DeploymentError):
            _check_table_counts(counts, fresh_seed=False)


if __name__ == "__main__":
    unittest.main()
