"""Optional Oracle-backed cache for *scoped, certified* metric results.

Natural-language wording never enters a cache key. Only an already validated
metric, its fixed compiled SQL, typed binds, authenticated user and current
policy/access/data revisions can identify a result. A hit rechecks those
revisions and the user's active grants in one Oracle SELECT statement.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Sequence

from app.db import OracleRepository
from app.metrics import CompiledQuery
from app.registered_metrics import MAX_ANSWER_ROWS, RegisteredMetric


CACHE_FORMAT_VERSION = 1
MAX_CACHE_ROWS = MAX_ANSWER_ROWS
MAX_CACHE_RESULT_BYTES = 262_144
MAX_CANONICAL_BYTES = 32_768
_UTC_TIMESTAMP_SQL = (
    "FROM_TZ(TO_TIMESTAMP(:generated_at_utc, "
    "'YYYY-MM-DD\"T\"HH24:MI:SS.FF6'), '+00:00')"
)
_REVISION_TRIGGERS_READY = (
    "(SELECT COUNT(*) FROM USER_TRIGGERS trigger_state "
    "WHERE trigger_state.TRIGGER_NAME IN "
    "('WH_CACHE_USER_REV_TRG', 'WH_CACHE_LOCATION_REV_TRG', "
    "'WH_CACHE_GRANT_REV_TRG', 'WH_CACHE_POLICY_REV_TRG', "
    "'WH_CACHE_SOURCE_REV_TRG') AND trigger_state.STATUS = 'ENABLED') = 5"
)


@dataclass(frozen=True)
class CachePolicy:
    ttl_seconds: int
    access_revision: int
    data_revision: int
    policy_revision: int


@dataclass(frozen=True)
class CacheIdentity:
    key: str
    canonical_json: str
    user_id: str
    metric_id: str
    metric_version: int
    source_view: str
    source_ddl_sha256: str
    policy: CachePolicy
    columns: tuple[str, ...]


@dataclass(frozen=True)
class CacheHit:
    rows: list[dict[str, Any]]
    generated_at: datetime


def _typed(value: Any) -> dict[str, Any]:
    """Keep Oracle bind types distinct in the canonical request."""

    if type(value) is str:
        return {"type": "string", "value": value}
    if type(value) is int:
        return {"type": "integer", "value": str(value)}
    if type(value) is float and math.isfinite(value):
        return {"type": "float", "value": value.hex()}
    if isinstance(value, Decimal) and value.is_finite():
        return {"type": "decimal", "value": str(value)}
    if type(value) is date:
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, datetime):
        return {"type": "datetime", "value": value.isoformat()}
    raise ValueError("A cache key contained an unsupported bind type.")


def utc_timestamp_text(value: datetime) -> str:
    """Bind a UTC wall-clock string so Oracle never applies a session offset."""

    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("A timezone-aware cache timestamp is required.")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")


def _reject_json_constant(_value: str) -> None:
    raise ValueError("Non-finite JSON numbers cannot be cached.")


def _normalize_json(value: Any, depth: int = 0) -> Any:
    """Validate native Oracle JSON and make its numeric values API-safe."""

    if depth > 32:
        raise ValueError("Cached JSON is too deeply nested.")
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Cached JSON contains a non-finite number.")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Cached JSON contains a non-finite number.")
        if value == value.to_integral_value():
            return int(value)
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Cached JSON contains a number outside the supported range.")
        return number
    if isinstance(value, list):
        return [_normalize_json(item, depth + 1) for item in value]
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise ValueError("Cached JSON has a non-string object key.")
        return {key: _normalize_json(item, depth + 1) for key, item in value.items()}
    raise ValueError("Cached JSON contains an unsupported value.")


def _load_json(value: Any, max_bytes: int) -> Any:
    """Accept Oracle's native dict/list output or text/CLOB JSON output."""

    if hasattr(value, "read"):
        value = value.read()
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > max_bytes:
            raise ValueError("Cached JSON exceeds the size limit.")
        value = json.loads(value, parse_constant=_reject_json_constant)
    normalized = _normalize_json(value)
    rendered = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    if len(rendered.encode("utf-8")) > max_bytes:
        raise ValueError("Cached JSON exceeds the size limit.")
    return normalized


