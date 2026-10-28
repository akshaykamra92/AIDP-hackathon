"""Oracle-backed lifecycle and execution for portable, view-based metrics.

Metric authors register metadata, not SQL. The only executable statement for a
registered metric is assembled by ``compile_registered`` from identifiers that
were validated against an owned Oracle view. Governance writes go through the
WH_METRIC_GOVERNANCE package; answer reads use a read-only transaction.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Sequence

from app.db import DatabaseUnavailable, OracleRepository
from app.metrics import CompileError
from app.registered_metrics import MAX_ANSWER_ROWS, RegisteredMetric, compile_registered
from app.result_cache import CacheIdentity, ResultCacheRepository, build_identity, utc_timestamp_text


class CatalogError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_METRIC_COLUMNS = (
    "metric_id, version, name, owner, definition_text, unit, source_view, "
    "date_column, location_column, value_column, numerator_column, denominator_column, "
    "aggregation, allowed_dims, allowed_filters, "
    "status, created_at, reviewed_at, reviewed_by, review_note, "
    "certified_at, certified_by, source_ddl_at, source_ddl_sha256"
)

_TEXT_TYPES = {"VARCHAR2", "CHAR", "NVARCHAR2", "NCHAR"}
_NUMBER_TYPES = {"NUMBER", "FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE"}


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _actor(value: str, label: str) -> str:
    actor = value.strip()
    if not actor or len(actor) > 120 or any(ord(char) < 32 for char in actor):
        raise CatalogError("INVALID_ACTOR", f"{label} must be a printable name of at most 120 characters.")
    return actor


class RegisteredMetricRepository:
    """Use an Oracle schema containing the non-destructive portable catalog."""

    def __init__(
        self, database: OracleRepository | None = None,
        *, result_cache: ResultCacheRepository | None = None,
        cache_enabled: bool | None = None,
    ):
        self.database = database or OracleRepository()
        self.cache_enabled = (
            cache_enabled if cache_enabled is not None
            else os.getenv("RESULT_CACHE_ENABLED", "false").strip().lower() == "true"
        )
        self.result_cache = result_cache or ResultCacheRepository(self.database)

    def catalog_ready(self) -> bool:
        rows = self.database.run_readonly(
            "SELECT COUNT(*) AS object_count FROM USER_TABLES "
            "WHERE TABLE_NAME IN ('WH_REGISTERED_METRIC', 'WH_REGISTERED_ANSWER_LOG')",
            {},
        )
        return bool(rows and int(rows[0]["object_count"]) == 2)

    def require_catalog(self) -> None:
        if not self.catalog_ready():
            raise CatalogError(
                "CATALOG_NOT_INSTALLED",
                "Portable metric catalog is not installed. Run scripts/install_catalog.py --apply.",
            )

    def list_certified(self, *, scoped_only: bool = False) -> list[RegisteredMetric]:
        self.require_catalog()
        location_clause = (
            " AND LOCATION_COLUMN IS NOT NULL AND SOURCE_DDL_SHA256 IS NOT NULL"
            if scoped_only else ""
        )
        rows = self.database.run_readonly(
            f"SELECT {_METRIC_COLUMNS} FROM WH_REGISTERED_METRIC "
            "WHERE STATUS = 'CERTIFIED'" + location_clause + " ORDER BY NAME, METRIC_ID",
            {},
        )
        return [RegisteredMetric.from_row(row) for row in rows]

    def get_certified(self, metric_id: str) -> RegisteredMetric | None:
        self.require_catalog()
        rows = self.database.run_readonly(
            f"SELECT {_METRIC_COLUMNS} FROM WH_REGISTERED_METRIC "
            "WHERE METRIC_ID = :metric_id AND STATUS = 'CERTIFIED' "
            "ORDER BY VERSION DESC FETCH FIRST 1 ROW ONLY",
            {"metric_id": metric_id.upper()},
        )
        return RegisteredMetric.from_row(rows[0]) if rows else None

    def get_version(self, metric_id: str, version: int) -> RegisteredMetric | None:
        self.require_catalog()
        rows = self.database.run_readonly(
            f"SELECT {_METRIC_COLUMNS} FROM WH_REGISTERED_METRIC "
            "WHERE METRIC_ID = :metric_id AND VERSION = :version",
            {"metric_id": metric_id.upper(), "version": version},
        )
        return RegisteredMetric.from_row(rows[0]) if rows else None

    def list_versions(self, metric_id: str | None = None) -> list[RegisteredMetric]:
        self.require_catalog()
        if metric_id:
            sql = (
                f"SELECT {_METRIC_COLUMNS} FROM WH_REGISTERED_METRIC "
                "WHERE METRIC_ID = :metric_id ORDER BY VERSION DESC"
            )
            binds = {"metric_id": metric_id.upper()}
        else:
            sql = f"SELECT {_METRIC_COLUMNS} FROM WH_REGISTERED_METRIC ORDER BY METRIC_ID, VERSION DESC"
            binds = {}
        return [RegisteredMetric.from_row(row) for row in self.database.run_readonly(sql, binds)]

    def validate_source(self, metric: RegisteredMetric) -> tuple[datetime, str]:
        """Check the owned view contract and hash its complete Oracle DDL CLOB."""
        owned_view = self.database.run_readonly(
            "SELECT VIEW_NAME FROM USER_VIEWS WHERE VIEW_NAME = :view_name",
            {"view_name": metric.source_view},
        )
        if not owned_view:
            raise CatalogError("SOURCE_VIEW_MISSING", "The source must be an owned WH_ adapter view.")
        rows = self.database.run_readonly(
            "SELECT LAST_DDL_TIME, STATUS FROM USER_OBJECTS "
            "WHERE OBJECT_NAME = :view_name AND OBJECT_TYPE = 'VIEW'",
            {"view_name": metric.source_view},
        )
        if not rows or str(rows[0]["status"]).upper() != "VALID":
            raise CatalogError("SOURCE_VIEW_MISSING", "The owned WH_ adapter view does not exist or is invalid.")
        ddl_at = rows[0]["last_ddl_time"]
        if not isinstance(ddl_at, datetime):
            raise CatalogError("SOURCE_VIEW_INVALID", "The adapter view has no valid DDL timestamp.")

        columns = {
            str(row["column_name"]).upper(): str(row["data_type"]).upper()
            for row in self.database.run_readonly(
                "SELECT COLUMN_NAME, DATA_TYPE FROM USER_TAB_COLUMNS WHERE TABLE_NAME = :view_name",
                {"view_name": metric.source_view},
            )
        }
        needed = {metric.date_column, *metric.allowed_dims, *metric.allowed_filters}
        if metric.location_column:
            needed.add(metric.location_column)
        if metric.value_column:
            needed.add(metric.value_column)
        if metric.numerator_column:
            needed.add(metric.numerator_column)
        if metric.denominator_column:
            needed.add(metric.denominator_column)
        if any(name not in columns for name in needed):
            raise CatalogError("SOURCE_COLUMN_MISSING", "The adapter view is missing a registered column.")
        date_type = columns[metric.date_column]
        if date_type != "DATE" and not (date_type.startswith("TIMESTAMP") and "WITH TIME ZONE" not in date_type):
            raise CatalogError("INVALID_DATE_COLUMN", "The metric date column must be DATE or TIMESTAMP without time zone.")
        if metric.value_column and columns[metric.value_column] not in _NUMBER_TYPES:
            raise CatalogError("INVALID_VALUE_COLUMN", "SUM and AVG require an Oracle numeric value column.")
        if metric.numerator_column and columns[metric.numerator_column] not in _NUMBER_TYPES:
            raise CatalogError("INVALID_NUMERATOR_COLUMN", "The ratio numerator must be an Oracle numeric column.")
        if metric.denominator_column and columns[metric.denominator_column] not in _NUMBER_TYPES:
            raise CatalogError("INVALID_DENOMINATOR_COLUMN", "The ratio denominator must be an Oracle numeric column.")
        if metric.location_column and columns[metric.location_column] not in _TEXT_TYPES:
            raise CatalogError("INVALID_LOCATION_COLUMN", "The location key column must be an Oracle text column.")
        if any(columns[name] not in _TEXT_TYPES | _NUMBER_TYPES for name in (*metric.allowed_dims, *metric.allowed_filters)):
            raise CatalogError("INVALID_GROUPING_COLUMN", "Dimensions and filters must be text or numeric columns.")
        ddl_rows = self.database.run_readonly(
            "SELECT DBMS_METADATA.GET_DDL('VIEW', :view_name) AS SOURCE_DDL FROM DUAL",
            {"view_name": metric.source_view},
        )
        ddl = ddl_rows[0].get("source_ddl") if ddl_rows else None
        if hasattr(ddl, "read"):
            ddl = ddl.read()
        if not isinstance(ddl, str) or not ddl:
            raise CatalogError("SOURCE_VIEW_INVALID", "The complete adapter view DDL could not be read.")
        return ddl_at, hashlib.sha256(ddl.encode("utf-8")).hexdigest()

    def assert_certified_source(self, metric: RegisteredMetric) -> str:
        ddl_at, ddl_sha256 = self.validate_source(metric)
        if (metric.source_ddl_at is None or ddl_at != metric.source_ddl_at
                or metric.source_ddl_sha256 is None
                or ddl_sha256 != metric.source_ddl_sha256):
            raise CatalogError(
                "SOURCE_CHANGED",
                "The adapter view changed after review. Create and certify a new metric version.",
            )
        return ddl_sha256

    def _call_governance(self, procedure: str, arguments: list[Any], out_version: bool = False) -> int | None:
        conn = self.database._connect()
        try:
            with conn.cursor() as cursor:
                if out_version:
                    version_var = cursor.var(int)
                    arguments.append(version_var)
                cursor.callproc(f"WH_METRIC_GOVERNANCE.{procedure}", arguments)
                version = version_var.getvalue() if out_version else None
            conn.commit()
            return int(version[0] if isinstance(version, list) else version) if version is not None else None
        except Exception as exc:
            conn.rollback()
            raise CatalogError(
                "GOVERNANCE_REJECTED",
                "Oracle rejected the metric change. Check the draft, source view, and review state.",
            ) from exc
        finally:
            conn.close()

    def create_draft(self, manifest: Mapping[str, Any]) -> RegisteredMetric:
        self.require_catalog()
        metric = RegisteredMetric.from_manifest(manifest, version=1)
        version = self._call_governance(
            "CREATE_DRAFT",
            [
                metric.metric_id,
                metric.name,
                metric.owner,
                metric.definition_text,
                metric.unit,
                metric.source_view,
                metric.date_column,
                metric.location_column,
                metric.value_column,
                metric.numerator_column,
                metric.denominator_column,
                metric.aggregation,
                json.dumps(metric.allowed_dims),
                json.dumps(metric.allowed_filters),
            ],
            out_version=True,
        )
        saved = self.get_version(metric.metric_id, int(version))
        if saved is None:
            raise CatalogError("DRAFT_NOT_FOUND", "The saved draft could not be read back.")
        return saved

    def review_draft(self, metric_id: str, version: int, reviewer: str, note: str) -> RegisteredMetric:
        self.require_catalog()
        metric = self.get_version(metric_id, version)
        if metric is None or metric.status != "DRAFT":
            raise CatalogError("DRAFT_NOT_FOUND", "A draft with that ID and version does not exist.")
        _, ddl_sha256 = self.validate_source(metric)
        reviewer = _actor(reviewer, "Reviewer")
        if not note.strip() or len(note) > 16000 or any(ord(char) < 32 for char in note):
            raise CatalogError("INVALID_REVIEW_NOTE", "Review note must be 1–16,000 printable characters.")
        self._call_governance(
            "REVIEW_DRAFT", [metric.metric_id, metric.version, reviewer, note, ddl_sha256],
        )
        saved = self.get_version(metric.metric_id, metric.version)
        if saved is None:
            raise CatalogError("REVIEW_NOT_FOUND", "The reviewed draft could not be read back.")
        return saved

    def certify(self, metric_id: str, version: int, approver: str) -> RegisteredMetric:
        self.require_catalog()
        metric = self.get_version(metric_id, version)
        if metric is None or metric.status != "REVIEWED":
            raise CatalogError("REVIEW_REQUIRED", "The metric must be reviewed before certification.")
        ddl_sha256 = self.assert_certified_source(metric)
        approver = _actor(approver, "Approver")
        if metric.reviewed_by and approver.casefold() == metric.reviewed_by.casefold():
            raise CatalogError("SEPARATION_OF_DUTIES", "The approver must differ from the reviewer.")
        self._call_governance(
            "CERTIFY", [metric.metric_id, metric.version, approver, ddl_sha256],
        )
        saved = self.get_version(metric.metric_id, metric.version)
        if saved is None:
            raise CatalogError("CERTIFICATION_NOT_FOUND", "The certified metric could not be read back.")
        return saved

    def log_answer(
        self, *, metric: RegisteredMetric, query: Mapping[str, Any], compiled_sql: str,
        binds: Mapping[str, Any], row_count: int, asked_by: str | None,
        cache_status: str = "DISABLED", cache_key: str | None = None,
        result_generated_at: datetime | None = None,
    ) -> None:
        conn = self.database._connect()
        try:
            with conn.cursor() as cursor:
                values = {
                    "metric_id": metric.metric_id,
                    "metric_version": metric.version,
                    "asked_by": asked_by,
                    "query_json": json.dumps(query, default=_json_value),
                    "compiled_sql": compiled_sql,
                    "bind_json": json.dumps(binds, default=_json_value),
                    "row_count": row_count,
                }
                if cache_status in {"HIT", "MISS"}:
                    values.update({
                        "cache_status": cache_status,
                        "cache_key": cache_key,
                        "result_generated_at_utc": utc_timestamp_text(result_generated_at),
                    })
                    cursor.execute(
                        "INSERT INTO WH_REGISTERED_ANSWER_LOG "
                        "(ASKED_AT, METRIC_ID, METRIC_VERSION, ASKED_BY, QUERY_JSON, "
                        "COMPILED_SQL, BIND_JSON, ROW_COUNT, CACHE_STATUS, CACHE_KEY, RESULT_GENERATED_AT) "
                        "VALUES (SYSTIMESTAMP, :metric_id, :metric_version, :asked_by, :query_json, "
                        ":compiled_sql, :bind_json, :row_count, :cache_status, :cache_key, "
                        "FROM_TZ(TO_TIMESTAMP(:result_generated_at_utc, "
                        "'YYYY-MM-DD\"T\"HH24:MI:SS.FF6'), '+00:00'))",
                        values,
                    )
                else:
                    cursor.execute(
                        "INSERT INTO WH_REGISTERED_ANSWER_LOG "
                        "(ASKED_AT, METRIC_ID, METRIC_VERSION, ASKED_BY, QUERY_JSON, COMPILED_SQL, BIND_JSON, ROW_COUNT) "
                        "VALUES (SYSTIMESTAMP, :metric_id, :metric_version, :asked_by, :query_json, :compiled_sql, :bind_json, :row_count)",
                        values,
                    )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise DatabaseUnavailable("The registered metric answer could not be audited.") from exc
        finally:
            conn.close()

    def query(
        self, metric_id: str, start_date: date, end_date: date,
        dimensions: Sequence[str] = (), filters: Mapping[str, Any] | None = None,
        *, user_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(user_id, str) or not user_id.strip():
            raise CatalogError("AUTH_REQUIRED", "A verified application user is required for this metric query.")
        metric = self.get_certified(metric_id)
        if metric is None:
            raise CatalogError("METRIC_NOT_CERTIFIED", "No certified version of this metric exists.")
        if metric.location_column is None:
            raise CatalogError("LOCATION_SCOPE_REQUIRED", "This metric predates location security; certify a scoped version.")
        self.assert_certified_source(metric)
        normalized_dims = [name.upper() for name in dimensions]
        raw_filters = filters or {}
        normalized_filters = {str(name).upper(): value for name, value in raw_filters.items()}
        if len(normalized_filters) != len(raw_filters):
            raise CatalogError("DUPLICATE_FILTER", "A filter was repeated with different capitalization.")
        compiled = compile_registered(
            metric, start_date, end_date, normalized_dims, normalized_filters,
            scope_user_id=user_id,
        )
        cache_status = "DISABLED"
        cache_identity: CacheIdentity | None = None
        cached = None
        source_started_at: datetime | None = None
        if self.cache_enabled:
            try:
                policy = self.result_cache.policy_for(metric)
                if policy is not None:
                    cache_identity = build_identity(metric, compiled, user_id, policy)
                    cached = self.result_cache.get_hit(cache_identity)
                    cache_status = "HIT" if cached is not None else "MISS"
                    if cached is None:
                        source_started_at = self.result_cache.current_utc()
            except (DatabaseUnavailable, ValueError, KeyError, TypeError):
                # Cache metadata cannot authorize a result. The scoped SQL still can.
                cache_status = "BYPASS"
                cache_identity = None

        if cached is not None:
            rows = cached.rows
            generated_at = cached.generated_at
        else:
            raw_rows = self.database.run_readonly(
                compiled.sql, compiled.binds, client_identifier=user_id,
            )
            if len(raw_rows) > MAX_ANSWER_ROWS:
                raise CatalogError(
                    "RESULT_TOO_LARGE",
                    f"The certified result exceeds {MAX_ANSWER_ROWS} rows. Narrow the approved grouping or period.",
                )
            rows = [
                {str(key).lower(): _json_value(value) for key, value in row.items()}
                for row in raw_rows
            ]
            generated_at = source_started_at or datetime.now(timezone.utc)
        if not rows or any(row.get("value") is None for row in rows):
            raise CatalogError("NO_DATA", "The adapter view has no matching data for that period and selection.")
        request = {
            "metric_id": metric.metric_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "dimensions": normalized_dims,
            "filters": normalized_filters,
            "asked_by": user_id,
        }
        self.log_answer(
            metric=metric, query=request, compiled_sql=compiled.sql,
            binds=compiled.binds, row_count=len(rows), asked_by=user_id,
            cache_status=cache_status,
            cache_key=cache_identity.key if cache_identity is not None else None,
            result_generated_at=generated_at,
        )
        if cache_status == "MISS" and cache_identity is not None:
            try:
                self.result_cache.put(cache_identity, rows, generated_at)
            except Exception:
                # The answer has already been audited; cache availability is optional.
                pass
        return {
            "tier": "CERTIFIED",
            "metric": metric.public_dict(),
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "columns": list(compiled.columns),
            "rows": rows,
            "row_count": len(rows),
            "sql": compiled.sql,
            "binds": {key: _json_value(value) for key, value in compiled.binds.items()},
            "access": {"user_id": user_id, "location_scoped": True},
            "cache": {
                "status": cache_status,
                "generated_at": generated_at.isoformat(),
                "ttl_seconds": cache_identity.policy.ttl_seconds if cache_identity is not None else None,
            },
            "coverage_note": (
                "Results are limited to your active location grants. "
                "The adapter view owner is responsible for source completeness."
            ),
        }
