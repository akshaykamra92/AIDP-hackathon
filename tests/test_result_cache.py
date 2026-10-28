"""Cache keys and the certified-query boundary without an Oracle connection."""

from __future__ import annotations

import hashlib
import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.metrics import CompiledQuery
from app.registered_metrics import RegisteredMetric
from app.registered_repo import CatalogError, RegisteredMetricRepository
from app.result_cache import (
    CacheHit, CachePolicy, ResultCacheRepository, build_identity,
)
from tests.test_registered_repo import FakeDatabase


VIEW_DDL = "CREATE OR REPLACE VIEW WH_FILLED_REQ_VIEW AS SELECT * FROM WH_SYNTHETIC_SOURCE"


def metric() -> RegisteredMetric:
    return RegisteredMetric.from_row({
        "metric_id": "FILLED_REQUISITIONS",
        "version": 1,
        "name": "Filled requisitions",
        "owner": "Synthetic Analytics",
        "definition_text": "Count of requisitions filled in a completed month.",
        "unit": "requisitions",
        "source_view": "WH_FILLED_REQ_VIEW",
        "date_column": "METRIC_DATE",
        "location_column": "LOCATION_KEY",
        "value_column": None,
        "numerator_column": None,
        "denominator_column": None,
        "aggregation": "COUNT",
        "allowed_dims": '["STATE"]',
        "allowed_filters": '["STATE"]',
        "status": "CERTIFIED",
        "source_ddl_at": datetime(2026, 10, 1, 12, 0, 0),
        "source_ddl_sha256": hashlib.sha256(VIEW_DDL.encode()).hexdigest(),
    })


def compiled(*, value="FL", user_id="reader-1") -> CompiledQuery:
    return CompiledQuery(
        sql="SELECT COUNT(*) AS value FROM WH_FILLED_REQ_VIEW src WHERE src.STATE = :f0 AND src.LOCATION_KEY = :auth_user_id",
        binds={"f0": value, "auth_user_id": user_id, "start_date": date(2026, 7, 1)},
        columns=("value",),
    )


class FakeCache:
    def __init__(self):
        self.policy = CachePolicy(1800, 1, 1, 1)
        self.saved = {}
        self.put_calls = 0
        self.get_calls = 0
        self.query_started_at = datetime(2026, 10, 28, tzinfo=timezone.utc)

    def policy_for(self, _metric):
        return self.policy

    def get_hit(self, identity):
        self.get_calls += 1
        return self.saved.get(identity.key)

    def current_utc(self):
        return self.query_started_at

    def put(self, identity, rows, generated_at):
        self.put_calls += 1
        self.saved[identity.key] = CacheHit(list(rows), generated_at)
        return True


class RecordingReadDatabase:
    def __init__(self, rows):
        self.rows = rows
        self.sql = None
        self.binds = None
        self.client_identifier = None

    def run_readonly(self, sql, binds, *, client_identifier=None):
        self.sql = sql
        self.binds = binds
        self.client_identifier = client_identifier
        return self.rows


class NeverConnectDatabase:
    def _connect(self):
        raise AssertionError("A bounded result should not connect to Oracle")


class CacheWriteCursor:
    def __init__(self):
        self.executions = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _trace):
        return False

    def execute(self, statement, binds):
        self.executions.append((statement, binds))


class CacheWriteConnection:
    def __init__(self):
        self.recorded = CacheWriteCursor()
        self.committed = False

    def cursor(self):
        return self.recorded

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


class CacheWriteDatabase:
    def __init__(self):
        self.connection = CacheWriteConnection()

    def _connect(self):
        return self.connection


class AuditCursor:
    def __init__(self):
        self.statement = None
        self.binds = None

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _trace):
        return False

    def execute(self, statement, binds):
        self.statement = statement
        self.binds = binds


class AuditConnection:
    def __init__(self):
        self.recorded = AuditCursor()
        self.committed = False

    def cursor(self):
        return self.recorded

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


class AuditDatabase:
    def __init__(self):
        self.connection = AuditConnection()

    def _connect(self):
        return self.connection


