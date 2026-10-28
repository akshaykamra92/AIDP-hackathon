"""Fail-closed checks for complete adapter-view DDL snapshots."""

from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace
from datetime import date, datetime

from app.registered_metrics import RegisteredMetric
from app.registered_repo import CatalogError, RegisteredMetricRepository


_DDL_AT = datetime(2026, 10, 28, 12, 34, 56)
_DDL = (
    "CREATE OR REPLACE VIEW WH_TEST_SOURCE AS SELECT METRIC_DATE, LOCATION_KEY "
    "FROM WH_TEST_FACT /*" + "x" * 40000 + "*/"
)
_MANIFEST = {
    "metric_id": "TEST_METRIC",
    "name": "Test metric",
    "owner": "Test owner",
    "definition_text": "Counts synthetic events by their event-time location.",
    "unit": "events",
    "source_view": "WH_TEST_SOURCE",
    "date_column": "METRIC_DATE",
    "location_column": "LOCATION_KEY",
    "value_column": None,
    "aggregation": "COUNT",
    "allowed_dims": [],
    "allowed_filters": [],
}


class _Clob:
    def __init__(self, value: str):
        self.value = value
        self.read_count = 0

    def read(self) -> str:
        self.read_count += 1
        return self.value


class _FakeDatabase:
    def __init__(self, ddl: str | None = _DDL):
        self.ddl = ddl
        self.ddl_at = _DDL_AT
        self.ddl_bind = None
        self.clob = None
        self.answer_reads = 0

    def run_readonly(self, sql, binds, *, client_identifier=None):
        if "FROM USER_VIEWS" in sql:
            return [{"view_name": "WH_TEST_SOURCE"}]
        if "FROM USER_OBJECTS" in sql:
            return [{"last_ddl_time": self.ddl_at, "status": "VALID"}]
        if "FROM USER_TAB_COLUMNS" in sql:
            return [
                {"column_name": "METRIC_DATE", "data_type": "DATE"},
                {"column_name": "LOCATION_KEY", "data_type": "VARCHAR2"},
            ]
        if "DBMS_METADATA.GET_DDL" in sql:
            self.ddl_bind = binds
            self.clob = _Clob(self.ddl) if self.ddl is not None else None
            return [{"source_ddl": self.clob}]
        if "FROM WH_TEST_SOURCE" in sql:
            self.answer_reads += 1
            return [{"value": 1}]
        raise AssertionError(f"Unexpected SQL: {sql}")


def _metric(*, status: str = "CERTIFIED", ddl: str = _DDL, with_hash: bool = True) -> RegisteredMetric:
    metric = RegisteredMetric.from_manifest(_MANIFEST, version=4, status=status)
    return replace(
        metric,
        source_ddl_at=_DDL_AT,
        source_ddl_sha256=hashlib.sha256(ddl.encode("utf-8")).hexdigest() if with_hash else None,
        reviewed_by="first_admin" if status != "DRAFT" else None,
    )


class ViewIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.database = _FakeDatabase()
        self.repo = RegisteredMetricRepository(self.database)
        self.repo.require_catalog = lambda: None
        self.logged = []
        self.repo.log_answer = lambda **fields: self.logged.append(fields)

    def test_full_clob_is_hashed_without_truncation(self):
        ddl_at, digest = self.repo.validate_source(_metric())
        self.assertEqual(ddl_at, _DDL_AT)
        self.assertEqual(digest, hashlib.sha256(_DDL.encode("utf-8")).hexdigest())
        self.assertEqual(self.database.ddl_bind, {"view_name": "WH_TEST_SOURCE"})
        self.assertEqual(self.database.clob.read_count, 1)

    def test_same_second_view_replacement_blocks_certified_answer(self):
        approved = _metric()
        self.repo.get_certified = lambda metric_id: approved
        self.database.ddl = _DDL[:-3] + "y*/"
        with self.assertRaises(CatalogError) as raised:
            self.repo.query(
                approved.metric_id, date(2026, 7, 1), date(2026, 10, 1),
                user_id="reader_one",
            )
        self.assertEqual(raised.exception.code, "SOURCE_CHANGED")
        self.assertEqual(self.database.ddl_at, approved.source_ddl_at)
        self.assertEqual(self.database.answer_reads, 0)
        self.assertEqual(self.logged, [])

    def test_old_certified_version_without_digest_fails_closed(self):
        approved = _metric(with_hash=False)
        self.repo.get_certified = lambda metric_id: approved
        with self.assertRaises(CatalogError) as raised:
            self.repo.query(
                approved.metric_id, date(2026, 7, 1), date(2026, 10, 1),
                user_id="reader_one",
            )
        self.assertEqual(raised.exception.code, "SOURCE_CHANGED")
        self.assertEqual(self.database.answer_reads, 0)
        self.assertEqual(self.logged, [])

    def test_missing_metadata_clob_is_rejected(self):
        self.database.ddl = None
        with self.assertRaises(CatalogError) as raised:
            self.repo.validate_source(_metric())
        self.assertEqual(raised.exception.code, "SOURCE_VIEW_INVALID")

    def test_review_and_certify_supply_the_current_digest_to_package(self):
        calls = []
        self.repo._call_governance = lambda procedure, arguments: calls.append((procedure, arguments))

        draft = _metric(status="DRAFT")
        self.repo.get_version = lambda metric_id, version: draft
        self.repo.review_draft(draft.metric_id, draft.version, "first_admin", "Reviewed source and meaning")

        reviewed = _metric(status="REVIEWED")
        self.repo.get_version = lambda metric_id, version: reviewed
        self.repo.certify(reviewed.metric_id, reviewed.version, "second_admin")

        digest = hashlib.sha256(_DDL.encode("utf-8")).hexdigest()
        self.assertEqual(calls[0], (
            "REVIEW_DRAFT", [draft.metric_id, draft.version, "first_admin", "Reviewed source and meaning", digest],
        ))
        self.assertEqual(calls[1], (
            "CERTIFY", [reviewed.metric_id, reviewed.version, "second_admin", digest],
        ))

    def test_scoped_listing_requires_a_reviewed_digest(self):
        recorded = []
        self.repo.require_catalog = lambda: None
        self.database.run_readonly = lambda sql, binds: recorded.append(sql) or []
        self.repo.list_certified(scoped_only=True)
        self.assertIn("LOCATION_COLUMN IS NOT NULL", recorded[0])
        self.assertIn("SOURCE_DDL_SHA256 IS NOT NULL", recorded[0])


if __name__ == "__main__":
    unittest.main()
