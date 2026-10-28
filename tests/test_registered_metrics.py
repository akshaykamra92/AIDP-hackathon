"""Registered metric contracts: typed catalog, fixed SQL, and bind-only values."""

import unittest
from datetime import date
from decimal import Decimal

from app.metrics import CompileError
from app.registered_metrics import RegisteredMetric, compile_registered


def manifest(**changes):
    values = {
        "metric_id": "FILLED_ORDERS",
        "name": "Filled orders",
        "owner": "Operations Analytics",
        "definition_text": "Number of orders fulfilled in the selected completed months.",
        "unit": "orders",
        "source_view": "WH_ORDER_METRIC_SOURCE",
        "date_column": "FILLED_DATE",
        "location_column": "LOCATION_KEY",
        "value_column": None,
        "aggregation": "COUNT",
        "allowed_dims": ["REGION", "CHANNEL"],
        "allowed_filters": ["REGION", "CHANNEL"],
    }
    values.update(changes)
    return values


def registered(**changes):
    return RegisteredMetric.from_manifest(manifest(**changes), 1, status="CERTIFIED")


class _Clob:
    def __init__(self, text):
        self.text = text

    def read(self):
        return self.text


class RegisteredMetricTests(unittest.TestCase):
    def test_manifest_and_oracle_row_round_trip(self):
        draft = RegisteredMetric.from_manifest(manifest(), 2)
        self.assertEqual("DRAFT", draft.status)
        self.assertEqual(("REGION", "CHANNEL"), draft.allowed_dims)
        row = {
            **{key.upper(): value for key, value in manifest().items()},
            "VERSION": Decimal(2),
            "STATUS": "CERTIFIED",
            "ALLOWED_DIMS": _Clob('["REGION","CHANNEL"]'),
            "ALLOWED_FILTERS": '["REGION","CHANNEL"]',
            "SOURCE_DDL_AT": date(2026, 10, 28),
        }
        approved = RegisteredMetric.from_row(row)
        self.assertEqual("CERTIFIED", approved.status)
        self.assertEqual(2, approved.version)
        self.assertEqual(date(2026, 10, 28), approved.source_ddl_at)
        self.assertEqual("orders", approved.public_dict()["unit"])
        self.assertEqual("LOCATION_KEY", approved.public_dict()["location_column"])
        self.assertEqual(["REGION", "CHANNEL"], approved.public_dict()["allowed_dims"])

    def test_count_compiles_fixed_sql_and_binds_hostile_filter(self):
        hostile = "West' OR 1=1 --"
        query = compile_registered(
            registered(), date(2026, 7, 1), date(2026, 10, 1),
            ["REGION"], {"REGION": hostile},
        )
        self.assertEqual(
            "SELECT src.REGION, COUNT(*) AS value FROM WH_ORDER_METRIC_SOURCE src "
            "WHERE src.FILLED_DATE >= :start_date AND src.FILLED_DATE < :end_date "
            "AND src.REGION = :f0 GROUP BY src.REGION HAVING COUNT(*) > 0 "
            "ORDER BY src.REGION FETCH FIRST 501 ROWS ONLY",
            query.sql,
        )
        self.assertNotIn(hostile, query.sql)
        self.assertEqual(hostile, query.binds["f0"])
        self.assertEqual(date(2026, 10, 1), query.binds["end_date"])
        self.assertEqual(("region", "value"), query.columns)

    def test_sum_and_average_require_measure_and_non_null_rows(self):
        for aggregation in ("SUM", "AVG"):
            with self.subTest(aggregation=aggregation):
                metric = registered(
                    aggregation=aggregation, value_column="AMOUNT", unit="USD"
                )
                query = compile_registered(
                    metric, date(2026, 1, 1), date(2026, 2, 1), (), {}
                )
                self.assertIn(f"{aggregation}(src.AMOUNT) AS value", query.sql)
                self.assertNotIn(" HAVING ", query.sql)
                self.assertIn("FETCH FIRST 501 ROWS ONLY", query.sql)
                self.assertEqual(("value",), query.columns)
                self.assertNotIn("GROUP BY", query.sql)

    def test_ratio_sum_uses_bound_filters_and_scoped_additive_components(self):
        metric = registered(
            aggregation="RATIO_SUM", numerator_column="ON_TIME_NUM",
            denominator_column="ELIGIBLE_DEN", unit="fraction",
        )
        query = compile_registered(
            metric, date(2026, 7, 1), date(2026, 10, 1),
            ["REGION"], {"CHANNEL": "DIRECT", "REGION": "FL"},
            scope_user_id="reader-1",
        )
        self.assertIn(
            "CASE WHEN COUNT(src.ON_TIME_NUM) = COUNT(*) "
            "AND COUNT(src.ELIGIBLE_DEN) = COUNT(*) AND SUM(src.ELIGIBLE_DEN) > 0 "
            "THEN SUM(src.ON_TIME_NUM) / NULLIF(SUM(src.ELIGIBLE_DEN), 0) "
            "ELSE CAST(NULL AS NUMBER) END AS value",
            query.sql,
        )
        self.assertIn("acl.LOCATION_KEY = src.LOCATION_KEY", query.sql)
        self.assertIn("src.CHANNEL = :f0 AND src.REGION = :f1", query.sql)
        self.assertNotIn(" HAVING ", query.sql)
        self.assertEqual(query.binds["f0"], "DIRECT")
        self.assertEqual(query.binds["f1"], "FL")
        self.assertEqual(query.binds["auth_user_id"], "reader-1")
        self.assertEqual(metric.public_dict()["numerator_column"], "ON_TIME_NUM")
        self.assertEqual(metric.public_dict()["denominator_column"], "ELIGIBLE_DEN")
        self.assertEqual(query.columns, ("region", "value"))

    def test_drafts_cannot_answer_but_can_compile_for_review(self):
        metric = RegisteredMetric.from_manifest(manifest(), 1)
        with self.assertRaises(CompileError) as caught:
            compile_registered(metric, date(2026, 7, 1), date(2026, 10, 1), (), {})
        self.assertEqual("METRIC_NOT_CERTIFIED", caught.exception.code)
        preview = compile_registered(
            metric, date(2026, 7, 1), date(2026, 10, 1), (), {},
            require_certified=False,
        )
        self.assertIn("COUNT(*) AS value", preview.sql)

        reviewed = RegisteredMetric.from_manifest(manifest(), 1, status="REVIEWED")
        with self.assertRaises(CompileError) as caught:
            compile_registered(reviewed, date(2026, 7, 1), date(2026, 10, 1), (), {})
        self.assertEqual("METRIC_NOT_CERTIFIED", caught.exception.code)

    def test_unapproved_or_invalid_dimensions_and_filters_fail(self):
        metric = registered()
        cases = (
            (["REGION", "REGION"], {}, "DUPLICATE_DIMENSION"),
            (["CUSTOMER_ID"], {}, "UNSUPPORTED_DIMENSION"),
            (["REGION; DROP TABLE WH_METRIC"], {}, "INVALID_IDENTIFIER"),
            ([], {"CUSTOMER_ID": 1}, "UNSUPPORTED_FILTER"),
            ([], {"REGION": ["WEST"]}, "INVALID_FILTER_VALUE"),
            ([], {"REGION": float("nan")}, "INVALID_FILTER_VALUE"),
            ([], {"REGION": None}, "INVALID_FILTER_VALUE"),
        )
        for dims, filters, code in cases:
            with self.subTest(dims=dims, filters=filters), self.assertRaises(CompileError) as caught:
                compile_registered(metric, date(2026, 7, 1), date(2026, 10, 1), dims, filters)
            self.assertEqual(code, caught.exception.code)

    def test_manifest_rejects_sql_and_unsafe_identifiers(self):
        cases = (
            (manifest(sql="SELECT * FROM WH_ORDER_METRIC_SOURCE"), "INVALID_MANIFEST"),
            (manifest(source_view="HR.ORDERS"), "INVALID_IDENTIFIER"),
            (manifest(source_view="OTHER_VIEW"), "INVALID_SOURCE_VIEW"),
            (manifest(date_column="FILLED_DATE DESC"), "INVALID_IDENTIFIER"),
            (manifest(location_column="LOCATION_KEY OR 1=1"), "INVALID_IDENTIFIER"),
            (manifest(allowed_dims=["REGION", "REGION"]), "INVALID_CATALOG"),
            (manifest(allowed_dims=["VALUE"]), "INVALID_CATALOG"),
            (manifest(aggregation="MAX"), "INVALID_AGGREGATION"),
            (manifest(aggregation="SUM"), "INVALID_VALUE_COLUMN"),
            (manifest(value_column="AMOUNT"), "INVALID_VALUE_COLUMN"),
            (manifest(numerator_column="ON_TIME_NUM"), "INVALID_VALUE_COLUMN"),
            (manifest(aggregation="SUM", value_column="AMOUNT", denominator_column="ELIGIBLE_DEN"), "INVALID_VALUE_COLUMN"),
            (manifest(aggregation="RATIO_SUM"), "INVALID_RATIO_COLUMNS"),
            (manifest(aggregation="RATIO_SUM", numerator_column="ON_TIME_NUM"), "INVALID_RATIO_COLUMNS"),
            (manifest(aggregation="RATIO_SUM", value_column="AMOUNT", numerator_column="ON_TIME_NUM", denominator_column="ELIGIBLE_DEN"), "INVALID_RATIO_COLUMNS"),
            (manifest(aggregation="RATIO_SUM", numerator_column="ON_TIME_NUM; DROP TABLE WH_METRIC", denominator_column="ELIGIBLE_DEN"), "INVALID_IDENTIFIER"),
        )
        for item, code in cases:
            with self.subTest(code=code, item=item), self.assertRaises(CompileError) as caught:
                RegisteredMetric.from_manifest(item, 1)
            self.assertEqual(code, caught.exception.code)

    def test_new_manifest_requires_location_and_legacy_row_cannot_be_scoped(self):
        missing_scope = manifest()
        del missing_scope["location_column"]
        with self.assertRaises(CompileError) as raised:
            RegisteredMetric.from_manifest(missing_scope, 1)
        self.assertEqual("LOCATION_SCOPE_REQUIRED", raised.exception.code)

        legacy = RegisteredMetric.from_row({
            **{key.upper(): value for key, value in missing_scope.items()},
            "VERSION": 1,
            "STATUS": "CERTIFIED",
        })
        self.assertIsNone(legacy.location_column)
        with self.assertRaises(CompileError) as raised:
            compile_registered(
                legacy, date(2026, 7, 1), date(2026, 10, 1), (), {},
                scope_user_id="reader-1",
            )
        self.assertEqual("LOCATION_SCOPE_REQUIRED", raised.exception.code)

    def test_scoped_sql_checks_live_grants_in_the_same_select(self):
        user_id = "reader' OR 1=1 --"
        query = compile_registered(
            registered(), date(2026, 7, 1), date(2026, 10, 1),
            ["REGION"], {"REGION": "WEST"}, scope_user_id=user_id,
        )
        self.assertEqual(user_id, query.binds["auth_user_id"])
        self.assertNotIn(user_id, query.sql)
        self.assertIn("FROM WH_USER_LOCATION acl", query.sql)
        self.assertIn("JOIN WH_SEC_LOCATION loc", query.sql)
        self.assertIn("JOIN WH_APP_USER usr", query.sql)
        self.assertIn("acl.USER_ID = :auth_user_id", query.sql)
        self.assertIn("usr.STATUS = 'ACTIVE'", query.sql)
        self.assertIn("acl.STATUS = 'ACTIVE'", query.sql)
        self.assertIn("loc.STATUS = 'ACTIVE'", query.sql)
        self.assertIn("acl.LOCATION_KEY = src.LOCATION_KEY", query.sql)
        self.assertIn("src.REGION = :f0", query.sql)
        self.assertIn("src.FILLED_DATE < :end_date", query.sql)

    def test_fractional_version_is_not_silently_truncated(self):
        with self.assertRaises(CompileError) as caught:
            RegisteredMetric.from_row({**manifest(), "VERSION": Decimal("1.5"), "STATUS": "DRAFT"})
        self.assertEqual("INVALID_METRIC_VERSION", caught.exception.code)

    def test_period_requires_complete_half_open_months(self):
        metric = registered()
        for start, end in (
            (date(2026, 7, 2), date(2026, 10, 1)),
            (date(2026, 7, 1), date(2026, 9, 30)),
            (date(2026, 10, 1), date(2026, 7, 1)),
        ):
            with self.subTest(start=start, end=end), self.assertRaises(CompileError) as caught:
                compile_registered(metric, start, end, (), {})
            self.assertEqual("INVALID_PERIOD", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