class ResultCacheTests(unittest.TestCase):
    def test_database_clock_is_read_before_cacheable_source_query(self):
        database = RecordingReadDatabase([{"now_utc": datetime(2026, 10, 28, 12, 0)}])
        self.assertEqual(
            ResultCacheRepository(database).current_utc(),
            datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc),
        )
        self.assertIn("SYS_EXTRACT_UTC(SYSTIMESTAMP)", database.sql)

    def test_policy_is_opt_in_for_one_certified_version(self):
        database = RecordingReadDatabase([{
            "ttl_seconds": 1800, "access_revision": 2,
            "data_revision": 3, "policy_revision": 4,
        }])
        self.assertEqual(ResultCacheRepository(database).policy_for(metric()), CachePolicy(1800, 2, 3, 4))
        self.assertIn("p.ENABLED = 'Y'", database.sql)
        self.assertIn("metric.STATUS = 'CERTIFIED'", database.sql)
        self.assertIn("USER_TRIGGERS", database.sql)
        self.assertEqual(database.binds["metric_version"], 1)
        database.rows = []
        self.assertIsNone(ResultCacheRepository(database).policy_for(metric()))

    def test_canonical_key_is_typed_and_independent_of_wording(self):
        policy = CachePolicy(1800, 4, 7, 2)
        first = build_identity(metric(), compiled(), "reader-1", policy)
        reordered = CompiledQuery(
            sql=compiled().sql,
            binds={"start_date": date(2026, 7, 1), "auth_user_id": "reader-1", "f0": "FL"},
            columns=("value",),
        )
        second = build_identity(metric(), reordered, "reader-1", policy)
        self.assertEqual(first.key, second.key)
        self.assertNotIn("question", first.canonical_json)
        self.assertNotIn("token", first.canonical_json)
        self.assertNotEqual(first.key, build_identity(metric(), compiled(value=7), "reader-1", policy).key)
        self.assertNotEqual(first.key, build_identity(metric(), compiled(value="7"), "reader-1", policy).key)
        later_period = CompiledQuery(
            sql=compiled().sql,
            binds={**compiled().binds, "start_date": date(2026, 8, 1)},
            columns=("value",),
        )
        self.assertNotEqual(first.key, build_identity(metric(), later_period, "reader-1", policy).key)
        grouped = CompiledQuery(
            sql=compiled().sql + " GROUP BY src.STATE",
            binds=compiled().binds,
            columns=("state", "value"),
        )
        self.assertNotEqual(first.key, build_identity(metric(), grouped, "reader-1", policy).key)
        self.assertNotEqual(first.key, build_identity(replace(metric(), version=2), compiled(), "reader-1", policy).key)
        self.assertNotEqual(first.key, build_identity(metric(), compiled(user_id="reader-2"), "reader-2", policy).key)
        self.assertNotEqual(first.key, build_identity(metric(), compiled(), "reader-1", CachePolicy(1800, 5, 7, 2)).key)
        self.assertNotEqual(first.key, build_identity(metric(), compiled(), "reader-1", CachePolicy(1800, 4, 8, 2)).key)
        self.assertNotEqual(first.key, build_identity(metric(), compiled(), "reader-1", CachePolicy(1800, 4, 7, 3)).key)

    def test_key_rejects_a_different_authenticated_bind(self):
        with self.assertRaises(ValueError):
            build_identity(metric(), compiled(user_id="reader-1"), "reader-2", CachePolicy(1800, 1, 1, 1))

    def test_hit_read_rechecks_access_certification_revisions_and_expiry(self):
        identity = build_identity(metric(), compiled(), "reader-1", CachePolicy(1800, 2, 3, 4))
        generated = datetime(2026, 10, 28, tzinfo=timezone.utc)
        database = RecordingReadDatabase([{
            "result_json": json.dumps([{"value": 7}]),
            "row_count": 1,
            "generated_at_utc": generated.replace(tzinfo=None),
            "canonical_json": identity.canonical_json,
        }])
        hit = ResultCacheRepository(database).get_hit(identity)
        self.assertEqual(hit, CacheHit([{"value": 7}], generated))
        self.assertEqual(hit.generated_at.utcoffset().total_seconds(), 0)
        self.assertEqual(database.binds, {"cache_key": identity.key, "auth_user_id": "reader-1"})
        self.assertEqual(database.client_identifier, "reader-1")
        self.assertIn("SYS_EXTRACT_UTC(cache.GENERATED_AT) AS GENERATED_AT_UTC", database.sql)
        for predicate in (
            "usr.STATUS = 'ACTIVE'", "state.ACCESS_REVISION = cache.ACCESS_REVISION",
            "policy.ENABLED = 'Y'", "policy.POLICY_REVISION = cache.POLICY_REVISION",
            "source.DATA_REVISION = cache.DATA_REVISION", "metric.STATUS = 'CERTIFIED'",
            "obj.LAST_DDL_TIME = metric.SOURCE_DDL_AT", "cache.EXPIRES_AT > SYSTIMESTAMP",
            "acl.STATUS = 'ACTIVE'", "loc.STATUS = 'ACTIVE'",
            "cache.USER_ID = :auth_user_id",
            "USER_TRIGGERS",
        ):
            self.assertIn(predicate, database.sql)

    def test_corrupt_hit_is_rejected(self):
        identity = build_identity(metric(), compiled(), "reader-1", CachePolicy(1800, 2, 3, 4))
        database = RecordingReadDatabase([{
            "result_json": '[{"value":7}]', "row_count": 1,
            "generated_at_utc": datetime(2026, 10, 28),
            "canonical_json": "different request",
        }])
        self.assertIsNone(ResultCacheRepository(database).get_hit(identity))

    def test_oracle_native_json_objects_are_valid_cache_hits(self):
        identity = build_identity(metric(), compiled(), "reader-1", CachePolicy(1800, 2, 3, 4))
        generated = datetime(2026, 10, 28, tzinfo=timezone.utc)
        native_canonical = json.loads(identity.canonical_json)
        native_canonical = dict(reversed(list(native_canonical.items())))
        database = RecordingReadDatabase([{
            "canonical_json": native_canonical,
            "result_json": [{"value": Decimal("7.5")}],
            "row_count": Decimal(1),
            "generated_at_utc": generated.replace(tzinfo=None),
        }])
        self.assertEqual(ResultCacheRepository(database).get_hit(identity), CacheHit([{"value": 7.5}], generated))

    def test_native_json_with_changed_type_or_oversize_result_is_rejected(self):
        identity = build_identity(metric(), compiled(), "reader-1", CachePolicy(1800, 2, 3, 4))
        changed_canonical = json.loads(identity.canonical_json)
        changed_canonical["access_revision"] = True  # Python would otherwise compare True == 1.
        database = RecordingReadDatabase([{
            "canonical_json": changed_canonical,
            "result_json": [{"value": 7}],
            "row_count": 1,
            "generated_at_utc": datetime(2026, 10, 28),
        }])
        self.assertIsNone(ResultCacheRepository(database).get_hit(identity))

        database.rows[0]["canonical_json"] = json.loads(identity.canonical_json)
        database.rows[0]["result_json"] = [{"value": "x" * 300_000}]
        self.assertIsNone(ResultCacheRepository(database).get_hit(identity))

    def test_cache_result_is_bounded_before_write(self):
        identity = build_identity(metric(), compiled(), "reader-1", CachePolicy(1800, 1, 1, 1))
        cache = ResultCacheRepository(NeverConnectDatabase())
        generated_at = datetime(2026, 10, 28, tzinfo=timezone.utc)
        self.assertFalse(cache.put(identity, [{"value": 1}] * 501, generated_at))
        self.assertFalse(cache.put(identity, [{"value": 1, "detail": "x" * 300_000}], generated_at))
        self.assertFalse(cache.put(identity, [{"value": 1}], generated_at))  # Optional cache survives connection failure.

    def test_cache_write_expiry_uses_source_start_not_later_write_time(self):
        identity = build_identity(metric(), compiled(), "reader-1", CachePolicy(1800, 1, 1, 1))
        database = CacheWriteDatabase()
        generated_at = datetime(2026, 10, 28, 5, 0, tzinfo=timezone(-timedelta(hours=7)))
        self.assertTrue(ResultCacheRepository(database).put(identity, [{"value": 7}], generated_at))
        self.assertTrue(database.connection.committed)
        statements = database.connection.recorded.executions
        self.assertEqual(len(statements), 2)  # UPDATE miss, then INSERT.
        for sql, binds in statements:
            self.assertIn("FROM_TZ(TO_TIMESTAMP(:generated_at_utc", sql)
            self.assertIn("NUMTODSINTERVAL(:ttl_seconds, 'SECOND')", sql)
            self.assertNotIn("GENERATED_AT = SYSTIMESTAMP", sql)
            self.assertEqual(binds["generated_at_utc"], "2026-10-28T12:00:00.000000")
            self.assertEqual(binds["ttl_seconds"], 1800)

    def test_hit_audit_explicitly_says_sql_was_not_executed(self):
        database = AuditDatabase()
        repo = RegisteredMetricRepository(database, cache_enabled=True)
        generated_at = datetime(2026, 10, 27, 17, tzinfo=timezone(-timedelta(hours=7)))
        repo.log_answer(
            metric=metric(), query={"metric_id": "FILLED_REQUISITIONS"},
            compiled_sql="SELECT COUNT(*) AS value FROM WH_FILLED_REQ_VIEW",
            binds={"auth_user_id": "reader-1"}, row_count=1, asked_by="reader-1",
            cache_status="HIT", cache_key="a" * 64,
            result_generated_at=generated_at,
        )
        cursor = database.connection.recorded
        self.assertIn("CACHE_STATUS, CACHE_KEY, RESULT_GENERATED_AT", cursor.statement)
        self.assertEqual(cursor.binds["cache_status"], "HIT")
        self.assertIn("FROM_TZ(TO_TIMESTAMP(:result_generated_at_utc", cursor.statement)
        self.assertEqual(cursor.binds["result_generated_at_utc"], "2026-10-28T00:00:00.000000")
        self.assertTrue(database.connection.committed)

    def test_repository_miss_hit_user_isolation_and_revision_change(self):
        database = FakeDatabase()
        cache = FakeCache()
        repo = RegisteredMetricRepository(database, result_cache=cache, cache_enabled=True)
        audit = []
        repo.log_answer = lambda **values: audit.append(values)
        first = repo.query("FILLED_REQUISITIONS", date(2026, 7, 1), date(2026, 10, 1), user_id="reader-1")
        second = repo.query("FILLED_REQUISITIONS", date(2026, 7, 1), date(2026, 10, 1), user_id="reader-1")
        self.assertEqual([first["cache"]["status"], second["cache"]["status"]], ["MISS", "HIT"])
        self.assertEqual(first["cache"]["generated_at"], cache.query_started_at.isoformat())
        self.assertEqual(second["cache"]["generated_at"], first["cache"]["generated_at"])
        self.assertEqual(database.query_calls, 1)
        self.assertEqual([entry["cache_status"] for entry in audit], ["MISS", "HIT"])
        self.assertEqual(audit[0]["cache_key"], audit[1]["cache_key"])
        self.assertEqual(cache.put_calls, 1)

        other = repo.query("FILLED_REQUISITIONS", date(2026, 7, 1), date(2026, 10, 1), user_id="reader-2")
        self.assertEqual(other["cache"]["status"], "MISS")
        self.assertEqual(database.query_calls, 2)
        cache.policy = CachePolicy(1800, 2, 1, 1)
        changed_access = repo.query("FILLED_REQUISITIONS", date(2026, 7, 1), date(2026, 10, 1), user_id="reader-1")
        self.assertEqual(changed_access["cache"]["status"], "MISS")
        cache.policy = CachePolicy(1800, 2, 2, 1)
        changed_data = repo.query("FILLED_REQUISITIONS", date(2026, 7, 1), date(2026, 10, 1), user_id="reader-1")
        self.assertEqual(changed_data["cache"]["status"], "MISS")
        self.assertEqual(database.query_calls, 4)

    def test_view_change_refuses_before_cached_hit(self):
        database = FakeDatabase()
        cache = FakeCache()
        repo = RegisteredMetricRepository(database, result_cache=cache, cache_enabled=True)
        repo.log_answer = lambda **_values: None
        repo.query("FILLED_REQUISITIONS", date(2026, 7, 1), date(2026, 10, 1), user_id="reader-1")
        database.ddl_at = datetime(2026, 10, 2, 12, 0, 0)
        with self.assertRaises(CatalogError) as raised:
            repo.query("FILLED_REQUISITIONS", date(2026, 7, 1), date(2026, 10, 1), user_id="reader-1")
        self.assertEqual(raised.exception.code, "SOURCE_CHANGED")
        self.assertEqual(cache.get_calls, 1)
        self.assertEqual(database.query_calls, 1)

    def test_unconfigured_policy_keeps_normal_scoped_query(self):
        database = FakeDatabase()
        cache = FakeCache()
        cache.policy = None
        repo = RegisteredMetricRepository(database, result_cache=cache, cache_enabled=True)
        audit = []
        repo.log_answer = lambda **values: audit.append(values)
        response = repo.query("FILLED_REQUISITIONS", date(2026, 7, 1), date(2026, 10, 1), user_id="reader-1")
        self.assertEqual(response["cache"]["status"], "DISABLED")
        self.assertEqual(database.query_calls, 1)
        self.assertIsNone(audit[0]["cache_key"])


if __name__ == "__main__":
    unittest.main()
