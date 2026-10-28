"""Oracle adapter. Certified reads and audit writes use separate transactions."""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence


class DatabaseUnavailable(RuntimeError):
    pass


class OracleRepository:
    def __init__(self, user: Optional[str] = None, password: Optional[str] = None, dsn: Optional[str] = None):
        self.user = user if user is not None else os.getenv("ORACLE_USER", "")
        self.password = password if password is not None else os.getenv("ORACLE_PASSWORD", "")
        self.dsn = dsn if dsn is not None else os.getenv("ORACLE_DSN", "")

    @property
    def configured(self) -> bool:
        return bool(self.user and self.password and self.dsn)

    def _connect(self):
        if not self.configured:
            raise DatabaseUnavailable(
                "Oracle credentials are not configured. Set ORACLE_USER, ORACLE_PASSWORD, and ORACLE_DSN."
            )
        try:
            import oracledb  # Imported here so parser/compiler tests work without Oracle packages.
        except ImportError as exc:
            raise DatabaseUnavailable("Install dependencies from requirements.txt to enable Oracle access.") from exc
        try:
            return oracledb.connect(user=self.user, password=self.password, dsn=self.dsn)
        except Exception as exc:
            # Oracle driver errors may contain connection details; do not return them to callers.
            raise DatabaseUnavailable("Oracle connection is unavailable. Check the local environment configuration.") from exc

    @staticmethod
    def _materialize(value: Any) -> Any:
        if hasattr(value, "read"):
            return value.read()
        return value

    @classmethod
    def _rows(cls, cursor) -> List[Dict[str, Any]]:
        columns = [str(item[0]).lower() for item in cursor.description]
        return [
            {name: cls._materialize(value) for name, value in zip(columns, row)}
            for row in cursor.fetchall()
        ]

    def _select(self, sql: str, binds: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        if not sql.lstrip().upper().startswith(("SELECT ", "WITH ")) or ";" in sql:
            raise ValueError("Only one SELECT statement may be executed by the read adapter.")
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute(sql, dict(binds or {}))
                rows = self._rows(cursor)
            conn.rollback()
            return rows
        except Exception as exc:
            conn.rollback()
            raise DatabaseUnavailable("Oracle read failed. Check schema deployment and connection permissions.") from exc
        finally:
            conn.close()

    def health(self) -> bool:
        return bool(self._select("SELECT 1 AS ready FROM dual"))

    def list_locations(self) -> List[Dict[str, Any]]:
        return self._select("SELECT location_id, name, city, state FROM WH_LOCATION ORDER BY location_id")

    def list_job_families(self) -> List[str]:
        rows = self._select("SELECT DISTINCT job_family FROM WH_HEADCOUNT_MONTH ORDER BY job_family")
        return [str(row["job_family"]) for row in rows]

    def list_available_months(self) -> List[date]:
        rows = self._select("SELECT DISTINCT month_start FROM WH_HEADCOUNT_MONTH ORDER BY month_start")
        values: List[date] = []
        for row in rows:
            value = row["month_start"]
            values.append(value.date() if isinstance(value, datetime) else value)
        return values

    def get_metric(self, metric_id: str) -> Optional[Dict[str, Any]]:
        rows = self._select(
            "SELECT metric_id, name, version, owner, status, definition_text, sql_template, "
            "allowed_dims, allowed_filters, grain FROM WH_METRIC WHERE metric_id = :metric_id",
            {"metric_id": metric_id},
        )
        return rows[0] if rows else None

    def list_certified_metrics(self) -> List[Dict[str, Any]]:
        return self._select(
            "SELECT metric_id, name, version, owner, status, definition_text, sql_template, "
            "allowed_dims, allowed_filters, grain FROM WH_METRIC "
            "WHERE status = 'CERTIFIED' ORDER BY name"
        )

    def run_readonly(self, sql: str, binds: Mapping[str, Any]) -> List[Dict[str, Any]]:
        return self._select(sql, binds)

    def log_answer(
        self,
        *,
        question: str,
        tier: str,
        metric_id: str,
        metric_version: int,
        compiled_sql: str,
        binds: Mapping[str, Any],
        row_count: int,
    ) -> None:
        bind_json = json.dumps(binds, default=lambda value: value.isoformat() if isinstance(value, (date, datetime)) else str(value))
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO WH_ANSWER_LOG "
                    "(asked_at, question, tier, metric_id, metric_version, compiled_sql, bind_json, row_count) "
                    "VALUES (SYSTIMESTAMP, :question, :tier, :metric_id, :metric_version, :compiled_sql, :bind_json, :row_count)",
                    {
                        "question": question,
                        "tier": tier,
                        "metric_id": metric_id,
                        "metric_version": metric_version,
                        "compiled_sql": compiled_sql,
                        "bind_json": bind_json,
                        "row_count": row_count,
                    },
                )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise DatabaseUnavailable("The certified answer could not be audited.") from exc
        finally:
            conn.close()

    def create_request(self, question: str) -> int:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                request_id = cursor.var(int)
                cursor.execute(
                    "INSERT INTO WH_METRIC_REQUEST (asked_at, question, exploratory_sql, status) "
                    "VALUES (SYSTIMESTAMP, :question, NULL, 'OPEN') RETURNING request_id INTO :request_id",
                    {"question": question, "request_id": request_id},
                )
                value = request_id.getvalue()
            conn.commit()
            return int(value[0] if isinstance(value, list) else value)
        except Exception as exc:
            conn.rollback()
            raise DatabaseUnavailable("The certification request could not be saved.") from exc
        finally:
            conn.close()
