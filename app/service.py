"""Certified answer workflow and fixed, illustrative demo comparison."""

from __future__ import annotations

import difflib
import os
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from app.db import DatabaseUnavailable, OracleRepository
from app.intent import Intent, ParseResult, parse_question
from app.metrics import CompileError, MetricDefinition, compile_certified


DEMO_COMPARISON_SQL = """
WITH monthly_hc AS (
  SELECT h.month_start, SUM(h.headcount) AS headcount
  FROM WH_HEADCOUNT_MONTH h JOIN WH_LOCATION l ON l.location_id = h.location_id
  WHERE l.state = :state AND h.month_start >= :start_date AND h.month_start < :end_date
  GROUP BY h.month_start
), denom AS (
  SELECT AVG(headcount) AS avg_headcount, COUNT(*) AS months_present FROM monthly_hc
), terms AS (
  SELECT COUNT(*) AS all_separations,
         SUM(CASE WHEN t.term_type <> 'INTERNAL_TRANSFER' THEN 1 ELSE 0 END) AS certified_separations,
         SUM(CASE WHEN t.term_type = 'INTERNAL_TRANSFER' THEN 1 ELSE 0 END) AS internal_transfers
  FROM WH_TERMINATION t JOIN WH_LOCATION l ON l.location_id = t.location_id
  WHERE l.state = :state AND t.term_date >= :start_date AND t.term_date < :end_date
)
SELECT d.avg_headcount, t.all_separations, t.certified_separations, t.internal_transfers,
       ROUND(100 * t.all_separations / d.avg_headcount, 4) AS ad_hoc_value
FROM denom d CROSS JOIN terms t
WHERE d.months_present = 3 AND d.avg_headcount > 0
""".strip()


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _month_starts(start: date, end: date) -> List[date]:
    values: List[date] = []
    cursor = start
    while cursor < end:
        values.append(cursor)
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return values


def _nearest_metrics(question: str, rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, str]]:
    query_tokens = set(re.findall(r"[a-z]+", question.lower()))
    ranked = []
    for row in rows:
        metric = MetricDefinition.from_row(row)
        body = (metric.name + " " + metric.definition).lower()
        body_tokens = set(re.findall(r"[a-z]+", body))
        overlap = len(query_tokens & body_tokens)
        ratio = difflib.SequenceMatcher(None, question.lower(), metric.name.lower()).ratio()
        ranked.append((overlap + ratio, metric))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [
        {"id": metric.metric_id, "name": metric.name, "definition": metric.definition}
        for _, metric in ranked[:3]
    ]