def _same_json(left: Any, right: Any) -> bool:
    """Compare parsed canonical content without treating booleans as integers."""

    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_same_json(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_same_json(a, b) for a, b in zip(left, right))
    if type(left) is bool or type(right) is bool:
        return type(left) is type(right) and left == right
    if type(left) in {int, float} and type(right) in {int, float}:
        return Decimal(str(left)) == Decimal(str(right))
    return type(left) is type(right) and left == right


def build_identity(
    metric: RegisteredMetric,
    compiled: CompiledQuery,
    user_id: str,
    policy: CachePolicy,
) -> CacheIdentity:
    """Hash the certified request shape; no raw question or token is included."""

    if metric.source_ddl_sha256 is None or metric.location_column is None:
        raise ValueError("Only scoped, fingerprinted metrics may be cached.")
    if compiled.binds.get("auth_user_id") != user_id:
        raise ValueError("Cache identity must match the authenticated SQL bind.")
    payload = {
        "cache_format_version": CACHE_FORMAT_VERSION,
        "metric_id": metric.metric_id,
        "metric_version": metric.version,
        "source_view": metric.source_view,
        "source_ddl_sha256": metric.source_ddl_sha256,
        "user_id": user_id,
        "access_revision": policy.access_revision,
        "data_revision": policy.data_revision,
        "policy_revision": policy.policy_revision,
        "ttl_seconds": policy.ttl_seconds,
        "columns": list(compiled.columns),
        "compiled_sql": compiled.sql,
        "typed_binds": {name: _typed(value) for name, value in sorted(compiled.binds.items())},
    }
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    if len(canonical_json.encode("utf-8")) > MAX_CANONICAL_BYTES:
        raise ValueError("The cache request exceeds the safe size limit.")
    return CacheIdentity(
        key=hashlib.sha256(canonical_json.encode("utf-8")).hexdigest(),
        canonical_json=canonical_json,
        user_id=user_id,
        metric_id=metric.metric_id,
        metric_version=metric.version,
        source_view=metric.source_view,
        source_ddl_sha256=metric.source_ddl_sha256,
        policy=policy,
        columns=tuple(compiled.columns),
    )