class WarehouseService:
    def __init__(self, repository: OracleRepository, today: Optional[date] = None):
        self.repository = repository
        self.today = today or date.today()

    @classmethod
    def from_environment(cls) -> "WarehouseService":
        as_of = os.getenv("DEMO_AS_OF_DATE", "").strip()
        try:
            today = date.fromisoformat(as_of) if as_of else date.today()
        except ValueError as exc:
            raise ValueError("DEMO_AS_OF_DATE must be YYYY-MM-DD.") from exc
        return cls(OracleRepository(), today=today)

    def _refusal(
        self,
        question: str,
        reason_code: str,
        message: str,
        unmapped_terms: Sequence[str] = (),
    ) -> Dict[str, Any]:
        nearest = _nearest_metrics(question, self.repository.list_certified_metrics())
        return {
            "tier": "EXPLORATORY",
            "question": question,
            "message": message,
            "reason_code": reason_code,
            "unmapped_terms": list(unmapped_terms),
            "nearest_metrics": nearest,
            "exploratory_answer": None,
        }

    def _check_coverage(self, intent: Intent) -> Optional[str]:
        current_month = date(self.today.year, self.today.month, 1)
        if intent.period.end > current_month:
            return "The reporting period includes an unfinished month. Ask for a completed period."
        available = set(self.repository.list_available_months())
        expected = _month_starts(intent.period.start, intent.period.end)
        if not expected or any(month not in available for month in expected):
            return "A complete monthly snapshot is unavailable for this period."
        return None

    @staticmethod
    def _summary(metric: MetricDefinition, intent: Intent, rows: Sequence[Mapping[str, Any]]) -> str:
        label = intent.period.label
        if len(rows) != 1 or intent.dimensions:
            return "%s: %d result rows for %s." % (metric.name, len(rows), label)
        value = rows[0]["value"]
        unit = metric.public_dict()["unit"]
        if unit == "percent":
            formatted = "%.2f%%" % float(value)
        elif unit == "days":
            formatted = "%.1f days" % float(value)
        else:
            formatted = "%s people" % format(int(value), ",")
        filters = []
        if "state" in intent.filters:
            filters.append(str(intent.filters["state"]))
        if "location_id" in intent.filters:
            filters.append("location %s" % intent.filters["location_id"])
        if "job_family" in intent.filters:
            filters.append(str(intent.filters["job_family"]).replace("_", " ").title())
        scope = " for " + ", ".join(filters) if filters else ""
        return "%s%s, %s: %s." % (metric.name, scope, label, formatted)

    def ask(self, question: str) -> Dict[str, Any]:
        locations = self.repository.list_locations()
        job_families = self.repository.list_job_families()
        parsed = parse_question(question, locations=locations, job_families=job_families, today=self.today)
        if parsed.intent is None:
            return self._refusal(
                question,
                parsed.reason_code or "UNSUPPORTED_INTENT",
                parsed.message or "The question does not map to a certified metric.",
                parsed.unmapped_terms,
            )
        intent = parsed.intent
        row = self.repository.get_metric(intent.metric_id)
        if not row:
            return self._refusal(question, "UNSUPPORTED_METRIC", "No certified catalog entry exists for this metric.")
        metric = MetricDefinition.from_row(row)
        try:
            compiled = compile_certified(intent, metric)
        except CompileError as exc:
            return self._refusal(question, exc.code, str(exc))
        coverage_error = self._check_coverage(intent)
        if coverage_error:
            return self._refusal(question, "DATA_COVERAGE", coverage_error)

        raw_rows = self.repository.run_readonly(compiled.sql, compiled.binds)
        rows = [
            {str(key).lower(): _json_value(value) for key, value in row.items()}
            for row in raw_rows
        ]
        if not rows or any(row.get("value") is None for row in rows):
            return self._refusal(question, "NO_DATA", "No complete certified result exists for this selection.")
        self.repository.log_answer(
            question=question,
            tier="CERTIFIED",
            metric_id=metric.metric_id,
            metric_version=metric.version,
            compiled_sql=compiled.sql,
            binds=compiled.binds,
            row_count=len(rows),
        )
        warnings = []
        if "(default)" in intent.period.label:
            warnings.append("Period defaulted to the latest completed reporting period.")
        return {
            "tier": "CERTIFIED",
            "question": question,
            "metric": metric.public_dict(),
            "intent": intent.as_dict(),
            "columns": list(compiled.columns),
            "rows": rows,
            "row_count": len(rows),
            "summary": self._summary(metric, intent, rows),
            "sql": compiled.sql,
            "binds": {key: _json_value(value) for key, value in compiled.binds.items()},
            "freshness": "Reporting period through %s; ingestion time unavailable" % (intent.period.end - timedelta(days=1)).isoformat(),
            "warnings": warnings,
        }

    def certification_request(self, question: str) -> Dict[str, Any]:
        if not question.strip() or len(question) > 1000:
            raise ValueError("The request must contain a question under 1,000 characters.")
        request_id = self.repository.create_request(question.strip())
        return {"request_id": request_id, "status": "OPEN"}

    def demo_turnover_comparison(self) -> Dict[str, Any]:
        question = "What was turnover in Florida Q3 2026?"
        certified_answer = self.ask(question)
        certified_rows = certified_answer.get("rows", [])
        if (
            certified_answer.get("tier") != "CERTIFIED"
            or len(certified_rows) != 1
            or certified_rows[0].get("value") is None
        ):
            raise DatabaseUnavailable("The fixed turnover demo has no certified result.")
        binds = {"state": "FL", "start_date": date(2026, 7, 1), "end_date": date(2026, 10, 1)}
        rows = self.repository.run_readonly(DEMO_COMPARISON_SQL, binds)
        if len(rows) != 1:
            raise DatabaseUnavailable("The fixed turnover comparison has incomplete source data.")
        row = {key: _json_value(value) for key, value in rows[0].items()}
        certified_value = float(certified_rows[0]["value"])
        expected_certified = round(100 * float(row["certified_separations"]) / float(row["avg_headcount"]), 4)
        if abs(certified_value - expected_certified) > 0.0001:
            raise DatabaseUnavailable("The comparison did not match the approved turnover definition.")
        return {
            "state": "FL",
            "period": {"start": "2026-07-01", "end": "2026-10-01", "label": "Q3 2026"},
            "certified": {"separations": row["certified_separations"], "value": certified_value},
            "ad_hoc": {"separations": row["all_separations"], "value": row["ad_hoc_value"]},
            "internal_transfers": row["internal_transfers"],
            "avg_headcount": row["avg_headcount"],
            "definition_note": "The illustrative ad hoc calculation includes internal transfers; the certified turnover metric excludes them. Both use the same average monthly headcount denominator.",
        }