class ResultCacheRepository:
    def __init__(self, database: OracleRepository):
        self.database = database

    def current_utc(self) -> datetime:
        """Read the database clock before a cacheable source query begins."""

        rows = self.database.run_readonly(
            "SELECT SYS_EXTRACT_UTC(SYSTIMESTAMP) AS NOW_UTC FROM DUAL", {},
        )
        if len(rows) != 1 or not isinstance(rows[0].get("now_utc"), datetime):
            raise ValueError("Oracle did not provide a cache timestamp.")
        now = rows[0]["now_utc"]
        return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)

    def policy_for(self, metric: RegisteredMetric) -> CachePolicy | None:
        """No policy row, disabled policy, or missing source revision means no cache."""

        rows = self.database.run_readonly(
            "SELECT p.TTL_SECONDS, p.POLICY_REVISION, state.ACCESS_REVISION, "
            "source.DATA_REVISION FROM WH_CACHE_POLICY p "
            "JOIN WH_REGISTERED_METRIC metric "
            "ON metric.METRIC_ID = p.METRIC_ID AND metric.VERSION = p.METRIC_VERSION "
            "JOIN WH_CACHE_SOURCE source ON source.SOURCE_VIEW = metric.SOURCE_VIEW "
            "CROSS JOIN WH_CACHE_STATE state "
            "WHERE p.METRIC_ID = :metric_id AND p.METRIC_VERSION = :metric_version "
            "AND p.ENABLED = 'Y' AND metric.STATUS = 'CERTIFIED' "
            "AND metric.SOURCE_VIEW = :source_view "
            "AND metric.SOURCE_DDL_SHA256 = :source_ddl_sha256 "
            "AND state.STATE_ID = 1 AND " + _REVISION_TRIGGERS_READY,
            {
                "metric_id": metric.metric_id,
                "metric_version": metric.version,
                "source_view": metric.source_view,
                "source_ddl_sha256": metric.source_ddl_sha256,
            },
        )
        if not rows:
            return None
        row = rows[0]
        policy = CachePolicy(
            ttl_seconds=int(row["ttl_seconds"]),
            access_revision=int(row["access_revision"]),
            data_revision=int(row["data_revision"]),
            policy_revision=int(row["policy_revision"]),
        )
        if not (1 <= policy.ttl_seconds <= 86400 and policy.access_revision >= 0
                and policy.data_revision >= 1 and policy.policy_revision >= 1):
            raise ValueError("Cache policy is invalid.")
        return policy

    def get_hit(self, identity: CacheIdentity) -> CacheHit | None:
        """Recheck current access, policy, source revision and certification in one SELECT."""

        rows = self.database.run_readonly(
            "SELECT cache.RESULT_JSON, cache.ROW_COUNT, "
            "SYS_EXTRACT_UTC(cache.GENERATED_AT) AS GENERATED_AT_UTC, "
            "cache.CANONICAL_JSON FROM WH_QUERY_CACHE cache "
            "JOIN WH_APP_USER usr ON usr.USER_ID = cache.USER_ID AND usr.STATUS = 'ACTIVE' "
            "JOIN WH_CACHE_STATE state ON state.STATE_ID = 1 "
            "AND state.ACCESS_REVISION = cache.ACCESS_REVISION "
            "JOIN WH_CACHE_POLICY policy ON policy.METRIC_ID = cache.METRIC_ID "
            "AND policy.METRIC_VERSION = cache.METRIC_VERSION "
            "AND policy.ENABLED = 'Y' AND policy.POLICY_REVISION = cache.POLICY_REVISION "
            "JOIN WH_CACHE_SOURCE source ON source.SOURCE_VIEW = cache.SOURCE_VIEW "
            "AND source.DATA_REVISION = cache.DATA_REVISION "
            "JOIN WH_REGISTERED_METRIC metric ON metric.METRIC_ID = cache.METRIC_ID "
            "AND metric.VERSION = cache.METRIC_VERSION AND metric.STATUS = 'CERTIFIED' "
            "AND metric.SOURCE_VIEW = cache.SOURCE_VIEW "
            "AND metric.SOURCE_DDL_SHA256 = cache.SOURCE_DDL_SHA256 "
            "JOIN USER_OBJECTS obj ON obj.OBJECT_NAME = cache.SOURCE_VIEW "
            "AND obj.OBJECT_TYPE = 'VIEW' AND obj.STATUS = 'VALID' "
            "AND obj.LAST_DDL_TIME = metric.SOURCE_DDL_AT "
            "WHERE cache.CACHE_KEY = :cache_key AND cache.USER_ID = :auth_user_id "
            "AND cache.EXPIRES_AT > SYSTIMESTAMP "
            "AND EXISTS (SELECT 1 FROM WH_USER_LOCATION acl "
            "JOIN WH_SEC_LOCATION loc ON loc.LOCATION_KEY = acl.LOCATION_KEY "
            "WHERE acl.USER_ID = usr.USER_ID AND acl.STATUS = 'ACTIVE' "
            "AND loc.STATUS = 'ACTIVE') AND " + _REVISION_TRIGGERS_READY,
            {"cache_key": identity.key, "auth_user_id": identity.user_id},
            client_identifier=identity.user_id,
        )
        if not rows:
            return None
        row = rows[0]
        try:
            canonical = _load_json(row["canonical_json"], MAX_CANONICAL_BYTES)
            expected = json.loads(identity.canonical_json, parse_constant=_reject_json_constant)
            if not isinstance(canonical, dict) or not _same_json(canonical, expected):
                return None
            parsed = _load_json(row["result_json"], MAX_CACHE_RESULT_BYTES)
            generated_at_utc = row["generated_at_utc"]
            row_count = row["row_count"]
        except (KeyError, TypeError, ValueError, UnicodeError, RecursionError):
            return None
        if not isinstance(generated_at_utc, datetime) or type(row_count) is bool:
            return None
        generated_at = (
            generated_at_utc.replace(tzinfo=timezone.utc)
            if generated_at_utc.tzinfo is None
            else generated_at_utc.astimezone(timezone.utc)
        )
        try:
            exact_row_count = int(row_count)
        except (TypeError, ValueError, OverflowError):
            return None
        if (not isinstance(parsed, list) or not 1 <= len(parsed) <= MAX_CACHE_ROWS
                or len(parsed) != exact_row_count or exact_row_count != row_count
                or any(not isinstance(item, dict) for item in parsed)):
            return None
        if any(set(item) != set(identity.columns) for item in parsed):
            return None
        return CacheHit(rows=parsed, generated_at=generated_at)

    def put(
        self, identity: CacheIdentity, rows: Sequence[Mapping[str, Any]], generated_at: datetime,
    ) -> bool:
        """Store expiry from source-query start, never from a later cache-write time."""

        if not 1 <= len(rows) <= MAX_CACHE_ROWS:
            return False
        try:
            generated_at_utc = utc_timestamp_text(generated_at)
        except ValueError:
            return False
        try:
            result_json = json.dumps(list(rows), separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            return False
        if len(result_json.encode("utf-8")) > MAX_CACHE_RESULT_BYTES:
            return False
        try:
            conn = self.database._connect()
        except Exception:
            return False
        binds = {
            "cache_key": identity.key,
            "user_id": identity.user_id,
            "metric_id": identity.metric_id,
            "metric_version": identity.metric_version,
            "source_view": identity.source_view,
            "source_ddl_sha256": identity.source_ddl_sha256,
            "access_revision": identity.policy.access_revision,
            "data_revision": identity.policy.data_revision,
            "policy_revision": identity.policy.policy_revision,
            "canonical_json": identity.canonical_json,
            "result_json": result_json,
            "row_count": len(rows),
            "generated_at_utc": generated_at_utc,
            "ttl_seconds": identity.policy.ttl_seconds,
        }
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE WH_QUERY_CACHE SET USER_ID = :user_id, METRIC_ID = :metric_id, "
                    "METRIC_VERSION = :metric_version, SOURCE_VIEW = :source_view, "
                    "SOURCE_DDL_SHA256 = :source_ddl_sha256, "
                    "ACCESS_REVISION = :access_revision, DATA_REVISION = :data_revision, "
                    "POLICY_REVISION = :policy_revision, CANONICAL_JSON = :canonical_json, "
                    "RESULT_JSON = :result_json, ROW_COUNT = :row_count, "
                    f"GENERATED_AT = {_UTC_TIMESTAMP_SQL}, "
                    f"EXPIRES_AT = {_UTC_TIMESTAMP_SQL} + NUMTODSINTERVAL(:ttl_seconds, 'SECOND') "
                    "WHERE CACHE_KEY = :cache_key",
                    binds,
                )
                if cursor.rowcount == 0:
                    cursor.execute(
                        "INSERT INTO WH_QUERY_CACHE (CACHE_KEY, USER_ID, METRIC_ID, "
                        "METRIC_VERSION, SOURCE_VIEW, SOURCE_DDL_SHA256, "
                        "ACCESS_REVISION, DATA_REVISION, POLICY_REVISION, "
                        "CANONICAL_JSON, RESULT_JSON, ROW_COUNT, GENERATED_AT, EXPIRES_AT) "
                        "VALUES (:cache_key, :user_id, :metric_id, :metric_version, "
                        ":source_view, :source_ddl_sha256, :access_revision, :data_revision, "
                        ":policy_revision, :canonical_json, :result_json, :row_count, "
                        f"{_UTC_TIMESTAMP_SQL}, "
                        f"{_UTC_TIMESTAMP_SQL} + NUMTODSINTERVAL(:ttl_seconds, 'SECOND'))",
                        binds,
                    )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()
